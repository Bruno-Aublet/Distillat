"""Client Gemini : comptage de tokens, résumé/personnages/analyse, sans retry."""
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app import config
from app import i18n
from app.__version__ import VERSION
from app.book_report import BookReport, Character
from app.epub_parser import Chapter, BookContent
from app.i18n import tr
from app.prompts_store import load_custom_prompts
from app.quota_tracker import QuotaSnapshot, QuotaTracker

# gemini-2.5-flash a été retiré pour les nouvelles clés API en juillet 2026,
# avant sa date de dépréciation officielle annoncée. gemini-3.5-flash est le
# modèle Flash generally available (non-preview) qui lui succède.
MODEL_NAME = "gemini-3.5-flash"


@dataclass(frozen=True)
class ModelInfo:
    """Caractéristiques d'un modèle Gemini proposé au choix par profil (voir
    app/config.py, champ "model" du profil). max_input_tokens est la fenêtre
    de contexte du modèle (marge de sécurité sous la vraie limite, pour
    laisser de la place au prompt système et à la réponse) ; c'est
    max_tokens_per_request, et non max_input_tokens, qui doit dimensionner une
    requête envoyée en une fois : une requête de plusieurs centaines de
    milliers de tokens tient dans la fenêtre de contexte mais sature le TPM à
    elle seule et déclenche une erreur 429, même si c'est la toute première
    requête envoyée. max_tokens_per_request garde une marge sous la vraie
    limite TPM constatée (voir aistudio.google.com/rate-limit) pour absorber
    l'écart entre le tokenizer local (count_tokens) et le décompte serveur
    exact."""

    name: str
    max_input_tokens: int
    max_tokens_per_request: int


# gemini-3.5-flash et gemini-3.6-flash partagent actuellement les mêmes
# caractéristiques (fenêtre de contexte 1 048 576 tokens officielle, TPM
# gratuit 250 000 constaté au 19/07/2026 pour les deux modèles sur
# aistudio.google.com/rate-limit) : les valeurs ci-dessous sont donc
# volontairement identiques pour les deux entrées, pas par oubli. À revoir si
# un futur modèle aux caractéristiques différentes est ajouté à cette liste.
AVAILABLE_MODELS: list[ModelInfo] = [
    ModelInfo(name="gemini-3.5-flash", max_input_tokens=900_000, max_tokens_per_request=200_000),
    ModelInfo(name="gemini-3.6-flash", max_input_tokens=900_000, max_tokens_per_request=200_000),
]


def get_model_info(model_name: str) -> ModelInfo:
    """Résout un nom de modèle vers ses caractéristiques. Repli sur le premier
    modèle de AVAILABLE_MODELS si le nom ne correspond à aucune entrée connue
    (ex. modèle stocké dans un vieux profil, retiré de la liste depuis)."""
    for model_info in AVAILABLE_MODELS:
        if model_info.name == model_name:
            return model_info
    return AVAILABLE_MODELS[0]

ProgressCallback = Callable[[int, int, str], None]
QuotaCallback = Callable[[QuotaSnapshot], None]


class GeminiError(Exception):
    """error_kind identifie la nature de l'erreur indépendamment de la langue
    du message ("daily_quota", "rate_quota", ou None pour tout autre cas) :
    l'appelant (main_window) s'appuie dessus pour adapter son comportement
    (ex : proposer une reprise), sans jamais chercher un mot-clé dans le
    message traduit, ce qui casserait selon la langue de l'UI."""

    def __init__(self, message: str, error_kind: str | None = None) -> None:
        super().__init__(message)
        self.error_kind = error_kind


class PartialGenerationError(GeminiError):
    """Levée quand un livre découpé en lots échoue après que d'autres lots ont
    déjà été résumés avec succès : porte ce travail déjà accompli pour que
    l'appelant puisse le sauvegarder et proposer une reprise plutôt que de
    tout perdre."""

    def __init__(
        self,
        message: str,
        chapter_summaries: list[tuple[str, str]],
        batches_done: int,
        batches_total: int,
        error_kind: str | None = None,
    ) -> None:
        super().__init__(message, error_kind=error_kind)
        self.chapter_summaries = chapter_summaries
        self.batches_done = batches_done
        self.batches_total = batches_total


@dataclass
class QuotaBlockedInfo:
    """Détail extrait d'une erreur 429 (quota dépassé), quand disponible."""

    quota_id: str | None
    retry_after_seconds: float | None


def _find_error_detail_block(details: object, type_suffix: str) -> dict | None:
    """Le corps JSON brut d'une erreur API (exc.details, un dict) place les
    informations structurées (quota dépassé, délai de nouvelle tentative...)
    dans error.details, une liste de blocs identifiés par leur clé "@type"
    (ex. "type.googleapis.com/google.rpc.QuotaFailure"), pas par un type
    Python dédié comme avec l'ancien SDK (google-generativeai) : ce
    changement de forme (attributs protobuf -> dict JSON générique) est la
    principale différence à gérer lors de la migration vers google-genai."""
    if not isinstance(details, dict):
        return None
    error = details.get("error")
    if not isinstance(error, dict):
        return None
    for block in error.get("details", []):
        if isinstance(block, dict) and str(block.get("@type", "")).endswith(type_suffix):
            return block
    return None


def _extract_quota_blocked_info(exc: genai_errors.APIError) -> QuotaBlockedInfo:
    quota_id: str | None = None
    retry_after_seconds: float | None = None
    try:
        quota_block = _find_error_detail_block(exc.details, "QuotaFailure")
        if quota_block:
            violations = quota_block.get("violations")
            if violations:
                quota_id = violations[0].get("quotaId") or violations[0].get("quotaMetric") or None
        retry_block = _find_error_detail_block(exc.details, "RetryInfo")
        if retry_block:
            retry_delay = retry_block.get("retryDelay")
            if retry_delay:
                # Chaîne au format "6s" ou "6.5s" (protobuf Duration en JSON),
                # jamais d'unité autre que la seconde pour ce champ.
                retry_after_seconds = float(str(retry_delay).rstrip("s"))
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return QuotaBlockedInfo(quota_id=quota_id, retry_after_seconds=retry_after_seconds)


def _is_daily_quota(quota_id: str | None) -> bool:
    """Un quota quotidien (RPD) ne se réinitialise qu'à minuit heure du
    Pacifique (Californie), pas à minuit heure locale : retenter dans les
    secondes/minutes qui suivent est voué à l'échec, contrairement à un quota
    par minute (RPM/TPM) qui se libère naturellement en attendant."""
    return bool(quota_id) and "perday" in quota_id.lower()


def _friendly_error_message(exc: Exception) -> tuple[str, str | None]:
    """Traduit une exception technique de l'API Gemini en message compréhensible
    dans la langue actuellement choisie par l'utilisateur, avec le code d'erreur
    d'origine entre parenthèses pour le diagnostic (support, recherche en
    ligne...). Retourne aussi error_kind ("daily_quota"/"rate_quota"/None),
    indépendant de la langue du message : c'est sur cette valeur, et non sur le
    texte traduit, que l'appelant (main_window) doit se baser pour adapter son
    comportement (ex : proposer une reprise).

    google-genai ne distingue les erreurs que par exc.code (l'entier HTTP) et
    deux sous-classes génériques (ClientError pour 4xx, ServerError pour
    5xx) : contrairement à l'ancien SDK, il n'existe plus de classe Python
    dédiée par cas (quota, service indisponible, timeout...), d'où le
    aiguillage explicite sur le code ci-dessous. Une clé API invalide est
    remontée en 400 (pas 401/403 comme avec l'ancien SDK, vérifié
    empiriquement le 2026-07-21), avec le détail structuré
    error.details[].reason == "API_KEY_INVALID" : ce cas est donc identifié
    par ce repère plutôt que par le seul code HTTP, plus fiable qu'un 400 nu
    qui recouvre aussi d'autres erreurs de requête malformée."""
    if isinstance(exc, genai_errors.APIError):
        code = exc.code
        if code == 429:
            blocked_info = _extract_quota_blocked_info(exc)
            if _is_daily_quota(blocked_info.quota_id):
                return tr("gemini_errors.daily_quota_exceeded"), "daily_quota"
            return tr("gemini_errors.rate_quota_exceeded"), "rate_quota"
        if code == 503:
            return tr("gemini_errors.service_unavailable"), None
        if code == 500:
            return tr("gemini_errors.internal_server_error"), None
        if code == 504:
            return tr("gemini_errors.deadline_exceeded"), None
        if code in (401, 403) or _error_reason(exc) == "API_KEY_INVALID":
            return tr("gemini_errors.invalid_api_key"), None
        status_part = f" ({tr('gemini_errors.error_code', code=code)})" if code else ""
        return tr("gemini_errors.generic_api_error", status_part=status_part, error=exc), None
    return tr("gemini_errors.generic_api_error", status_part="", error=exc), None


def _error_reason(exc: genai_errors.APIError) -> str | None:
    block = _find_error_detail_block(exc.details, "ErrorInfo")
    return block.get("reason") if block else None


_HTTP_OPTIONS_NO_RETRY = genai_types.HttpOptions(
    retry_options=genai_types.HttpRetryOptions(attempts=1)
)

# Client courant, créé par configure() : un seul Client réutilisé pour tous
# les appels (generate_content/count_tokens), à l'image de l'ancien
# genai.configure() global - google-genai n'a pas d'équivalent module-level,
# le Client est le point d'entrée explicite de toutes les requêtes.
_client: genai.Client | None = None


def configure(api_key: str) -> None:
    global _client
    # attempts=1 (pas de nouvelle tentative automatique) reproduit
    # request_options={"retry": None} de l'ancien SDK : par défaut,
    # google-genai retente lui-même jusqu'à 5 fois sur 408/429/5xx, ce qui
    # reproduirait exactement le bug de comptage caché corrigé le 2026-07-21
    # (voir _call_gemini) si on le laissait activé.
    _client = genai.Client(api_key=api_key, http_options=_HTTP_OPTIONS_NO_RETRY)


_JSON_GENERATION_CONFIG = genai_types.GenerateContentConfig(response_mime_type="application/json")

# count_tokens() garde le retry par défaut du SDK (contrairement à
# generate_content, voir _HTTP_OPTIONS_NO_RETRY) : appel gratuit sur un quota
# séparé (voir ARCHITECTURE.md), le laisser retenter un 503 est sans
# conséquence sur le quota RPM/RPD et évite de faire échouer une génération
# pour un simple comptage. Un HttpOptions() vide (sans retry_options) écrase
# ici le retry_options=attempts:1 hérité du Client (configure()), qui
# s'appliquerait sinon à tous les appels y compris celui-ci.
_COUNT_TOKENS_CONFIG = genai_types.CountTokensConfig(http_options=genai_types.HttpOptions())


def count_tokens(text: str, context_label: str = "", model: str = MODEL_NAME) -> int:
    """Appel réseau CountTokens (gratuit, quota séparé de generateContent,
    voir ARCHITECTURE.md). Journalisé comme les appels de génération pour
    pouvoir corréler chaque appel réseau de l'application avec le dashboard
    AI Studio en cas d'écart de compteur."""
    start = time.monotonic()
    _api_call_totals["count_tokens"] += 1
    try:
        result = _client.models.count_tokens(model=model, contents=text, config=_COUNT_TOKENS_CONFIG)
    except Exception as exc:
        _log_api_call(
            f"count_tokens ECHEC contexte={context_label} {type(exc).__name__}: {_one_line(exc)} "
            f"duree={time.monotonic() - start:.1f}s"
        )
        raise
    _api_call_totals["count_tokens_total_tokens"] += result.total_tokens
    _log_api_call(
        f"count_tokens OK contexte={context_label} total_tokens={result.total_tokens} "
        f"duree={time.monotonic() - start:.1f}s"
    )
    return result.total_tokens


def _split_chapters_into_batches(
    chapters: list[Chapter], model: str = MODEL_NAME
) -> list[tuple[list[Chapter], int]]:
    """Regroupe les chapitres en lots dont le texte cumulé tient sous
    max_tokens_per_request (voir ModelInfo), la limite de débit par minute du
    palier gratuit du modèle utilisé (et non max_input_tokens, la fenêtre de
    contexte du modèle, bien plus large - un lot dimensionné sur cette
    dernière saturerait le quota TPM à lui seul). Un chapitre dont le texte
    dépasse à lui seul max_tokens_per_request forme son propre lot (l'appel
    Gemini correspondant échouera probablement, mais ce n'est pas à cette
    fonction de tronquer le contenu du livre).

    Chaque lot est retourné avec le compte de tokens de son texte source déjà
    calculé ici (estimation du prompt final, qui ajoute aussi les consignes du
    template) : réutilisé tel quel par l'appelant pour créditer le suivi de
    quota si l'appel Gemini correspondant échoue, sans refaire d'appel
    count_tokens (donc sans appel réseau) juste pour ça."""
    max_tokens_per_request = get_model_info(model).max_tokens_per_request
    batches: list[tuple[list[Chapter], int]] = []
    current_batch: list[Chapter] = []
    current_tokens = 0

    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_tokens = count_tokens(
            chapter.text, f"decoupage_chapitre_{chapter_index}/{len(chapters)}", model=model
        )
        if current_batch and current_tokens + chapter_tokens > max_tokens_per_request:
            batches.append((current_batch, current_tokens))
            current_batch = []
            current_tokens = 0
        current_batch.append(chapter)
        current_tokens += chapter_tokens

    if current_batch:
        batches.append((current_batch, current_tokens))

    return batches


def _one_line(value: object) -> str:
    """Aplatit un texte (ex : message d'exception multiligne) en une seule
    ligne, pour que chaque événement du journal d'appels API tienne sur une
    ligne physique et reste exploitable avec des outils ligne à ligne."""
    return " ".join(str(value).split())


# Totaux d'appels réseau de la génération en cours, restitués dans la ligne
# FIN (ou ECHEC) du journal d'appels API pour comparaison directe avec le
# dashboard AI Studio : "generate_content" (les vrais appels, comptés dans le
# quota RPM/RPD) et "count_tokens" (censés être gratuits et hors quota, mais
# comptés séparément ici précisément pour pouvoir le vérifier si le dashboard
# monte plus vite que les seuls appels de génération). Incrémentés à l'envoi
# (une tentative échouée reste un appel réseau). "count_tokens_total_tokens"
# cumule le volume de texte soumis au comptage (le total_tokens de chaque
# réponse CountTokens réussie) : si ces appels s'avéraient comptés dans le
# TPM du dashboard malgré leur gratuité annoncée, ce cumul donnerait
# directement le volume à comparer avec le graphe TPM. État module partagé
# sans verrou : une seule génération à la fois (un seul SummarizeWorker), et
# seul le thread worker y touche.
_api_call_totals = {"generate_content": 0, "count_tokens": 0, "count_tokens_total_tokens": 0}


def _reset_api_call_totals() -> None:
    for key in _api_call_totals:
        _api_call_totals[key] = 0


def _api_call_totals_summary() -> str:
    return (
        f"appels_generation={_api_call_totals['generate_content']} "
        f"appels_comptage_tokens={_api_call_totals['count_tokens']} "
        f"tokens_soumis_au_comptage={_api_call_totals['count_tokens_total_tokens']}"
    )


def log_api_event(message: str) -> None:
    """Point d'entrée public du journal d'appels API pour les événements émis
    hors de ce module (ex : ligne de démarrage de l'application, écrite par
    main_window) : même fichier, même format ligne à ligne horodatée que les
    événements d'appel écrits ici."""
    _log_api_call(message)



# Nombre de générations ("generation DEBUT") conservées dans
# api_requests.log : au-delà, la plus ancienne est purgée (voir
# _trim_api_requests_log) pour que le fichier reste toujours de taille
# raisonnable à relire ou à transmettre pour diagnostic.
API_REQUESTS_LOG_MAX_GENERATIONS = 5


def _trim_api_requests_log() -> None:
    """Purge la génération la plus ancienne de debug_logs/api_requests.log
    quand le fichier en contient déjà API_REQUESTS_LOG_MAX_GENERATIONS avant
    même de démarrer la nouvelle : chaque génération commence par une ligne
    'generation DEBUT', qui sert de repère de découpage. Ne garde que ce qui
    suit la 2e occurrence de ce repère, supprimant ainsi le bloc complet du
    plus ancien livre (y compris un éventuel 'application DEMARRAGE' initial
    qui le précédait). Best-effort, comme le reste du journal : une erreur
    d'écriture ici ne doit jamais empêcher la génération de démarrer."""
    try:
        log_path = config.get_debug_logs_dir() / "api_requests.log"
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    debut_indices = [i for i, line in enumerate(lines) if " generation DEBUT " in line]
    if len(debut_indices) < API_REQUESTS_LOG_MAX_GENERATIONS:
        return
    cutoff = debut_indices[1]
    try:
        log_path.write_text("".join(lines[cutoff:]), encoding="utf-8")
    except OSError:
        pass


def _log_api_call(message: str) -> None:
    """Journal d'appels API : une ligne horodatée par événement (envoi,
    succès, échec de chaque appel réseau à Gemini, y compris les comptages de
    tokens), en append dans debug_logs/api_requests.log. Ajouté le 2026-07-21
    pour diagnostiquer un écart inexpliqué entre le compteur local de
    requêtes quotidiennes et celui du dashboard AI Studio (le dashboard
    comptait environ le double) : ce journal donne la liste exacte et datée
    de ce que l'application a réellement envoyé, à comparer avec le
    dashboard. Purgé au-delà de API_REQUESTS_LOG_MAX_GENERATIONS générations
    (voir _trim_api_requests_log) pour rester exploitable et transmissible
    sans grossir indéfiniment. Écriture best-effort : ne doit jamais faire
    échouer un appel ni la génération.

    Chaque ligne est préfixée par pid=<PID> (2026-07-22, support
    multi-instances, voir app/instance_lock.py) : le fichier est partagé par
    toutes les instances de Distillat lancées sur la machine
    (get_debug_logs_dir() est unique, indépendant du mode de lancement), leurs
    lignes s'entrelacent donc chronologiquement si plusieurs tournent en
    parallèle. Ce préfixe permet de filtrer après coup les lignes d'une seule
    instance (ex. grep "pid=12345") sans avoir à deviner la frontière entre
    deux sessions."""
    try:
        log_path = config.get_debug_logs_dir() / "api_requests.log"
        timestamp = datetime.now().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} pid={os.getpid()} {message}\n")
    except OSError:
        pass


def _call_gemini(
    prompt: str,
    quota_tracker: QuotaTracker,
    json_mode: bool,
    on_quota_update: QuotaCallback | None = None,
    estimated_input_tokens: int = 0,
    context_label: str = "",
    model: str = MODEL_NAME,
) -> tuple[str, str | None]:
    """Effectue un seul appel à l'API Gemini, sans retry automatique : toute
    erreur (quota, service indisponible...) remonte immédiatement sous forme
    de GeminiError avec un message clair, laissant à l'utilisateur le choix
    de relancer la génération en recliquant sur Résumer.

    Le suivi de quota (record_call) est crédité que l'appel réussisse ou
    échoue : Google comptabilise la requête (RPM/RPD) côté serveur dès qu'elle
    est reçue, indépendamment du résultat, donc le suivi local doit faire de
    même pour ne pas diverger du dashboard Google (bug constaté le
    2026-07-21). En cas d'échec, usage_metadata n'existe pas (pas de réponse
    exploitable) : estimated_input_tokens sert alors d'estimation des tokens
    d'entrée envoyés, à charge pour l'appelant de le renseigner via
    count_tokens() sur le prompt (appel gratuit, hors quota RPM/RPD/TPM,
    vérifié empiriquement le 2026-07-21 : le compteur RPD du dashboard Google
    ne bouge pas après un count_tokens). Les tokens de sortie restent à 0
    puisque Gemini n'a rien généré.

    record_call() ne pouvant être crédité qu'une fois l'appel revenu (succès
    ou échec), le compteur RPD/RPM affiché restait figé pendant toute la
    durée de l'appel réseau (jusqu'à plusieurs minutes pour un gros livre),
    au point de sembler ne pas bouger du tout à l'envoi d'une requête sur un
    livre tenant en une seule requête (constaté par l'utilisateur le
    2026-07-21). begin_request()/end_request() encadrent donc tout l'appel
    pour permettre à l'UI d'afficher un indicateur "requête en attente"
    distinct du compteur RPD/RPM lui-même, sans jamais influencer ce dernier."""
    quota_tracker.begin_request()
    if on_quota_update:
        on_quota_update(quota_tracker.snapshot())
    _log_api_call(
        f"generate_content ENVOI contexte={context_label} tokens_entree_estimes={estimated_input_tokens}"
    )
    _api_call_totals["generate_content"] += 1
    start = time.monotonic()
    try:
        try:
            # _HTTP_OPTIONS_NO_RETRY (voir configure()) désactive le retry
            # automatique intégré au SDK (par défaut : nouvelles tentatives
            # silencieuses sur 408/429/5xx). Chaque tentative supplémentaire
            # était une vraie requête comptée par Google (RPM/RPD) mais
            # invisible pour l'application, donc impossible à suivre
            # localement (piste découverte le 2026-07-21 en cherchant un
            # écart entre compteur local et dashboard, avec l'ancien SDK
            # google-generativeai - même risque avec google-genai si le
            # retry par défaut restait actif). Sans lui, un appel applicatif
            # = exactement une requête serveur.
            config = _JSON_GENERATION_CONFIG if json_mode else None
            response = _client.models.generate_content(model=model, contents=prompt, config=config)
            usage = response.usage_metadata
            # candidates_token_count peut être None (constaté le 2026-07-21
            # avec google-genai sur une réponse très courte), contrairement à
            # l'ancien SDK qui garantissait toujours un entier : ramené à 0
            # pour ne pas faire échouer le suivi de quota local sur un cas qui
            # n'est pourtant pas une erreur.
            input_tokens = usage.prompt_token_count or 0
            output_tokens = usage.candidates_token_count or 0
            snapshot = quota_tracker.record_call(input_tokens=input_tokens, output_tokens=output_tokens)
            # finish_reason est journalisé systématiquement, même quand text
            # est exploitable : sans ça, une réponse tronquée par la limite de
            # longueur du modèle (MAX_TOKENS) mais non vide (donc acceptée par
            # le `if not text` ci-dessous) ne laissait aucune trace de sa
            # cause réelle, seule l'erreur générique de parsing JSON qui en
            # découlait plus tard était visible (cas vécu le 2026-07-22 sur
            # une consolidation, diagnostiqué a posteriori uniquement grâce au
            # fichier de log brut gemini_unparsable_*.txt).
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            _log_api_call(
                f"generate_content OK contexte={context_label} tokens_entree={input_tokens} "
                f"tokens_sortie={output_tokens} duree={time.monotonic() - start:.1f}s "
                f"requetes_jour={snapshot.requests_today} finish_reason={finish_reason}"
            )
            text = response.text
            if not text:
                # response.text vaut None (pas d'exception, contrairement à
                # l'ancien SDK) quand aucun candidat exploitable n'est
                # retourné, notamment si les filtres de sécurité de Gemini
                # ont bloqué la réponse : sans ce cas, l'utilisateur voyait
                # un message technique brut au lieu d'une explication.
                if finish_reason not in (None, "STOP"):
                    raise GeminiError(tr("gemini_errors.blocked_by_safety_filters"))
                raise GeminiError(tr("gemini_errors.empty_response"))
            return text, finish_reason
        except genai_errors.APIError as exc:
            snapshot = quota_tracker.record_call(input_tokens=estimated_input_tokens, output_tokens=0)
            _log_api_call(
                f"generate_content ECHEC contexte={context_label} {type(exc).__name__}: {_one_line(exc)} "
                f"duree={time.monotonic() - start:.1f}s requetes_jour={snapshot.requests_today}"
            )
            message, error_kind = _friendly_error_message(exc)
            raise GeminiError(message, error_kind=error_kind) from exc
    finally:
        snapshot = quota_tracker.end_request()
        if on_quota_update:
            on_quota_update(snapshot)


DEFAULT_FULL_REPORT_PROMPT = """Tu es un assistant expert en littérature. Voici le texte intégral d'un livre \
intitulé "{book_title}" de {author}.

Produis TOUJOURS EN FRANÇAIS, quelle que soit la langue originale du texte, les quatre éléments \
suivants. Les quatre sont OBLIGATOIRES et de même importance : ne t'arrête JAMAIS en cours de route \
après avoir produit seulement un, deux ou trois d'entre eux, quel que soit celui après lequel tu \
serais tenté de t'arrêter - tu dois toujours aller jusqu'au bout des quatre, y compris les \
personnages/entités et l'analyse, même si le résumé détaillé t'a déjà pris beaucoup de place :

1. Un RÉSUMÉ COURT (deux à trois paragraphes maximum, pas plus) donnant une vue d'ensemble concise de \
l'intrigue ou du propos, du début à la fin.
2. Un RÉSUMÉ DÉTAILLÉ, substantiel et développé (au moins 1500 mots, et bien davantage - \
2500 à 4000 mots - pour un roman long à l'intrigue riche), qui reprend la structure du livre \
(une section par partie ou groupe de chapitres si pertinent) et couvre pour chaque section : \
les événements clés, les rebondissements, les dialogues ou moments marquants, et l'évolution \
des personnages. Ne te contente pas d'une liste télégraphique de faits : développe chaque \
section avec plusieurs phrases fluides et concrètes, comme le ferait un lecteur racontant le \
livre en détail à un ami. Chaque titre de section doit être seul sur sa ligne et commencer \
par "## " (par exemple "## Partie 1 : ..."), ou "### " pour un sous-titre ; n'utilise aucune \
autre mise en forme Markdown dans le texte.
3. La liste des PERSONNAGES ET ENTITÉS PRINCIPAUX : les personnages individuels qui apparaissent \
dans plusieurs chapitres ou scènes ET ont un impact direct sur le déroulement de l'intrigue (par \
leurs décisions, leurs actions ou leurs relations avec le protagoniste), ainsi que les groupes ou \
organisations centraux à l'intrigue (faction, conseil, armée, famille, société secrète...) dès \
lors qu'ils agissent comme un acteur à part entière de l'histoire. Ignore les personnages ou \
groupes qui n'apparaissent qu'une fois ou qui n'influencent pas le cours de l'histoire. Vise \
typiquement entre 3 et 20 entrées selon la richesse du roman (moins pour un texte court ou centré \
sur peu de personnages, plus pour une saga chorale) - n'invente jamais d'entrée pour atteindre ce \
nombre. Pour chaque personnage, rédige une description couvrant son rôle, sa personnalité et son \
évolution ; pour chaque groupe ou organisation, décris plutôt son rôle dans l'intrigue, ses \
objectifs et son influence sur les événements.
4. Une ANALYSE littéraire d'au moins 2500 à 4000 mots, structurée en plusieurs paragraphes distincts \
couvrant les thèmes principaux, le style d'écriture et la construction narrative, puis la portée \
de l'œuvre - développée et argumentée, sans répéter le contenu déjà couvert par les résumés.

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact. Les quatre champs ci-dessous sont tous obligatoires : ne renvoie \
jamais "characters" vide ou "analysis" vide, quel que soit le nombre de mots déjà produits pour \
les champs précédents :
{{
  "summary": "Le résumé court ici, en français...",
  "detailed_summary": "Le résumé détaillé et développé ici, en français...",
  "characters": [{{"name": "Nom", "description": "Description en français..."}}, ...],
  "analysis": "L'analyse littéraire ici, en français..."
}}

Texte du livre :
---
{full_text}
---

Réponds uniquement avec l'objet JSON, avec les quatre champs "summary", "detailed_summary", \
"characters" et "analysis" tous renseignés."""


DEFAULT_CHAPTER_SUMMARY_PROMPT = """Tu résumes des chapitres du livre "{book_title}" de {author}.

Voici un lot de chapitres consécutifs de ce livre, chacun précédé de son titre exact entre balises \
[[[TITRE: ...]]]. Résume CHAQUE chapitre SÉPARÉMENT, TOUJOURS EN FRANÇAIS quelle que soit la langue \
du texte source. Pour chaque chapitre, sois fidèle au contenu, couvre les événements et idées \
importants (scènes clés, dialogues marquants, évolution des personnages). Ces résumés seront \
ensuite fusionnés avec ceux des autres lots de chapitres : ne les brade pas en une liste \
télégraphique, développe chacun sur au moins 300 mots (davantage si le chapitre est riche en \
événements), sans limite maximale. Rédige chaque résumé en texte brut, sans aucune mise en \
forme Markdown.

Chapitres :
---
{chapters_text}
---

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact, avec EXACTEMENT une entrée par chapitre du lot ci-dessus, dans le même \
ordre, en reprenant le titre exact de chaque chapitre :
{{
  "chapter_summaries": [{{"title": "Titre exact du chapitre", "summary": "Résumé en français..."}}, ...]
}}

Réponds uniquement avec l'objet JSON."""


DEFAULT_CONSOLIDATION_PROMPT = """Voici les résumés successifs des chapitres du livre "{book_title}" de {author} \
(le livre est trop volumineux pour être traité en une seule requête, il a donc été découpé par lots \
de chapitres puis résumé lot par lot).

Résumés par chapitre :
---
{chapter_summaries}
---

À partir de ces résumés partiels, TOUJOURS EN FRANÇAIS, produis les quatre éléments suivants. Les \
quatre sont OBLIGATOIRES et de même importance : ne t'arrête JAMAIS en cours de route après avoir \
produit seulement un, deux ou trois d'entre eux, quel que soit celui après lequel tu serais tenté \
de t'arrêter - tu dois toujours aller jusqu'au bout des quatre, y compris les personnages/entités \
et l'analyse, même si le résumé détaillé t'a déjà pris beaucoup de place :

1. Un RÉSUMÉ COURT (deux à trois paragraphes maximum, pas plus) donnant une vue d'ensemble concise de \
l'intrigue du début à la fin.
2. Un RÉSUMÉ DÉTAILLÉ (au moins 1500 mots, et bien davantage - 2500 à 4000 mots - pour un roman \
long à l'intrigue riche), qui fusionne et reformule les résumés de chapitre ci-dessus en un texte \
cohérent et fluide (pas une simple concaténation), en conservant les événements clés, les \
rebondissements et l'évolution des personnages de chaque partie, en évitant les répétitions et \
en assurant une continuité narrative claire entre les parties. Chaque titre de section doit \
être seul sur sa ligne et commencer par "## " (par exemple "## Partie 1 : ..."), ou "### " \
pour un sous-titre ; n'utilise aucune autre mise en forme Markdown dans le texte.
3. La liste des PERSONNAGES ET ENTITÉS PRINCIPAUX : les personnages individuels qui apparaissent \
dans plusieurs chapitres ou scènes ET ont un impact direct sur le déroulement de l'intrigue (par \
leurs décisions, leurs actions ou leurs relations avec le protagoniste), ainsi que les groupes ou \
organisations centraux à l'intrigue (faction, conseil, armée, famille, société secrète...) dès \
lors qu'ils agissent comme un acteur à part entière de l'histoire. Ignore les personnages ou \
groupes qui n'apparaissent qu'une fois ou qui n'influencent pas le cours de l'histoire. Vise \
typiquement entre 3 et 20 entrées selon la richesse du roman (moins pour un texte court ou centré \
sur peu de personnages, plus pour une saga chorale) - n'invente jamais d'entrée pour atteindre ce \
nombre. Pour chaque personnage, rédige une description couvrant son rôle, sa personnalité et son \
évolution ; pour chaque groupe ou organisation, décris plutôt son rôle dans l'intrigue, ses \
objectifs et son influence sur les événements.
4. Une ANALYSE littéraire d'au moins 2500 à 4000 mots, structurée en plusieurs paragraphes distincts \
couvrant les thèmes principaux, le style d'écriture et la construction narrative, puis la portée \
de l'œuvre - développée et argumentée, sans répéter le contenu déjà couvert par les résumés.

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact. Les quatre champs ci-dessous sont tous obligatoires : ne renvoie \
jamais "characters" vide ou "analysis" vide, quel que soit le nombre de mots déjà produits pour \
les champs précédents :
{{
  "summary": "Le résumé court ici, en français...",
  "detailed_summary": "Le résumé détaillé et développé ici, en français...",
  "characters": [{{"name": "Nom", "description": "Description en français..."}}, ...],
  "analysis": "L'analyse littéraire ici, en français..."
}}

Réponds uniquement avec l'objet JSON, avec les quatre champs "summary", "detailed_summary", \
"characters" et "analysis" tous renseignés."""


DEFAULT_FULL_REPORT_PROMPT_EN = """You are an expert literary assistant. Here is the full text of a book \
titled "{book_title}" by {author}.

Produce ALWAYS IN ENGLISH, regardless of the original language of the text, the following four \
elements. All four are MANDATORY and equally important: NEVER stop partway through after producing \
only one, two, or three of them, no matter which one you might be tempted to stop after - you must \
always go all the way through all four, including the characters/entities and the analysis, even if \
the detailed summary has already taken up a lot of space:

1. A SHORT SUMMARY (two to three paragraphs maximum, no more) giving a concise overview of the plot \
or subject, from beginning to end.
2. A DETAILED SUMMARY, substantial and developed (at least 1500 words, and considerably more - \
2500 to 4000 words - for a long novel with a rich plot), that follows the structure of the book \
(one section per part or group of chapters where relevant) and covers, for each section: the key \
events, the twists, the memorable dialogue or moments, and the characters' development. Do not \
settle for a telegraphic list of facts: develop each section with several fluid, concrete sentences, \
as a reader would when recounting the book in detail to a friend. Each section heading must stand \
alone on its own line and start with "## " (for example "## Part 1: ..."), or "### " for a \
subheading; do not use any other Markdown formatting in the text.
3. The list of MAIN CHARACTERS AND ENTITIES: individual characters who appear across multiple \
chapters or scenes AND have a direct impact on the plot (through their decisions, actions, or \
relationships with the protagonist), as well as groups or organizations central to the plot \
(faction, council, army, family, secret society...) whenever they act as a fully-fledged actor in \
the story. Ignore characters or groups who appear only once or who do not influence the course of \
the story. Aim typically for between 3 and 20 entries depending on how rich the novel is (fewer for \
a short text or one centered on few characters, more for a sprawling ensemble saga) - never invent \
an entry to reach this number. For each character, write a description covering their role, \
personality, and development; for each group or organization, describe instead its role in the \
plot, its goals, and its influence on events.
4. A literary ANALYSIS of at least 2500 to 4000 words, structured in several distinct paragraphs \
covering the main themes, the writing style and narrative construction, then the work's significance \
- developed and well-argued, without repeating content already covered by the summaries.

Respond STRICTLY with a valid JSON object, with no text whatsoever before or after, no markdown \
fences, in the exact format. All four fields below are mandatory: never return an empty \
"characters" or an empty "analysis", no matter how many words you have already produced for the \
preceding fields:
{{
  "summary": "The short summary here, in English...",
  "detailed_summary": "The detailed, developed summary here, in English...",
  "characters": [{{"name": "Name", "description": "Description in English..."}}, ...],
  "analysis": "The literary analysis here, in English..."
}}

Book text:
---
{full_text}
---

Respond only with the JSON object, with all four fields "summary", "detailed_summary", \
"characters" and "analysis" filled in."""


DEFAULT_CHAPTER_SUMMARY_PROMPT_EN = """You are summarizing chapters of the book "{book_title}" by {author}.

Here is a batch of consecutive chapters from this book, each preceded by its exact title between \
[[[TITLE: ...]]] tags. Summarize EACH chapter SEPARATELY, ALWAYS IN ENGLISH regardless of the \
language of the source text. For each chapter, stay faithful to the content, covering the important \
events and ideas (key scenes, memorable dialogue, character development). These summaries will later \
be merged with those of other chapter batches: do not shortchange them into a telegraphic list, \
develop each one to at least 300 words (more if the chapter is rich in events), with no maximum \
limit. Write each summary in plain text, with no Markdown formatting whatsoever.

Chapters:
---
{chapters_text}
---

Respond STRICTLY with a valid JSON object, with no text whatsoever before or after, no markdown \
fences, in the exact format, with EXACTLY one entry per chapter in the batch above, in the same \
order, reusing the exact title of each chapter:
{{
  "chapter_summaries": [{{"title": "Exact chapter title", "summary": "Summary in English..."}}, ...]
}}

Respond only with the JSON object."""


DEFAULT_CONSOLIDATION_PROMPT_EN = """Here are the successive chapter summaries of the book "{book_title}" \
by {author} (the book is too large to be processed in a single request, so it was split into chapter \
batches and summarized batch by batch).

Chapter summaries:
---
{chapter_summaries}
---

From these partial summaries, ALWAYS IN ENGLISH, produce the following four elements. All four are \
MANDATORY and equally important: NEVER stop partway through after producing only one, two, or three \
of them, no matter which one you might be tempted to stop after - you must always go all the way \
through all four, including the characters/entities and the analysis, even if the detailed summary \
has already taken up a lot of space:

1. A SHORT SUMMARY (two to three paragraphs maximum, no more) giving a concise overview of the plot \
from beginning to end.
2. A DETAILED SUMMARY (at least 1500 words, and considerably more - 2500 to 4000 words - for a long \
novel with a rich plot), that merges and rephrases the chapter summaries above into a coherent, \
fluid text (not a mere concatenation), preserving the key events, the twists, and the characters' \
development from each part, avoiding repetition, and ensuring clear narrative continuity between \
parts. Each section heading must stand alone on its own line and start with "## " (for example \
"## Part 1: ..."), or "### " for a subheading; do not use any other Markdown formatting in the text.
3. The list of MAIN CHARACTERS AND ENTITIES: individual characters who appear across multiple \
chapters or scenes AND have a direct impact on the plot (through their decisions, actions, or \
relationships with the protagonist), as well as groups or organizations central to the plot \
(faction, council, army, family, secret society...) whenever they act as a fully-fledged actor in \
the story. Ignore characters or groups who appear only once or who do not influence the course of \
the story. Aim typically for between 3 and 20 entries depending on how rich the novel is (fewer for \
a short text or one centered on few characters, more for a sprawling ensemble saga) - never invent \
an entry to reach this number. For each character, write a description covering their role, \
personality, and development; for each group or organization, describe instead its role in the \
plot, its goals, and its influence on events.
4. A literary ANALYSIS of at least 2500 to 4000 words, structured in several distinct paragraphs \
covering the main themes, the writing style and narrative construction, then the work's significance \
- developed and well-argued, without repeating content already covered by the summaries.

Respond STRICTLY with a valid JSON object, with no text whatsoever before or after, no markdown \
fences, in the exact format. All four fields below are mandatory: never return an empty \
"characters" or an empty "analysis", no matter how many words you have already produced for the \
preceding fields:
{{
  "summary": "The short summary here, in English...",
  "detailed_summary": "The detailed, developed summary here, in English...",
  "characters": [{{"name": "Name", "description": "Description in English..."}}, ...],
  "analysis": "The literary analysis here, in English..."
}}

Respond only with the JSON object, with all four fields "summary", "detailed_summary", \
"characters" and "analysis" filled in."""


DEFAULT_PROMPT_TEMPLATES_FR = {
    "full_report": DEFAULT_FULL_REPORT_PROMPT,
    "chapter_summary": DEFAULT_CHAPTER_SUMMARY_PROMPT,
    "consolidation": DEFAULT_CONSOLIDATION_PROMPT,
}

DEFAULT_PROMPT_TEMPLATES_EN = {
    "full_report": DEFAULT_FULL_REPORT_PROMPT_EN,
    "chapter_summary": DEFAULT_CHAPTER_SUMMARY_PROMPT_EN,
    "consolidation": DEFAULT_CONSOLIDATION_PROMPT_EN,
}

_DEFAULT_PROMPT_TEMPLATES_BY_LANGUAGE = {
    "fr": DEFAULT_PROMPT_TEMPLATES_FR,
    "en": DEFAULT_PROMPT_TEMPLATES_EN,
}


def default_prompt_templates() -> dict[str, str]:
    """Prompts par défaut pour la langue actuellement choisie par l'utilisateur
    (voir app.i18n) : la langue de sortie demandée à Gemini suit toujours la
    langue de l'UI au moment de la génération, jamais l'inverse."""
    return _DEFAULT_PROMPT_TEMPLATES_BY_LANGUAGE[i18n.current_language()]


def _get_prompt_template(key: str, use_custom_prompts: bool, profile_id: str | None) -> str:
    """Renvoie le template personnalisé par l'utilisateur pour ce prompt,
    cette langue et ce profil de clé API s'il existe, sinon le template par
    défaut de la langue actuellement choisie (voir default_prompt_templates).
    Les personnalisations sont propres à chaque profil et à chaque langue
    (2026-07-22, support des profils multiples) : celles du français
    n'affectent jamais l'anglais et inversement, et celles d'un profil
    n'affectent jamais celles d'un autre. profile_id est None si aucun profil
    n'est actif (ce qui ne devrait pas arriver en pratique, une génération
    exigeant déjà une clé API donc un profil résolu) : dans ce cas, aucune
    personnalisation n'est cherchée, comme si use_custom_prompts était faux."""
    if use_custom_prompts and profile_id is not None:
        custom = load_custom_prompts(i18n.current_language(), profile_id)
        if key in custom:
            return custom[key]
    return default_prompt_templates()[key]


def _format_prompt_template(template: str, prompt_label: str, **kwargs: str) -> str:
    """Remplit un template de prompt, avec un message clair si un repère entre
    accolades a été mal orthographié lors d'une personnalisation via le
    bouton Prompts (KeyError sinon peu explicite : juste le nom du repère,
    ex. "'full_textt'")."""
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        raise GeminiError(tr("gemini_errors.invalid_prompt_placeholder", prompt_label=prompt_label, error=exc)) from exc


def _full_report_prompt(content: BookContent, use_custom_prompts: bool, profile_id: str | None) -> str:
    """Un seul prompt demandant les deux résumés + personnages + analyse en une
    requête, pour un livre dont le texte tient dans la fenêtre de contexte du modèle."""
    template = _get_prompt_template("full_report", use_custom_prompts, profile_id)
    return _format_prompt_template(
        template,
        tr("prompts_dialog.tabs.full_report.title"),
        book_title=content.book_title,
        author=content.author,
        full_text=content.full_text,
    )


_CHAPTER_TITLE_MARKER_BY_LANGUAGE = {"fr": "TITRE", "en": "TITLE"}


def _chapters_batch_text(chapters: list[Chapter]) -> str:
    """Le marqueur ([[[TITRE: ...]]] ou [[[TITLE: ...]]]) doit correspondre à
    celui annoncé dans le prompt de la langue active (_chapter_summary_prompt),
    sous peine d'incohérence pour Gemini entre l'instruction et le texte reçu."""
    marker = _CHAPTER_TITLE_MARKER_BY_LANGUAGE[i18n.current_language()]
    return "\n\n".join(f"[[[{marker}: {chapter.title}]]]\n{chapter.text}" for chapter in chapters)


def _chapter_summary_prompt(
    book_title: str, author: str, batch: list[Chapter], use_custom_prompts: bool, profile_id: str | None
) -> str:
    """Prompt de résumé appliqué à un LOT de chapitres consécutifs (voir
    _split_chapters_into_batches) plutôt qu'à un seul, pour limiter le nombre
    de requêtes envoyées à l'API sur le palier gratuit (quota quotidien très
    serré : 20 requêtes/jour par défaut)."""
    template = _get_prompt_template("chapter_summary", use_custom_prompts, profile_id)
    return _format_prompt_template(
        template,
        tr("prompts_dialog.tabs.chapter_summary.title"),
        book_title=book_title,
        author=author,
        chapters_text=_chapters_batch_text(batch),
    )


def _consolidation_prompt(
    book_title: str,
    author: str,
    chapter_summaries: list[tuple[str, str]],
    use_custom_prompts: bool,
    profile_id: str | None,
) -> str:
    # Un chapitre au résumé vide (page sans contenu narratif, voir
    # _parse_chapter_summaries_batch_json) n'a rien à apporter à la
    # consolidation : l'inclure enverrait un titre suivi de rien à Gemini.
    joined = "\n\n".join(f"### {title}\n{summary}" for title, summary in chapter_summaries if summary)
    template = _get_prompt_template("consolidation", use_custom_prompts, profile_id)
    return _format_prompt_template(
        template,
        tr("prompts_dialog.tabs.consolidation.title"),
        book_title=book_title,
        author=author,
        chapter_summaries=joined,
    )


def _strip_json_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


_STUTTER_TAIL_WINDOW = 200
_STUTTER_MAX_SUFFIX_LENGTH = 80
_STUTTER_IGNORED_CHARS = " \t\r\n{}\"',."
# En dessous de cette longueur (normalisée), un fragment est accepté comme
# bégaiement même si un de ses "mots" ne se retrouve pas tel quel dans la fin
# du texte accepté (voir _looks_like_stutter) : à cette taille, la perte de
# contenu légitime en cas de faux positif est de toute façon négligeable.
_STUTTER_SHORT_FRAGMENT_LENGTH = 25


def _normalize_for_stutter_check(text: str) -> str:
    return "".join(ch for ch in text if ch not in _STUTTER_IGNORED_CHARS)


def _looks_like_stutter(accepted_text: str, suffix: str) -> bool:
    """Détecte le bégaiement de fin de génération observé chez Gemini en mode
    JSON natif : après avoir correctement fermé l'objet JSON demandé, le
    modèle répète parfois quelques fragments de la toute fin du texte déjà
    produit (ex. la fin de la valeur de "analysis" suivie de "}") au lieu de
    s'arrêter, cassant la syntaxe juste avant/à la fermeture (voir
    conversation du 2026-07-20, reproduit par appel API réel : finish_reason
    STOP, pas MAX_TOKENS - ce n'est pas une troncature).

    N'accepte comme bégaiement que ce qui est à la fois COURT et entièrement
    composé de fragments déjà présents dans la fin du texte accepté : un vrai
    contenu supplémentaire (second objet JSON légitime, texte nouveau) ne
    remplit pas ce critère et n'est donc jamais réparé silencieusement.

    Exception, en dessous de _STUTTER_SHORT_FRAGMENT_LENGTH (fragment très
    court, ex. un mot coupé en plein milieu comme "anation" pour
    "profan[ation]") : le critère mot-à-mot est trop strict pour ce cas
    (repéré le 2026-07-22 sur une consolidation) car un mot tronqué ne se
    retrouve jamais tel quel dans le texte accepté. À cette taille, même un
    faux positif ferait perdre au plus quelques caractères."""
    if not suffix or len(suffix) > _STUTTER_MAX_SUFFIX_LENGTH:
        return False

    tail = accepted_text[-_STUTTER_TAIL_WINDOW:]
    normalized_tail = _normalize_for_stutter_check(tail)
    normalized_suffix = _normalize_for_stutter_check(suffix)

    if not normalized_suffix:
        # Le suffixe ne contenait que de la ponctuation/espaces/accolades :
        # rien qui ressemble à du vrai contenu nouveau.
        return True

    if len(normalized_suffix) <= _STUTTER_SHORT_FRAGMENT_LENGTH:
        return True

    # Chaque fragment du suffixe (séparé par les caractères ignorés, ex. les
    # guillemets fermant/ouvrant une chaîne répétée) doit réapparaître tel
    # quel dans la fin du texte déjà accepté.
    fragments = [f for f in suffix.split() if _normalize_for_stutter_check(f)]
    return all(_normalize_for_stutter_check(fragment) in normalized_tail for fragment in fragments)


_STUTTER_REPAIR_MAX_LINES_DROPPED = 8


def _try_repair_stuttered_json(text: str) -> tuple[dict, str] | None:
    """Tente de récupérer un objet JSON dont la clé de fermeture attendue a
    été remplacée par un bégaiement de fin de génération (voir
    _looks_like_stutter) : Gemini termine correctement la dernière valeur
    (ex. la chaîne de "analysis"), puis répète des fragments de cette même
    fin AVANT de placer (ou à la place de) l'accolade de fermeture, ce qui
    casse la syntaxe à un point où il n'existe déjà plus d'accolade fermante
    valide dans le texte reçu (une simple recherche de la dernière "}" qui
    parse ne suffit donc pas).

    Recoupe le texte ligne par ligne en partant de la fin, referme l'objet
    racine avec une accolade ajoutée, et ne garde la première coupe qui donne
    un JSON valide QUE si les quelques lignes retirées sont reconnues comme
    un bégaiement du texte restant ; sinon ne renvoie rien, pour ne jamais
    masquer un cas différent (ex. vraie troncature en plein milieu d'une
    valeur, qu'aucune fermeture ajoutée ne peut légitimement réparer).

    Si aucune coupe par la fin ne donne de JSON valide, tente la variante du
    bégaiement INTERNE (_try_repair_internal_stutter) avant d'abandonner."""
    lines = text.split("\n")
    max_cut = len(lines)
    min_cut = max(0, len(lines) - _STUTTER_REPAIR_MAX_LINES_DROPPED)
    for cut in range(max_cut, min_cut, -1):
        candidate = "\n".join(lines[:cut])
        try:
            obj = json.loads(candidate + "}")
        except json.JSONDecodeError:
            continue
        suffix = "\n".join(lines[cut:]).strip()
        if _looks_like_stutter(candidate, suffix):
            return obj, suffix
        return None
    return _try_repair_internal_stutter(lines)


def _try_repair_internal_stutter(lines: list[str]) -> tuple[dict, str] | None:
    """Variante du bégaiement observée le 2026-07-21 sur un lot de résumés de
    chapitres : Gemini a répété un fragment de la fin de la dernière valeur
    sur une ligne parasite, puis a quand même refermé correctement le JSON
    derrière ("}", "]", "}"). La ou les lignes fautives sont donc au MILIEU
    du texte, suivies des fermetures légitimes : la coupe par la fin de
    _try_repair_stuttered_json ne peut pas les atteindre sans emporter aussi
    ces fermetures (et sa refermeture par un unique "}" ne conviendrait de
    toute façon qu'à un bégaiement à la racine de l'objet, pas à la structure
    imbriquée des lots de chapitres, qui exigerait "}" + "]" + "}").

    Cherche un petit bloc de lignes contigu proche de la fin dont la
    SUPPRESSION SEULE (sans rien ajouter) rend le JSON valide, en essayant
    les blocs les plus petits d'abord, et pour une même taille les plus
    proches de la fin d'abord. Un bloc n'est retenu QUE s'il est reconnu
    comme un bégaiement du texte qui le précède (mêmes garde-fous stricts
    que _looks_like_stutter) ; un bloc dont la suppression rend le JSON
    valide mais qui contient du vrai contenu (ex. une entrée de chapitre
    entière) est refusé, et contrairement à la coupe par la fin la recherche
    continue avec les autres blocs : ici un bloc refusé signifie seulement
    que ce n'était pas le bon emplacement, pas que le texte est ambigu."""
    earliest = max(0, len(lines) - _STUTTER_REPAIR_MAX_LINES_DROPPED)
    for size in range(1, _STUTTER_REPAIR_MAX_LINES_DROPPED + 1):
        for start in range(len(lines) - size, earliest - 1, -1):
            removed = "\n".join(lines[start:start + size]).strip()
            if not removed:
                continue
            candidate = "\n".join(lines[:start] + lines[start + size:])
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if _looks_like_stutter("\n".join(lines[:start]), removed):
                return obj, removed
    return None



# Nombre de fichiers gemini_unparsable_*.txt conservés dans debug_logs/ : au-
# delà, les plus anciens sont purgés (voir _log_unparsable_response) pour ne
# pas accumuler indéfiniment un fichier par échec de parsing.
UNPARSABLE_LOGS_MAX_FILES = 5


def _log_unparsable_response(raw_text: str, context_label: str, error: json.JSONDecodeError) -> None:
    """Sauvegarde la réponse Gemini brute qui a fait échouer tout parsing (y
    compris la tentative de réparation du bégaiement), pour diagnostic après
    coup : sans ça, la réponse exacte qui a fait échouer une génération était
    perdue dès l'affichage de l'erreur, ne laissant que le message d'erreur
    générique pour comprendre ce qui s'est passé. Best-effort : une erreur
    d'écriture ici (disque plein, permissions...) ne doit jamais empêcher
    l'erreur GeminiError normale de remonter à l'utilisateur."""
    try:
        logs_dir = config.get_debug_logs_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = logs_dir / f"gemini_unparsable_{context_label}_{timestamp}.txt"
        header = (
            f"context: {context_label}\n"
            f"timestamp: {datetime.now().isoformat()}\n"
            f"json_error: {error}\n"
            f"{'-' * 40}\n"
        )
        log_path.write_text(header + raw_text, encoding="utf-8")
        # Trace aussi l'événement dans la chronologie du journal d'appels
        # API : la requête elle-même a réussi (ligne OK déjà écrite), mais sa
        # réponse est inexploitable et la génération va échouer - sans cette
        # ligne, le journal montrerait un OK suivi d'aucune FIN, sans
        # explication visible dans la chronologie.
        _log_api_call(f"reponse_illisible contexte={context_label} fichier={log_path.name}")
        # Le contexte (variable) précède le timestamp dans le nom de fichier,
        # donc un tri par nom mélangerait les contextes entre eux : on trie
        # par date de modification pour retrouver les plus anciens.
        existing = sorted(logs_dir.glob("gemini_unparsable_*.txt"), key=lambda p: p.stat().st_mtime)
        for old_path in existing[:-UNPARSABLE_LOGS_MAX_FILES]:
            old_path.unlink(missing_ok=True)
    except OSError:
        pass


def _parse_json_object(
    raw_text: str, context_label: str = "unknown", finish_reason: str | None = None
) -> tuple[dict, str]:
    """Parse le premier objet JSON de la réponse. Même en mode JSON natif de
    l'API, Gemini produit parfois du contenu superflu après un premier objet
    par ailleurs valide (ex : un second objet accolé) ; json.loads() rejette
    ce cas ("Extra data") alors que le contenu utile est bien présent et
    exploitable. Le texte en trop est retourné (au lieu d'être jeté) : il
    peut s'agir de contenu légitime que l'utilisateur voudra récupérer à la
    main, ce n'est pas à l'application de décider silencieusement qu'il ne
    sert à rien.

    Si le parsing direct échoue, tente une réparation ciblée du bégaiement de
    fin de génération (_try_repair_stuttered_json) avant d'abandonner : ce
    cas est distinct d'un contenu superflu propre (raw_decode le gère déjà)
    car le bégaiement casse la syntaxe de l'objet JSON lui-même. Si même cette
    réparation échoue, la réponse brute est journalisée (_log_unparsable_response)
    avant de lever l'erreur, pour permettre un diagnostic sur un futur cas non
    couvert par la réparation actuelle. context_label identifie l'appel Gemini
    en cause (ex. "consolidation", "chapter_summary_batch", "full_report")
    dans le nom du fichier de log.

    finish_reason (renvoyé par _call_gemini avec le texte brut) permet de
    distinguer un cas de vraie troncature confirmée (MAX_TOKENS : la réponse
    s'arrête net, sans texte superflu ni fermeture JSON, car le modèle a
    atteint sa limite de longueur en cours de génération) d'un échec de
    parsing sans cause connue, pour donner à l'utilisateur un message
    explicite plutôt que l'erreur générique de format inattendu (cas vécu le
    2026-07-22 sur une consolidation, où finish_reason n'était même pas
    consulté puisque le texte reçu n'était pas vide)."""
    text = _strip_json_fences(raw_text)
    try:
        obj, end_index = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        repaired = _try_repair_stuttered_json(text)
        if repaired is not None:
            return repaired
        _log_unparsable_response(raw_text, context_label, exc)
        if finish_reason == "MAX_TOKENS":
            raise GeminiError(tr("gemini_errors.truncated_response")) from exc
        raise GeminiError(tr("gemini_errors.unreadable_response", error=exc)) from exc
    leftover = text[end_index:].strip()
    return obj, leftover


def _normalize_dashes(text: str) -> str:
    """Remplace les tirets cadratin (—) et demi-cadratin (–) que Gemini peut
    produire par un tiret simple, quelle que soit la section concernée."""
    return text.replace("—", "-").replace("–", "-")


def _parse_characters_list(data: list) -> list[Character]:
    """Ignore silencieusement une entrée qui n'est pas de la forme attendue
    (dict avec name/description) : le mode JSON natif de Gemini garantit une
    syntaxe JSON valide, pas le respect du schéma demandé dans le prompt."""
    characters: list[Character] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = _normalize_dashes(item.get("name", "").strip())
        description = _normalize_dashes(item.get("description", "").strip())
        if name and description:
            characters.append(Character(name=name, description=description))
    return characters


def _parse_full_report_json(
    raw_text: str, context_label: str = "full_report", finish_reason: str | None = None
) -> tuple[str, str, list[Character], str, str]:
    """Parse la réponse combinée résumé + personnages + analyse. Le 4e élément
    retourné est le texte ignoré après le premier objet JSON (vide la plupart
    du temps). context_label distingue, dans le log de diagnostic en cas
    d'échec, l'appel "rapport complet" (livre tenant en une requête) de
    l'appel "consolidation" (dernière requête d'un livre découpé en lots) :
    même fonction de parsing, deux contextes d'appel différents."""
    data, leftover = _parse_json_object(raw_text, context_label, finish_reason)
    if not isinstance(data, dict):
        # Le mode JSON natif de Gemini garantit une syntaxe JSON valide, mais
        # pas que la racine soit un objet conforme au schéma demandé (ex : une
        # liste accolée) : sans ce contrôle, l'utilisateur voyait une erreur
        # technique brute ("'list' object has no attribute 'get'").
        raise GeminiError(tr("gemini_errors.unexpected_response_shape"))

    summary = _normalize_dashes(str(data.get("summary", "")).strip())
    detailed_summary = _normalize_dashes(str(data.get("detailed_summary", "")).strip())
    analysis = _normalize_dashes(str(data.get("analysis", "")).strip())
    characters = _parse_characters_list(data.get("characters", []))

    if not summary:
        raise GeminiError(tr("gemini_errors.no_usable_summary"))

    return summary, detailed_summary, characters, analysis, leftover


def _parse_chapter_summaries_batch_json(
    raw_text: str, batch: list[Chapter], finish_reason: str | None = None
) -> tuple[list[tuple[str, str]], str]:
    """Parse la réponse d'un lot de résumés de chapitre. Associe chaque résumé
    au titre du chapitre correspondant dans `batch` par position (et non par
    correspondance exacte du titre renvoyé par Gemini, qui peut légèrement
    différer de l'original) : le nombre d'entrées attendu est connu à
    l'avance, contrairement au cas des personnages où Gemini choisit lui-même
    combien d'entrées produire."""
    data, leftover = _parse_json_object(raw_text, "chapter_summary_batch", finish_reason)
    raw_summaries = data.get("chapter_summaries", [])

    summaries: list[tuple[str, str]] = []
    for i, chapter in enumerate(batch):
        if i < len(raw_summaries) and isinstance(raw_summaries[i], dict):
            summary = _normalize_dashes(str(raw_summaries[i].get("summary", "")).strip())
        else:
            summary = ""
        # Un résumé vide est toléré (pas d'erreur) : certains chapitres n'ont
        # aucun contenu narratif à résumer (page "Du même auteur", mentions
        # légales...), Gemini renvoie alors légitimement un résumé vide pour
        # eux plutôt que d'inventer du contenu. Le chapitre est simplement
        # absent des résumés utiles, sans faire échouer tout le lot.
        summaries.append((chapter.title, summary))

    return summaries, leftover


def generate_book_report(
    content: BookContent,
    quota_tracker: QuotaTracker,
    on_progress: ProgressCallback | None = None,
    on_quota_update: QuotaCallback | None = None,
    use_custom_prompts: bool = True,
    profile_id: str | None = None,
    resume_chapter_summaries: list[tuple[str, str]] | None = None,
    resume_batches_done: int = 0,
    model: str = MODEL_NAME,
) -> BookReport:
    """Génère à la suite le résumé, les fiches personnages et l'analyse littéraire
    du livre. Le résumé est produit directement si le livre tient dans une seule
    requête, ou par découpage en chapitres puis consolidation sinon.

    resume_chapter_summaries/resume_batches_done permettent de reprendre une
    génération en lots interrompue par un échec partiel (voir
    PartialGenerationError) sans reformuler les lots déjà résumés avec
    succès : ignorés si le livre tient en une seule requête.

    Enveloppe de journalisation autour de _generate_book_report_impl : les
    marqueurs DEBUT/FIN/ECHEC du journal d'appels API délimitent chaque
    génération, avec l'état du compteur local avant/après, l'info de reprise
    éventuelle (des lots déjà faits sont sautés, ce qui explique un nombre de
    requêtes inférieur au nombre de lots annoncé) et les totaux d'appels
    réseau de la génération (appels de génération et appels de comptage de
    tokens comptés séparément, voir _api_call_totals). La ligne ECHEC est
    écrite quelle que soit l'erreur (quota, réponse illisible, bug...) pour
    que ces totaux ne soient jamais perdus - c'est précisément dans les
    générations qui échouent qu'on en a le plus besoin."""
    _reset_api_call_totals()
    _trim_api_requests_log()
    _log_api_call(
        f"generation DEBUT version={VERSION} livre={_one_line(content.book_title)} modele={model} "
        f"reprise_lots_deja_faits={resume_batches_done} "
        f"requetes_jour_avant={quota_tracker.snapshot().requests_today}"
    )
    try:
        result = _generate_book_report_impl(
            content,
            quota_tracker,
            on_progress=on_progress,
            on_quota_update=on_quota_update,
            use_custom_prompts=use_custom_prompts,
            profile_id=profile_id,
            resume_chapter_summaries=resume_chapter_summaries,
            resume_batches_done=resume_batches_done,
            model=model,
        )
    except Exception:
        _log_api_call(
            f"generation ECHEC livre={_one_line(content.book_title)} "
            f"{_api_call_totals_summary()} "
            f"requetes_jour={quota_tracker.snapshot().requests_today}"
        )
        raise
    _log_api_call(
        f"generation FIN livre={_one_line(content.book_title)} "
        f"{_api_call_totals_summary()} "
        f"requetes_jour={quota_tracker.snapshot().requests_today}"
    )
    return result


def _generate_book_report_impl(
    content: BookContent,
    quota_tracker: QuotaTracker,
    on_progress: ProgressCallback | None = None,
    on_quota_update: QuotaCallback | None = None,
    use_custom_prompts: bool = True,
    profile_id: str | None = None,
    resume_chapter_summaries: list[tuple[str, str]] | None = None,
    resume_batches_done: int = 0,
    model: str = MODEL_NAME,
) -> BookReport:
    def report(done: int, total: int, message: str) -> None:
        if on_progress:
            on_progress(done, total, message)

    def call(
        prompt: str,
        context_label: str,
        estimated_input_tokens: int = 0,
    ) -> tuple[str, str | None]:
        return _call_gemini(
            prompt,
            quota_tracker=quota_tracker,
            json_mode=True,
            on_quota_update=on_quota_update,
            estimated_input_tokens=estimated_input_tokens,
            context_label=context_label,
            model=model,
        )

    report(0, 1, tr("gemini_progress.counting_tokens"))
    token_count = count_tokens(content.full_text, "texte_integral", model=model)
    leftovers: list[str] = []

    if token_count <= get_model_info(model).max_tokens_per_request:
        # Cas le plus courant : le texte tient sous la limite de débit par minute
        # (TPM) du palier gratuit, donc les deux résumés, personnages et analyse
        # sont demandés en une seule requête pour limiter la consommation de quota.
        _log_api_call(f"generation MODE une_seule_requete tokens_texte={token_count}")
        report(0, 1, tr("gemini_progress.single_request", token_count=token_count))
        raw_text, finish_reason = call(
            _full_report_prompt(content, use_custom_prompts, profile_id), "full_report", estimated_input_tokens=token_count
        )
        summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
            raw_text, finish_reason=finish_reason
        )
        if leftover:
            leftovers.append(leftover)
        was_split = False
        chapter_count = 1
    else:
        # Livre trop volumineux pour tenir dans une seule requête : le livre est
        # réparti en lots de chapitres consécutifs (un lot regroupe autant de
        # chapitres que la fenêtre de contexte du modèle le permet, pour
        # limiter le nombre de requêtes envoyées - le quota quotidien du
        # palier gratuit est très serré, 20 requêtes/jour par défaut). Chaque
        # lot est résumé séparément, puis UNE SEULE requête finale reçoit tous
        # les résumés de chapitre et produit le résumé court, le résumé
        # détaillé, les personnages et l'analyse littéraire.
        batches = _split_chapters_into_batches(content.chapters, model=model)

        if len(batches) == 1 and not resume_chapter_summaries:
            # count_tokens(full_text) mesuré en bloc peut dépasser
            # max_tokens_per_request (ModelInfo) alors que la somme des
            # comptages par chapitre (utilisée par
            # _split_chapters_into_batches) tient en un seul lot : écart de
            # mesure du tokenizer, pas un vrai dépassement (max_tokens_per_request
            # garde 50 000 tokens de marge sous la vraie limite TPM de 250 000,
            # largement suffisant pour absorber cet écart et les instructions
            # ajoutées par le prompt full_report).
            # Dans ce cas, traiter comme le mode une seule requête plutôt que
            # d'enchaîner résumé-de-lot puis consolidation : même résultat,
            # une requête Gemini économisée sur un quota quotidien serré.
            _log_api_call(f"generation MODE une_seule_requete_lot_unique tokens_texte={token_count}")
            report(0, 1, tr("gemini_progress.single_request", token_count=token_count))
            raw_text, finish_reason = call(
                _full_report_prompt(content, use_custom_prompts, profile_id), "full_report", estimated_input_tokens=token_count
            )
            summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
                raw_text, finish_reason=finish_reason
            )
            if leftover:
                leftovers.append(leftover)
            was_split = False
            chapter_count = 1
            report(1, 1, tr("gemini_progress.done"))
            return BookReport(
                book_title=content.book_title,
                author=content.author,
                summary_text=summary_text,
                detailed_summary_text=detailed_summary_text,
                characters=characters,
                extra_generated_text="\n\n---\n\n".join(leftovers),
                analysis_text=analysis_text,
                cover_image=content.cover_image,
                was_split=was_split,
                chapter_count=chapter_count,
            )

        _log_api_call(
            f"generation MODE decoupage_en_lots lots={len(batches)} "
            f"chapitres={len(content.chapters)} tokens_texte={token_count} "
            f"requetes_generation_attendues={len(batches) + 1}"
        )
        total_steps = len(batches) + 1
        report(
            0,
            total_steps,
            tr(
                "gemini_progress.book_too_large",
                token_count=token_count,
                batch_count=len(batches),
                chapter_count=len(content.chapters),
            ),
        )

        chapter_summaries: list[tuple[str, str]] = list(resume_chapter_summaries or [])
        start_index = min(resume_batches_done, len(batches))
        for i, (batch, batch_tokens) in enumerate(batches[start_index:], start=start_index + 1):
            report(i - 1, total_steps, tr("gemini_progress.summarizing_batch", current=i, total=len(batches)))
            try:
                raw_text, finish_reason = call(
                    _chapter_summary_prompt(content.book_title, content.author, batch, use_custom_prompts, profile_id),
                    f"chapter_summary_batch_{i}/{len(batches)}",
                    estimated_input_tokens=batch_tokens,
                )
                batch_summaries, leftover = _parse_chapter_summaries_batch_json(
                    raw_text, batch, finish_reason=finish_reason
                )
            except GeminiError as exc:
                if chapter_summaries:
                    raise PartialGenerationError(
                        str(exc),
                        chapter_summaries,
                        batches_done=i - 1,
                        batches_total=len(batches),
                        error_kind=exc.error_kind,
                    ) from exc
                raise
            if leftover:
                leftovers.append(leftover)
            chapter_summaries.extend(batch_summaries)
            report(i, total_steps, tr("gemini_progress.batch_summarized", current=i, total=len(batches)))

        report(len(batches), total_steps, tr("gemini_progress.merging_summaries"))
        consolidation_prompt = _consolidation_prompt(
            content.book_title, content.author, chapter_summaries, use_custom_prompts, profile_id
        )
        try:
            raw_text, finish_reason = call(
                consolidation_prompt,
                "consolidation",
                estimated_input_tokens=count_tokens(consolidation_prompt, "consolidation", model=model),
            )
            summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
                raw_text, "consolidation", finish_reason=finish_reason
            )
        except GeminiError as exc:
            raise PartialGenerationError(
                str(exc),
                chapter_summaries,
                batches_done=len(batches),
                batches_total=len(batches),
                error_kind=exc.error_kind,
            ) from exc
        if leftover:
            leftovers.append(leftover)

        was_split = True
        chapter_count = len(content.chapters)

    report(1, 1, tr("gemini_progress.done"))

    return BookReport(
        book_title=content.book_title,
        author=content.author,
        summary_text=summary_text,
        detailed_summary_text=detailed_summary_text,
        characters=characters,
        extra_generated_text="\n\n---\n\n".join(leftovers),
        analysis_text=analysis_text,
        cover_image=content.cover_image,
        was_split=was_split,
        chapter_count=chapter_count,
    )
