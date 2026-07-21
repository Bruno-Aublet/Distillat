"""Client Gemini : comptage de tokens, résumé/personnages/analyse, sans retry."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import google.generativeai as genai
from google.api_core.exceptions import (
    DeadlineExceeded,
    GoogleAPIError,
    InternalServerError,
    PermissionDenied,
    ResourceExhausted,
    ServiceUnavailable,
    Unauthenticated,
)

from app import config
from app import i18n
from app.book_report import BookReport, Character
from app.epub_parser import Chapter, BookContent
from app.i18n import tr
from app.prompts_store import load_custom_prompts
from app.quota_tracker import QuotaSnapshot, QuotaTracker

# gemini-2.5-flash a été retiré pour les nouvelles clés API en juillet 2026,
# avant sa date de dépréciation officielle annoncée. gemini-3.5-flash est le
# modèle Flash generally available (non-preview) qui lui succède.
MODEL_NAME = "gemini-3.5-flash"

# Marge de sécurité : on vise à rester sous la fenêtre de contexte du modèle
# pour laisser de la place au prompt système et à la réponse.
MAX_INPUT_TOKENS = 900_000

# Limite de débit du palier gratuit (tokens par minute), constatée sur
# https://aistudio.google.com/rate-limit pour gemini-3.5-flash (250 000 au
# 19/07/2026, cf. app/quota_tracker.py). C'est elle, et non MAX_INPUT_TOKENS
# (la fenêtre de contexte du modèle, bien plus large), qui doit dimensionner
# une requête envoyée en une fois : une requête de plusieurs centaines de
# milliers de tokens tient dans le modèle mais sature le TPM à elle seule et
# déclenche une erreur 429, même si c'est la toute première requête envoyée.
# Marge de sécurité sous la vraie limite (250 000) pour absorber l'écart
# entre le tokenizer local (count_tokens) et le décompte serveur exact.
MAX_TOKENS_PER_REQUEST = 200_000

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


def _extract_quota_blocked_info(exc: ResourceExhausted) -> QuotaBlockedInfo:
    quota_id: str | None = None
    retry_after_seconds: float | None = None
    try:
        for detail in exc.details:
            violations = getattr(detail, "violations", None)
            if violations:
                quota_id = violations[0].quota_id or violations[0].quota_metric or None
            retry_delay = getattr(detail, "retry_delay", None)
            if retry_delay is not None and (retry_delay.seconds or retry_delay.nanos):
                retry_after_seconds = retry_delay.seconds + retry_delay.nanos / 1e9
    except (AttributeError, IndexError, TypeError):
        pass
    return QuotaBlockedInfo(quota_id=quota_id, retry_after_seconds=retry_after_seconds)


def _is_daily_quota(quota_id: str | None) -> bool:
    """Un quota quotidien (RPD) ne se réinitialise qu'à minuit heure du
    Pacifique (Californie), pas à minuit heure locale : retenter dans les
    secondes/minutes qui suivent est voué à l'échec, contrairement à un quota
    par minute (RPM/TPM) qui se libère naturellement en attendant."""
    return bool(quota_id) and "perday" in quota_id.lower()


def _http_status(exc: GoogleAPIError) -> int | None:
    return getattr(exc, "code", None) or getattr(exc, "grpc_status_code", None)


def _friendly_error_message(exc: Exception) -> tuple[str, str | None]:
    """Traduit une exception technique de l'API Gemini en message compréhensible
    dans la langue actuellement choisie par l'utilisateur, avec le code d'erreur
    d'origine entre parenthèses pour le diagnostic (support, recherche en
    ligne...). Retourne aussi error_kind ("daily_quota"/"rate_quota"/None),
    indépendant de la langue du message : c'est sur cette valeur, et non sur le
    texte traduit, que l'appelant (main_window) doit se baser pour adapter son
    comportement (ex : proposer une reprise)."""
    if isinstance(exc, ResourceExhausted):
        blocked_info = _extract_quota_blocked_info(exc)
        if _is_daily_quota(blocked_info.quota_id):
            return tr("gemini_errors.daily_quota_exceeded"), "daily_quota"
        return tr("gemini_errors.rate_quota_exceeded"), "rate_quota"
    if isinstance(exc, ServiceUnavailable):
        return tr("gemini_errors.service_unavailable"), None
    if isinstance(exc, InternalServerError):
        return tr("gemini_errors.internal_server_error"), None
    if isinstance(exc, DeadlineExceeded):
        return tr("gemini_errors.deadline_exceeded"), None
    if isinstance(exc, (PermissionDenied, Unauthenticated)):
        return tr("gemini_errors.invalid_api_key"), None
    status = _http_status(exc) if isinstance(exc, GoogleAPIError) else None
    status_part = f" ({tr('gemini_errors.error_code', code=status)})" if status else ""
    return tr("gemini_errors.generic_api_error", status_part=status_part, error=exc), None


def configure(api_key: str) -> None:
    genai.configure(api_key=api_key)


def _get_model() -> genai.GenerativeModel:
    return genai.GenerativeModel(MODEL_NAME)


def _get_json_model() -> genai.GenerativeModel:
    """Modèle configuré pour forcer une sortie JSON syntaxiquement valide côté
    API (mode JSON natif de Gemini), plutôt que de compter uniquement sur la
    consigne du prompt : réduit fortement le risque de réponse malformée
    (guillemet non échappé, virgule manquante...) qui faisait échouer le
    parsing côté application, sans possibilité de retenter automatiquement."""
    return genai.GenerativeModel(
        MODEL_NAME, generation_config=genai.GenerationConfig(response_mime_type="application/json")
    )


def count_tokens(text: str, context_label: str = "") -> int:
    """Appel réseau CountTokens (gratuit, quota séparé de generateContent,
    voir ARCHITECTURE.md). Journalisé comme les appels de génération pour
    pouvoir corréler chaque appel réseau de l'application avec le dashboard
    AI Studio en cas d'écart de compteur."""
    model = _get_model()
    start = time.monotonic()
    _api_call_totals["count_tokens"] += 1
    try:
        result = model.count_tokens(text)
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


def _split_chapters_into_batches(chapters: list[Chapter]) -> list[tuple[list[Chapter], int]]:
    """Regroupe les chapitres en lots dont le texte cumulé tient sous
    MAX_TOKENS_PER_REQUEST, la limite de débit par minute du palier gratuit
    (et non MAX_INPUT_TOKENS, la fenêtre de contexte du modèle, bien plus
    large - un lot dimensionné sur cette dernière saturerait le quota TPM à
    lui seul). Un chapitre dont le texte dépasse à lui seul
    MAX_TOKENS_PER_REQUEST forme son propre lot (l'appel Gemini correspondant
    échouera probablement, mais ce n'est pas à cette fonction de tronquer le
    contenu du livre).

    Chaque lot est retourné avec le compte de tokens de son texte source déjà
    calculé ici (estimation du prompt final, qui ajoute aussi les consignes du
    template) : réutilisé tel quel par l'appelant pour créditer le suivi de
    quota si l'appel Gemini correspondant échoue, sans refaire d'appel
    count_tokens (donc sans appel réseau) juste pour ça."""
    batches: list[tuple[list[Chapter], int]] = []
    current_batch: list[Chapter] = []
    current_tokens = 0

    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_tokens = count_tokens(chapter.text, f"decoupage_chapitre_{chapter_index}/{len(chapters)}")
        if current_batch and current_tokens + chapter_tokens > MAX_TOKENS_PER_REQUEST:
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


def _log_api_call(message: str) -> None:
    """Journal d'appels API : une ligne horodatée par événement (envoi,
    succès, échec de chaque appel réseau à Gemini, y compris les comptages de
    tokens), en append dans debug_logs/api_requests.log. Ajouté le 2026-07-21
    pour diagnostiquer un écart inexpliqué entre le compteur local de
    requêtes quotidiennes et celui du dashboard AI Studio (le dashboard
    comptait environ le double) : ce journal donne la liste exacte et datée
    de ce que l'application a réellement envoyé, à comparer avec le
    dashboard. Écriture best-effort : ne doit jamais faire échouer un appel
    ni la génération."""
    try:
        log_path = config.get_debug_logs_dir() / "api_requests.log"
        timestamp = datetime.now().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def _call_gemini(
    model: genai.GenerativeModel,
    prompt: str,
    quota_tracker: QuotaTracker,
    on_quota_update: QuotaCallback | None = None,
    estimated_input_tokens: int = 0,
    context_label: str = "",
) -> str:
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
            # request_options={"retry": None} désactive le retry automatique
            # intégré à la bibliothèque google-generativeai (par défaut :
            # nouvelles tentatives silencieuses sur 503 ServiceUnavailable,
            # backoff 1 à 10 s, pendant jusqu'à 10 min - voir
            # generative_service/transports/base.py du paquet installé).
            # Chaque tentative supplémentaire était une vraie requête comptée
            # par Google (RPM/RPD) mais invisible pour l'application, donc
            # impossible à suivre localement (piste découverte le 2026-07-21
            # en cherchant un écart entre compteur local et dashboard). Sans
            # lui, un appel applicatif = exactement une requête serveur,
            # conformément au choix "sans retry automatique" ci-dessus.
            response = model.generate_content(prompt, request_options={"retry": None})
            usage = response.usage_metadata
            snapshot = quota_tracker.record_call(
                input_tokens=usage.prompt_token_count,
                output_tokens=usage.candidates_token_count,
            )
            _log_api_call(
                f"generate_content OK contexte={context_label} tokens_entree={usage.prompt_token_count} "
                f"tokens_sortie={usage.candidates_token_count} duree={time.monotonic() - start:.1f}s "
                f"requetes_jour={snapshot.requests_today}"
            )
            try:
                text = response.text
            except ValueError as exc:
                # Levée par la bibliothèque quand aucun candidat exploitable
                # n'est retourné, notamment si les filtres de sécurité de
                # Gemini ont bloqué la réponse : sans ce cas, l'utilisateur
                # voyait un message technique brut au lieu d'une explication.
                raise GeminiError(tr("gemini_errors.blocked_by_safety_filters")) from exc
            if not text:
                raise GeminiError(tr("gemini_errors.empty_response"))
            return text
        except (ResourceExhausted, ServiceUnavailable, InternalServerError, DeadlineExceeded, PermissionDenied, Unauthenticated) as exc:
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
suivants :

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
4. Une ANALYSE littéraire d'au moins 600 à 900 mots, structurée en plusieurs paragraphes distincts \
couvrant les thèmes principaux, le style d'écriture et la construction narrative, puis la portée \
de l'œuvre - développée et argumentée, sans répéter le contenu déjà couvert par les résumés.

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact :
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

Réponds uniquement avec l'objet JSON."""


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

À partir de ces résumés partiels, TOUJOURS EN FRANÇAIS, produis les quatre éléments suivants :

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
4. Une ANALYSE littéraire d'au moins 600 à 900 mots, structurée en plusieurs paragraphes distincts \
couvrant les thèmes principaux, le style d'écriture et la construction narrative, puis la portée \
de l'œuvre - développée et argumentée, sans répéter le contenu déjà couvert par les résumés.

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact :
{{
  "summary": "Le résumé court ici, en français...",
  "detailed_summary": "Le résumé détaillé et développé ici, en français...",
  "characters": [{{"name": "Nom", "description": "Description en français..."}}, ...],
  "analysis": "L'analyse littéraire ici, en français..."
}}

Réponds uniquement avec l'objet JSON."""


DEFAULT_FULL_REPORT_PROMPT_EN = """You are an expert literary assistant. Here is the full text of a book \
titled "{book_title}" by {author}.

Produce ALWAYS IN ENGLISH, regardless of the original language of the text, the following four \
elements:

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
4. A literary ANALYSIS of at least 600 to 900 words, structured in several distinct paragraphs \
covering the main themes, the writing style and narrative construction, then the work's significance \
- developed and well-argued, without repeating content already covered by the summaries.

Respond STRICTLY with a valid JSON object, with no text whatsoever before or after, no markdown \
fences, in the exact format:
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

Respond only with the JSON object."""


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

From these partial summaries, ALWAYS IN ENGLISH, produce the following four elements:

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
4. A literary ANALYSIS of at least 600 to 900 words, structured in several distinct paragraphs \
covering the main themes, the writing style and narrative construction, then the work's significance \
- developed and well-argued, without repeating content already covered by the summaries.

Respond STRICTLY with a valid JSON object, with no text whatsoever before or after, no markdown \
fences, in the exact format:
{{
  "summary": "The short summary here, in English...",
  "detailed_summary": "The detailed, developed summary here, in English...",
  "characters": [{{"name": "Name", "description": "Description in English..."}}, ...],
  "analysis": "The literary analysis here, in English..."
}}

Respond only with the JSON object."""


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


def _get_prompt_template(key: str, use_custom_prompts: bool) -> str:
    """Renvoie le template personnalisé par l'utilisateur pour ce prompt et
    cette langue s'il existe, sinon le template par défaut de la langue
    actuellement choisie (voir default_prompt_templates). Les personnalisations
    sont propres à chaque langue : celles du français n'affectent jamais
    l'anglais, et inversement."""
    if use_custom_prompts:
        custom = load_custom_prompts(i18n.current_language())
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


def _full_report_prompt(content: BookContent, use_custom_prompts: bool) -> str:
    """Un seul prompt demandant les deux résumés + personnages + analyse en une
    requête, pour un livre dont le texte tient dans la fenêtre de contexte du modèle."""
    template = _get_prompt_template("full_report", use_custom_prompts)
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


def _chapter_summary_prompt(book_title: str, author: str, batch: list[Chapter], use_custom_prompts: bool) -> str:
    """Prompt de résumé appliqué à un LOT de chapitres consécutifs (voir
    _split_chapters_into_batches) plutôt qu'à un seul, pour limiter le nombre
    de requêtes envoyées à l'API sur le palier gratuit (quota quotidien très
    serré : 20 requêtes/jour par défaut)."""
    template = _get_prompt_template("chapter_summary", use_custom_prompts)
    return _format_prompt_template(
        template,
        tr("prompts_dialog.tabs.chapter_summary.title"),
        book_title=book_title,
        author=author,
        chapters_text=_chapters_batch_text(batch),
    )


def _consolidation_prompt(
    book_title: str, author: str, chapter_summaries: list[tuple[str, str]], use_custom_prompts: bool
) -> str:
    # Un chapitre au résumé vide (page sans contenu narratif, voir
    # _parse_chapter_summaries_batch_json) n'a rien à apporter à la
    # consolidation : l'inclure enverrait un titre suivi de rien à Gemini.
    joined = "\n\n".join(f"### {title}\n{summary}" for title, summary in chapter_summaries if summary)
    template = _get_prompt_template("consolidation", use_custom_prompts)
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
    remplit pas ce critère et n'est donc jamais réparé silencieusement."""
    if not suffix or len(suffix) > _STUTTER_MAX_SUFFIX_LENGTH:
        return False

    tail = accepted_text[-_STUTTER_TAIL_WINDOW:]
    normalized_tail = _normalize_for_stutter_check(tail)
    normalized_suffix = _normalize_for_stutter_check(suffix)

    if not normalized_suffix:
        # Le suffixe ne contenait que de la ponctuation/espaces/accolades :
        # rien qui ressemble à du vrai contenu nouveau.
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
    except OSError:
        pass


def _parse_json_object(raw_text: str, context_label: str = "unknown") -> tuple[dict, str]:
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
    dans le nom du fichier de log."""
    text = _strip_json_fences(raw_text)
    try:
        obj, end_index = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        repaired = _try_repair_stuttered_json(text)
        if repaired is not None:
            return repaired
        _log_unparsable_response(raw_text, context_label, exc)
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


def _parse_full_report_json(raw_text: str, context_label: str = "full_report") -> tuple[str, str, list[Character], str, str]:
    """Parse la réponse combinée résumé + personnages + analyse. Le 4e élément
    retourné est le texte ignoré après le premier objet JSON (vide la plupart
    du temps). context_label distingue, dans le log de diagnostic en cas
    d'échec, l'appel "rapport complet" (livre tenant en une requête) de
    l'appel "consolidation" (dernière requête d'un livre découpé en lots) :
    même fonction de parsing, deux contextes d'appel différents."""
    data, leftover = _parse_json_object(raw_text, context_label)
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


def _parse_chapter_summaries_batch_json(raw_text: str, batch: list[Chapter]) -> tuple[list[tuple[str, str]], str]:
    """Parse la réponse d'un lot de résumés de chapitre. Associe chaque résumé
    au titre du chapitre correspondant dans `batch` par position (et non par
    correspondance exacte du titre renvoyé par Gemini, qui peut légèrement
    différer de l'original) : le nombre d'entrées attendu est connu à
    l'avance, contrairement au cas des personnages où Gemini choisit lui-même
    combien d'entrées produire."""
    data, leftover = _parse_json_object(raw_text, "chapter_summary_batch")
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
    resume_chapter_summaries: list[tuple[str, str]] | None = None,
    resume_batches_done: int = 0,
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
    _log_api_call(
        f"generation DEBUT livre={_one_line(content.book_title)} modele={MODEL_NAME} "
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
            resume_chapter_summaries=resume_chapter_summaries,
            resume_batches_done=resume_batches_done,
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
    resume_chapter_summaries: list[tuple[str, str]] | None = None,
    resume_batches_done: int = 0,
) -> BookReport:
    def report(done: int, total: int, message: str) -> None:
        if on_progress:
            on_progress(done, total, message)

    json_model = _get_json_model()

    def call(
        prompt: str,
        context_label: str,
        model: genai.GenerativeModel = json_model,
        estimated_input_tokens: int = 0,
    ) -> str:
        return _call_gemini(
            model,
            prompt,
            quota_tracker=quota_tracker,
            on_quota_update=on_quota_update,
            estimated_input_tokens=estimated_input_tokens,
            context_label=context_label,
        )

    report(0, 1, tr("gemini_progress.counting_tokens"))
    token_count = count_tokens(content.full_text, "texte_integral")
    leftovers: list[str] = []

    if token_count <= MAX_TOKENS_PER_REQUEST:
        # Cas le plus courant : le texte tient sous la limite de débit par minute
        # (TPM) du palier gratuit, donc les deux résumés, personnages et analyse
        # sont demandés en une seule requête pour limiter la consommation de quota.
        _log_api_call(f"generation MODE une_seule_requete tokens_texte={token_count}")
        report(0, 1, tr("gemini_progress.single_request", token_count=token_count))
        summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
            call(_full_report_prompt(content, use_custom_prompts), "full_report", estimated_input_tokens=token_count)
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
        batches = _split_chapters_into_batches(content.chapters)
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
                batch_summaries, leftover = _parse_chapter_summaries_batch_json(
                    call(
                        _chapter_summary_prompt(content.book_title, content.author, batch, use_custom_prompts),
                        f"chapter_summary_batch_{i}/{len(batches)}",
                        estimated_input_tokens=batch_tokens,
                    ),
                    batch,
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
        consolidation_prompt = _consolidation_prompt(content.book_title, content.author, chapter_summaries, use_custom_prompts)
        try:
            summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
                call(
                    consolidation_prompt,
                    "consolidation",
                    estimated_input_tokens=count_tokens(consolidation_prompt, "consolidation"),
                ),
                "consolidation",
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
