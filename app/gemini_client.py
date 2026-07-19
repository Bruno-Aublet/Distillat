"""Client Gemini : comptage de tokens, résumé/personnages/analyse, avec retry."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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

from app.book_report import BookReport, Character
from app.epub_parser import Chapter, BookContent
from app.prompts_store import load_custom_prompts
from app.quota_tracker import QuotaSnapshot, QuotaTracker

# gemini-2.5-flash a été retiré pour les nouvelles clés API en juillet 2026,
# avant sa date de dépréciation officielle annoncée. gemini-3.5-flash est le
# modèle Flash generally available (non-preview) qui lui succède.
MODEL_NAME = "gemini-3.5-flash"

# Marge de sécurité : on vise à rester sous la fenêtre de contexte du modèle
# pour laisser de la place au prompt système et à la réponse.
MAX_INPUT_TOKENS = 900_000

MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 5

ProgressCallback = Callable[[int, int, str], None]
QuotaCallback = Callable[[QuotaSnapshot], None]
RetryWaitCallback = Callable[[float, str], None]  # secondes d'attente, nom du quota (ou "")


class GeminiError(Exception):
    pass


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
    """Un quota journalier (RPD) ne se réinitialise qu'à minuit : retenter
    dans les secondes/minutes qui suivent est voué à l'échec, contrairement à
    un quota par minute (RPM/TPM) qui se libère naturellement en attendant."""
    return bool(quota_id) and "perday" in quota_id.lower()


def _http_status(exc: GoogleAPIError) -> int | None:
    return getattr(exc, "code", None) or getattr(exc, "grpc_status_code", None)


def _friendly_error_message(exc: Exception) -> str:
    """Traduit une exception technique de l'API Gemini en message compréhensible
    en français, avec le code d'erreur d'origine entre parenthèses pour le
    diagnostic (support, recherche en ligne...)."""
    if isinstance(exc, ResourceExhausted):
        blocked_info = _extract_quota_blocked_info(exc)
        if _is_daily_quota(blocked_info.quota_id):
            return (
                "Le quota journalier de requêtes Gemini est épuisé pour aujourd'hui. "
                "Il se réinitialise à minuit. (erreur 429 : quota journalier dépassé)"
            )
        return (
            "Le quota Gemini (requêtes ou tokens par minute) est temporairement dépassé. "
            "Nouvelle tentative automatique en cours. (erreur 429 : quota par minute dépassé)"
        )
    if isinstance(exc, ServiceUnavailable):
        return (
            "Le service Gemini est temporairement indisponible. "
            "Nouvelle tentative automatique en cours. (erreur 503)"
        )
    if isinstance(exc, InternalServerError):
        return (
            "Le service Gemini a rencontré une erreur interne. "
            "Nouvelle tentative automatique en cours. (erreur 500)"
        )
    if isinstance(exc, DeadlineExceeded):
        return (
            "Le service Gemini a mis trop de temps à répondre. "
            "Nouvelle tentative automatique en cours. (erreur 504)"
        )
    if isinstance(exc, (PermissionDenied, Unauthenticated)):
        return (
            "La clé API Gemini est invalide, expirée ou ne dispose pas des autorisations "
            "nécessaires. Vérifiez-la via le bouton Clé API. (erreur 401/403)"
        )
    status = _http_status(exc) if isinstance(exc, GoogleAPIError) else None
    status_part = f" (erreur {status})" if status else ""
    return f"Une erreur est survenue lors de la communication avec l'API Gemini{status_part} : {exc}"


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


def count_tokens(text: str) -> int:
    model = _get_model()
    result = model.count_tokens(text)
    return result.total_tokens


def _split_chapters_into_batches(chapters: list[Chapter]) -> list[list[Chapter]]:
    """Regroupe les chapitres en lots dont le texte cumulé tient sous
    MAX_INPUT_TOKENS, pour un livre si volumineux que même le texte intégral
    dépasse la fenêtre de contexte du modèle (cas rare, un roman très long).
    Un chapitre dont le texte dépasse à lui seul MAX_INPUT_TOKENS forme son
    propre lot (l'appel Gemini correspondant échouera probablement, mais ce
    n'est pas à cette fonction de tronquer le contenu du livre)."""
    batches: list[list[Chapter]] = []
    current_batch: list[Chapter] = []
    current_tokens = 0

    for chapter in chapters:
        chapter_tokens = count_tokens(chapter.text)
        if current_batch and current_tokens + chapter_tokens > MAX_INPUT_TOKENS:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(chapter)
        current_tokens += chapter_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _call_with_retry(
    model: genai.GenerativeModel,
    prompt: str,
    quota_tracker: QuotaTracker,
    on_retry: Callable[[int, float, QuotaBlockedInfo | None], None] | None = None,
    on_quota_update: QuotaCallback | None = None,
) -> str:
    backoff = INITIAL_BACKOFF_SECONDS
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = model.generate_content(prompt)
            usage = response.usage_metadata
            snapshot = quota_tracker.record_call(
                input_tokens=usage.prompt_token_count,
                output_tokens=usage.candidates_token_count,
            )
            if on_quota_update:
                on_quota_update(snapshot)
            try:
                text = response.text
            except ValueError as exc:
                # Levée par la bibliothèque quand aucun candidat exploitable
                # n'est retourné, notamment si les filtres de sécurité de
                # Gemini ont bloqué la réponse : sans ce cas, l'utilisateur
                # voyait un message technique brut au lieu d'une explication.
                raise GeminiError(
                    "Gemini n'a pas produit de réponse exploitable, probablement bloquée par ses "
                    "filtres de sécurité (contenu du livre jugé sensible). Nouvelle tentative "
                    "impossible pour cette requête."
                ) from exc
            if not text:
                raise GeminiError("Réponse vide reçue de l'API Gemini.")
            return text
        except ResourceExhausted as exc:
            blocked_info = _extract_quota_blocked_info(exc)
            if _is_daily_quota(blocked_info.quota_id):
                # Un quota journalier ne se libère qu'à minuit : retenter dans
                # la minute qui suit ne peut qu'échouer, autant s'arrêter
                # immédiatement plutôt que de gaspiller MAX_RETRIES tentatives.
                raise GeminiError(_friendly_error_message(exc)) from exc
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            if on_retry:
                wait_seconds = blocked_info.retry_after_seconds or backoff
                on_retry(attempt, wait_seconds, blocked_info)
                time.sleep(wait_seconds)
            else:
                time.sleep(backoff)
            backoff *= 2
        except (ServiceUnavailable, InternalServerError, DeadlineExceeded) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            if on_retry:
                on_retry(attempt, backoff, None)
            time.sleep(backoff)
            backoff *= 2
        except (PermissionDenied, Unauthenticated) as exc:
            # Une clé API invalide échouera de la même façon à chaque essai :
            # inutile de retenter.
            raise GeminiError(_friendly_error_message(exc)) from exc

    raise GeminiError(_friendly_error_message(last_error))


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


DEFAULT_PROMPT_TEMPLATES = {
    "full_report": DEFAULT_FULL_REPORT_PROMPT,
    "chapter_summary": DEFAULT_CHAPTER_SUMMARY_PROMPT,
    "consolidation": DEFAULT_CONSOLIDATION_PROMPT,
}


def _get_prompt_template(key: str, settings_dir: Path | None) -> str:
    """Renvoie le template personnalisé par l'utilisateur pour ce prompt s'il
    existe, sinon le template par défaut."""
    if settings_dir is not None:
        custom = load_custom_prompts(settings_dir)
        if key in custom:
            return custom[key]
    return DEFAULT_PROMPT_TEMPLATES[key]


def _format_prompt_template(template: str, prompt_label: str, **kwargs: str) -> str:
    """Remplit un template de prompt, avec un message clair si un repère entre
    accolades a été mal orthographié lors d'une personnalisation via le
    bouton Prompts (KeyError sinon peu explicite : juste le nom du repère,
    ex. "'full_textt'")."""
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        raise GeminiError(
            f"Le prompt personnalisé « {prompt_label} » contient une erreur : le repère {exc} n'est "
            "pas reconnu. Vérifiez son orthographe dans la fenêtre Prompts (bouton « Réinitialiser ce "
            "prompt » si besoin)."
        ) from exc


def _full_report_prompt(content: BookContent, settings_dir: Path | None) -> str:
    """Un seul prompt demandant les deux résumés + personnages + analyse en une
    requête, pour un livre dont le texte tient dans la fenêtre de contexte du modèle."""
    template = _get_prompt_template("full_report", settings_dir)
    return _format_prompt_template(
        template,
        "Résumé + personnages + analyse",
        book_title=content.book_title,
        author=content.author,
        full_text=content.full_text,
    )


def _chapters_batch_text(chapters: list[Chapter]) -> str:
    return "\n\n".join(f"[[[TITRE: {chapter.title}]]]\n{chapter.text}" for chapter in chapters)


def _chapter_summary_prompt(book_title: str, author: str, batch: list[Chapter], settings_dir: Path | None) -> str:
    """Prompt de résumé appliqué à un LOT de chapitres consécutifs (voir
    _split_chapters_into_batches) plutôt qu'à un seul, pour limiter le nombre
    de requêtes envoyées à l'API sur le palier gratuit (quota journalier très
    serré : 20 requêtes/jour par défaut)."""
    template = _get_prompt_template("chapter_summary", settings_dir)
    return _format_prompt_template(
        template,
        "Résumé d'un lot de chapitres",
        book_title=book_title,
        author=author,
        chapters_text=_chapters_batch_text(batch),
    )


def _consolidation_prompt(
    book_title: str, author: str, chapter_summaries: list[tuple[str, str]], settings_dir: Path | None
) -> str:
    joined = "\n\n".join(f"### {title}\n{summary}" for title, summary in chapter_summaries)
    template = _get_prompt_template("consolidation", settings_dir)
    return _format_prompt_template(
        template,
        "Fusion résumé + personnages + analyse",
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


def _parse_json_object(raw_text: str) -> tuple[dict, str]:
    """Parse le premier objet JSON de la réponse. Même en mode JSON natif de
    l'API, Gemini produit parfois du contenu superflu après un premier objet
    par ailleurs valide (ex : un second objet accolé) ; json.loads() rejette
    ce cas ("Extra data") alors que le contenu utile est bien présent et
    exploitable. Le texte en trop est retourné (au lieu d'être jeté) : il
    peut s'agir de contenu légitime que l'utilisateur voudra récupérer à la
    main, ce n'est pas à l'application de décider silencieusement qu'il ne
    sert à rien."""
    text = _strip_json_fences(raw_text)
    try:
        obj, end_index = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Réponse de Gemini illisible (format inattendu) : {exc}") from exc
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


def _parse_full_report_json(raw_text: str) -> tuple[str, str, list[Character], str, str]:
    """Parse la réponse combinée résumé + personnages + analyse. Le 4e élément
    retourné est le texte ignoré après le premier objet JSON (vide la plupart
    du temps)."""
    data, leftover = _parse_json_object(raw_text)
    if not isinstance(data, dict):
        # Le mode JSON natif de Gemini garantit une syntaxe JSON valide, mais
        # pas que la racine soit un objet conforme au schéma demandé (ex : une
        # liste accolée) : sans ce contrôle, l'utilisateur voyait une erreur
        # technique brute ("'list' object has no attribute 'get'").
        raise GeminiError("La réponse de Gemini n'a pas la forme attendue (racine JSON non exploitable).")

    summary = _normalize_dashes(str(data.get("summary", "")).strip())
    detailed_summary = _normalize_dashes(str(data.get("detailed_summary", "")).strip())
    analysis = _normalize_dashes(str(data.get("analysis", "")).strip())
    characters = _parse_characters_list(data.get("characters", []))

    if not summary:
        raise GeminiError("La réponse de Gemini ne contient pas de résumé exploitable.")

    return summary, detailed_summary, characters, analysis, leftover


def _parse_chapter_summaries_batch_json(raw_text: str, batch: list[Chapter]) -> tuple[list[tuple[str, str]], str]:
    """Parse la réponse d'un lot de résumés de chapitre. Associe chaque résumé
    au titre du chapitre correspondant dans `batch` par position (et non par
    correspondance exacte du titre renvoyé par Gemini, qui peut légèrement
    différer de l'original) : le nombre d'entrées attendu est connu à
    l'avance, contrairement au cas des personnages où Gemini choisit lui-même
    combien d'entrées produire."""
    data, leftover = _parse_json_object(raw_text)
    raw_summaries = data.get("chapter_summaries", [])

    summaries: list[tuple[str, str]] = []
    for i, chapter in enumerate(batch):
        if i < len(raw_summaries) and isinstance(raw_summaries[i], dict):
            summary = _normalize_dashes(str(raw_summaries[i].get("summary", "")).strip())
        else:
            summary = ""
        if not summary:
            raise GeminiError(
                f"La réponse de Gemini ne contient pas de résumé exploitable pour le chapitre "
                f'"{chapter.title}".'
            )
        summaries.append((chapter.title, summary))

    return summaries, leftover


def generate_book_report(
    content: BookContent,
    quota_tracker: QuotaTracker,
    on_progress: ProgressCallback | None = None,
    on_quota_update: QuotaCallback | None = None,
    on_retry_wait: RetryWaitCallback | None = None,
    settings_dir: Path | None = None,
) -> BookReport:
    """Génère à la suite le résumé, les fiches personnages et l'analyse littéraire
    du livre. Le résumé est produit directement si le livre tient dans une seule
    requête, ou par découpage en chapitres puis consolidation sinon."""

    def report(done: int, total: int, message: str) -> None:
        if on_progress:
            on_progress(done, total, message)

    json_model = _get_json_model()

    def retry_notice(attempt: int, wait_seconds: float, blocked_info: QuotaBlockedInfo | None) -> None:
        quota_id = blocked_info.quota_id if blocked_info and blocked_info.quota_id else ""
        quota_part = f" (quota : {quota_id})" if quota_id else ""
        report(
            0,
            1,
            f"Quota atteint{quota_part}, nouvelle tentative dans {wait_seconds:.0f}s "
            f"(essai {attempt}/{MAX_RETRIES})…",
        )
        if on_retry_wait:
            on_retry_wait(wait_seconds, quota_id)

    def call(prompt: str, model: genai.GenerativeModel = json_model) -> str:
        return _call_with_retry(
            model,
            prompt,
            quota_tracker=quota_tracker,
            on_retry=retry_notice,
            on_quota_update=on_quota_update,
        )

    report(0, 1, "Comptage des tokens du texte extrait…")
    token_count = count_tokens(content.full_text)
    leftovers: list[str] = []

    if token_count <= MAX_INPUT_TOKENS:
        # Cas le plus courant : tout tient dans le contexte du modèle, donc les deux
        # résumés, personnages et analyse sont demandés en une seule requête pour
        # limiter la consommation de quota (RPM/RPD serrés sur le palier gratuit).
        report(0, 1, f"Le livre tient en une seule requête ({token_count} tokens). Génération en cours…")
        summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
            call(_full_report_prompt(content, settings_dir))
        )
        if leftover:
            leftovers.append(leftover)
        was_split = False
        chapter_count = 1
    else:
        # Livre trop volumineux pour tenir dans une seule requête : le livre est
        # réparti en lots de chapitres consécutifs (un lot regroupe autant de
        # chapitres que la fenêtre de contexte du modèle le permet, pour
        # limiter le nombre de requêtes envoyées - le quota journalier du
        # palier gratuit est très serré, 20 requêtes/jour par défaut). Chaque
        # lot est résumé séparément, puis UNE SEULE requête finale reçoit tous
        # les résumés de chapitre et produit le résumé court, le résumé
        # détaillé, les personnages et l'analyse littéraire.
        batches = _split_chapters_into_batches(content.chapters)
        total_steps = len(batches) + 1
        report(
            0,
            total_steps,
            f"Livre trop volumineux ({token_count} tokens). Découpage en {len(batches)} lot(s) "
            f"de chapitres ({len(content.chapters)} chapitres au total)…",
        )

        chapter_summaries: list[tuple[str, str]] = []
        for i, batch in enumerate(batches, start=1):
            report(i - 1, total_steps, f"Résumé du lot de chapitres {i}/{len(batches)}…")
            batch_summaries, leftover = _parse_chapter_summaries_batch_json(
                call(_chapter_summary_prompt(content.book_title, content.author, batch, settings_dir)), batch
            )
            if leftover:
                leftovers.append(leftover)
            chapter_summaries.extend(batch_summaries)
            report(i, total_steps, f"Lot de chapitres {i}/{len(batches)} résumé.")

        report(
            len(batches),
            total_steps,
            "Résumés fusionnés. Identification des personnages et rédaction de l'analyse…",
        )
        summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
            call(_consolidation_prompt(content.book_title, content.author, chapter_summaries, settings_dir))
        )
        if leftover:
            leftovers.append(leftover)

        was_split = True
        chapter_count = len(content.chapters)

    report(1, 1, "Résumé, personnages et analyse terminés.")

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
