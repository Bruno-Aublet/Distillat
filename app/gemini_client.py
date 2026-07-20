"""Client Gemini : comptage de tokens, résumé/personnages/analyse, avec retry."""
import json
from collections.abc import Callable
from dataclasses import dataclass

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
    """Un quota journalier (RPD) ne se réinitialise qu'à minuit : retenter
    dans les secondes/minutes qui suivent est voué à l'échec, contrairement à
    un quota par minute (RPM/TPM) qui se libère naturellement en attendant."""
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


def count_tokens(text: str) -> int:
    model = _get_model()
    result = model.count_tokens(text)
    return result.total_tokens


def _split_chapters_into_batches(chapters: list[Chapter]) -> list[list[Chapter]]:
    """Regroupe les chapitres en lots dont le texte cumulé tient sous
    MAX_TOKENS_PER_REQUEST, la limite de débit par minute du palier gratuit
    (et non MAX_INPUT_TOKENS, la fenêtre de contexte du modèle, bien plus
    large - un lot dimensionné sur cette dernière saturerait le quota TPM à
    lui seul). Un chapitre dont le texte dépasse à lui seul
    MAX_TOKENS_PER_REQUEST forme son propre lot (l'appel Gemini correspondant
    échouera probablement, mais ce n'est pas à cette fonction de tronquer le
    contenu du livre)."""
    batches: list[list[Chapter]] = []
    current_batch: list[Chapter] = []
    current_tokens = 0

    for chapter in chapters:
        chapter_tokens = count_tokens(chapter.text)
        if current_batch and current_tokens + chapter_tokens > MAX_TOKENS_PER_REQUEST:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(chapter)
        current_tokens += chapter_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _call_gemini(
    model: genai.GenerativeModel,
    prompt: str,
    quota_tracker: QuotaTracker,
    on_quota_update: QuotaCallback | None = None,
) -> str:
    """Effectue un seul appel à l'API Gemini, sans retry automatique : toute
    erreur (quota, service indisponible...) remonte immédiatement sous forme
    de GeminiError avec un message clair, laissant à l'utilisateur le choix
    de relancer la génération en recliquant sur Résumer."""
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
            raise GeminiError(tr("gemini_errors.blocked_by_safety_filters")) from exc
        if not text:
            raise GeminiError(tr("gemini_errors.empty_response"))
        return text
    except (ResourceExhausted, ServiceUnavailable, InternalServerError, DeadlineExceeded, PermissionDenied, Unauthenticated) as exc:
        message, error_kind = _friendly_error_message(exc)
        raise GeminiError(message, error_kind=error_kind) from exc


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
    de requêtes envoyées à l'API sur le palier gratuit (quota journalier très
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
    data, leftover = _parse_json_object(raw_text)
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
    succès : ignorés si le livre tient en une seule requête."""

    def report(done: int, total: int, message: str) -> None:
        if on_progress:
            on_progress(done, total, message)

    json_model = _get_json_model()

    def call(prompt: str, model: genai.GenerativeModel = json_model) -> str:
        return _call_gemini(
            model,
            prompt,
            quota_tracker=quota_tracker,
            on_quota_update=on_quota_update,
        )

    report(0, 1, tr("gemini_progress.counting_tokens"))
    token_count = count_tokens(content.full_text)
    leftovers: list[str] = []

    if token_count <= MAX_TOKENS_PER_REQUEST:
        # Cas le plus courant : le texte tient sous la limite de débit par minute
        # (TPM) du palier gratuit, donc les deux résumés, personnages et analyse
        # sont demandés en une seule requête pour limiter la consommation de quota.
        report(0, 1, tr("gemini_progress.single_request", token_count=token_count))
        summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
            call(_full_report_prompt(content, use_custom_prompts))
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
            tr(
                "gemini_progress.book_too_large",
                token_count=token_count,
                batch_count=len(batches),
                chapter_count=len(content.chapters),
            ),
        )

        chapter_summaries: list[tuple[str, str]] = list(resume_chapter_summaries or [])
        start_index = min(resume_batches_done, len(batches))
        for i, batch in enumerate(batches[start_index:], start=start_index + 1):
            report(i - 1, total_steps, tr("gemini_progress.summarizing_batch", current=i, total=len(batches)))
            try:
                batch_summaries, leftover = _parse_chapter_summaries_batch_json(
                    call(_chapter_summary_prompt(content.book_title, content.author, batch, use_custom_prompts)), batch
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
        try:
            summary_text, detailed_summary_text, characters, analysis_text, leftover = _parse_full_report_json(
                call(_consolidation_prompt(content.book_title, content.author, chapter_summaries, use_custom_prompts))
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
