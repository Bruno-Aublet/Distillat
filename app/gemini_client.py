"""Client Gemini : comptage de tokens, résumé/personnages/analyse, avec retry."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

from app.book_report import BookReport, Character
from app.epub_parser import Chapter, BookContent
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


def configure(api_key: str) -> None:
    genai.configure(api_key=api_key)


def _get_model() -> genai.GenerativeModel:
    return genai.GenerativeModel(MODEL_NAME)


def count_tokens(text: str) -> int:
    model = _get_model()
    result = model.count_tokens(text)
    return result.total_tokens


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
            if not response.text:
                raise GeminiError("Réponse vide reçue de l'API Gemini.")
            return response.text
        except ResourceExhausted as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            if on_retry:
                blocked_info = _extract_quota_blocked_info(exc)
                wait_seconds = blocked_info.retry_after_seconds or backoff
                on_retry(attempt, wait_seconds, blocked_info)
                time.sleep(wait_seconds)
            else:
                time.sleep(backoff)
            backoff *= 2
        except ServiceUnavailable as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            if on_retry:
                on_retry(attempt, backoff, None)
            time.sleep(backoff)
            backoff *= 2

    raise GeminiError(
        f"Échec de l'appel à l'API Gemini après {MAX_RETRIES} tentatives "
        f"(limite de quota probablement dépassée) : {last_error}"
    )


def _full_report_prompt(content: BookContent) -> str:
    """Un seul prompt demandant les deux résumés + personnages + analyse en une
    requête, pour un livre dont le texte tient dans la fenêtre de contexte du modèle."""
    return f"""Tu es un assistant expert en littérature. Voici le texte intégral d'un livre \
intitulé "{content.book_title}" de {content.author}.

Produis TOUJOURS EN FRANÇAIS, quelle que soit la langue originale du texte, les quatre éléments \
suivants :

1. Un RÉSUMÉ COURT (trois à quatre paragraphes de synthèse) donnant une vue d'ensemble concise de \
l'intrigue ou du propos, du début à la fin.
2. Un RÉSUMÉ DÉTAILLÉ, substantiel et développé (au moins 1500 mots, et bien davantage — \
2500 à 4000 mots — pour un roman long à l'intrigue riche), qui reprend la structure du livre \
(une section par partie ou groupe de chapitres si pertinent) et couvre pour chaque section : \
les événements clés, les rebondissements, les dialogues ou moments marquants, et l'évolution \
des personnages. Ne te contente pas d'une liste télégraphique de faits : développe chaque \
section avec plusieurs phrases fluides et concrètes, comme le ferait un lecteur racontant le \
livre en détail à un ami.
3. La liste des PERSONNAGES PRINCIPAUX (significatifs pour l'intrigue ; ignore les personnages \
anecdotiques), chacun avec une description couvrant son rôle, sa personnalité et son évolution.
4. Une ANALYSE littéraire d'au moins 600 à 900 mots, structurée en plusieurs paragraphes distincts \
couvrant les thèmes principaux, le style d'écriture et la construction narrative, puis la portée \
de l'œuvre — développée et argumentée, sans répéter le contenu déjà couvert par les résumés.

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
{content.full_text}
---

Réponds uniquement avec l'objet JSON."""


def _characters_and_analysis_prompt(content: BookContent) -> str:
    """Personnages + analyse combinés en une requête, utilisé quand le résumé
    a déjà nécessité un découpage par chapitres (texte trop long pour un seul appel)."""
    return f"""Tu es un assistant expert en littérature. Voici le texte intégral du livre \
"{content.book_title}" de {content.author}.

Produis TOUJOURS EN FRANÇAIS les deux éléments suivants :

1. La liste des PERSONNAGES PRINCIPAUX (significatifs pour l'intrigue ; ignore les personnages \
anecdotiques), chacun avec une description couvrant son rôle, sa personnalité et son évolution.
2. Une ANALYSE littéraire d'au moins 600 à 900 mots, structurée en plusieurs paragraphes distincts \
couvrant les thèmes principaux, le style d'écriture et la construction narrative, puis la portée \
de l'œuvre — développée et argumentée.

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact :
{{
  "characters": [{{"name": "Nom", "description": "Description en français..."}}, ...],
  "analysis": "L'analyse littéraire ici, en français..."
}}

Texte du livre :
---
{content.full_text}
---

Réponds uniquement avec l'objet JSON."""


def _chapter_summary_prompt(book_title: str, author: str, chapter: Chapter) -> str:
    return f"""Tu résumes un chapitre du livre "{book_title}" de {author}.

Résume le chapitre suivant, intitulé "{chapter.title}", TOUJOURS EN FRANÇAIS quelle que soit \
la langue du texte source. Sois fidèle au contenu, couvre les événements et idées importants \
(scènes clés, dialogues marquants, évolution des personnages). Ce résumé sera ensuite fusionné \
avec ceux des autres chapitres : ne le brade pas en une liste télégraphique, développe-le sur \
au moins 300 mots (davantage si le chapitre est riche en événements), sans limite maximale.

Texte du chapitre :
---
{chapter.text}
---

Rédige le résumé de ce chapitre en français."""


def _consolidation_prompt(book_title: str, author: str, chapter_summaries: list[tuple[str, str]]) -> str:
    joined = "\n\n".join(f"### {title}\n{summary}" for title, summary in chapter_summaries)
    return f"""Voici les résumés successifs des chapitres du livre "{book_title}" de {author}.

Résumés par chapitre :
---
{joined}
---

À partir de ces résumés partiels, TOUJOURS EN FRANÇAIS, produis deux versions consolidées :

1. Un RÉSUMÉ COURT (trois à quatre paragraphes de synthèse) donnant une vue d'ensemble concise de \
l'intrigue du début à la fin.
2. Un RÉSUMÉ DÉTAILLÉ (au moins 1500 mots, et bien davantage — 2500 à 4000 mots — pour un roman \
long à l'intrigue riche), qui fusionne et reformule les résumés de chapitre ci-dessus en un texte \
cohérent et fluide (pas une simple concaténation), en conservant les événements clés, les \
rebondissements et l'évolution des personnages de chaque partie, en évitant les répétitions et \
en assurant une continuité narrative claire entre les parties.

Réponds STRICTEMENT avec un objet JSON valide, sans aucun texte avant ou après, ni fences \
markdown, au format exact :
{{
  "summary": "Le résumé court ici, en français...",
  "detailed_summary": "Le résumé détaillé ici, en français..."
}}

Réponds uniquement avec l'objet JSON."""


def _strip_json_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def _parse_characters_list(data: list) -> list[Character]:
    characters: list[Character] = []
    for item in data:
        name = item.get("name", "").strip()
        description = item.get("description", "").strip()
        if name and description:
            characters.append(Character(name=name, description=description))
    return characters


def _parse_full_report_json(raw_text: str) -> tuple[str, list[Character], str]:
    """Parse la réponse combinée résumé + personnages + analyse."""
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Réponse de Gemini illisible (format inattendu) : {exc}") from exc

    summary = str(data.get("summary", "")).strip()
    detailed_summary = str(data.get("detailed_summary", "")).strip()
    analysis = str(data.get("analysis", "")).strip()
    characters = _parse_characters_list(data.get("characters", []))

    if not summary:
        raise GeminiError("La réponse de Gemini ne contient pas de résumé exploitable.")

    return summary, detailed_summary, characters, analysis


def _parse_characters_and_analysis_json(raw_text: str) -> tuple[list[Character], str]:
    """Parse la réponse combinée personnages + analyse (cas du résumé par chapitres)."""
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Réponse de Gemini illisible (format inattendu) : {exc}") from exc

    analysis = str(data.get("analysis", "")).strip()
    characters = _parse_characters_list(data.get("characters", []))
    return characters, analysis


def _parse_dual_summary_json(raw_text: str) -> tuple[str, str]:
    """Parse la réponse résumé court + détaillé (cas de la consolidation par chapitres)."""
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Réponse de Gemini illisible (format inattendu) : {exc}") from exc

    summary = str(data.get("summary", "")).strip()
    detailed_summary = str(data.get("detailed_summary", "")).strip()

    if not summary:
        raise GeminiError("La réponse de Gemini ne contient pas de résumé exploitable.")

    return summary, detailed_summary


def generate_book_report(
    content: BookContent,
    quota_tracker: QuotaTracker,
    on_progress: ProgressCallback | None = None,
    on_quota_update: QuotaCallback | None = None,
    on_retry_wait: RetryWaitCallback | None = None,
) -> BookReport:
    """Génère à la suite le résumé, les fiches personnages et l'analyse littéraire
    du livre. Le résumé est produit directement si le livre tient dans une seule
    requête, ou par découpage en chapitres puis consolidation sinon."""

    def report(done: int, total: int, message: str) -> None:
        if on_progress:
            on_progress(done, total, message)

    model = _get_model()

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

    def call(prompt: str) -> str:
        return _call_with_retry(
            model,
            prompt,
            quota_tracker=quota_tracker,
            on_retry=retry_notice,
            on_quota_update=on_quota_update,
        )

    report(0, 1, "Comptage des tokens du texte extrait…")
    token_count = count_tokens(content.full_text)

    if token_count <= MAX_INPUT_TOKENS:
        # Cas le plus courant : tout tient dans le contexte du modèle, donc les deux
        # résumés, personnages et analyse sont demandés en une seule requête pour
        # limiter la consommation de quota (RPM/RPD serrés sur le palier gratuit).
        report(0, 1, f"Le livre tient en une seule requête ({token_count} tokens). Génération en cours…")
        summary_text, detailed_summary_text, characters, analysis_text = _parse_full_report_json(
            call(_full_report_prompt(content))
        )
        was_split = False
        chapter_count = 1
    else:
        # Livre trop volumineux : le résumé nécessite un découpage par chapitres
        # puis consolidation (incompressible), mais personnages + analyse sont
        # ensuite regroupés en une seule requête supplémentaire (au lieu de deux).
        total_steps = len(content.chapters) + 2  # + consolidation + personnages/analyse
        report(
            0,
            total_steps,
            f"Livre trop volumineux ({token_count} tokens). Découpage en {len(content.chapters)} chapitres…",
        )

        chapter_summaries: list[tuple[str, str]] = []
        for i, chapter in enumerate(content.chapters, start=1):
            report(i - 1, total_steps, f"Résumé du chapitre {i}/{len(content.chapters)} : {chapter.title}")
            chapter_summary = call(_chapter_summary_prompt(content.book_title, content.author, chapter))
            chapter_summaries.append((chapter.title, chapter_summary))
            report(i, total_steps, f"Chapitre {i}/{len(content.chapters)} résumé.")

        report(len(content.chapters), total_steps, "Consolidation du résumé final…")
        summary_text, detailed_summary_text = _parse_dual_summary_json(
            call(_consolidation_prompt(content.book_title, content.author, chapter_summaries))
        )

        report(
            len(content.chapters) + 1,
            total_steps,
            "Résumé consolidé. Identification des personnages et rédaction de l'analyse…",
        )
        characters, analysis_text = _parse_characters_and_analysis_json(
            call(_characters_and_analysis_prompt(content))
        )
        was_split = True
        chapter_count = len(content.chapters)

    report(1, 1, "Résumé, personnages et analyse terminés.")

    return BookReport(
        book_title=content.book_title,
        author=content.author,
        summary_text=summary_text,
        detailed_summary_text=detailed_summary_text,
        characters=characters,
        analysis_text=analysis_text,
        cover_image=content.cover_image,
        was_split=was_split,
        chapter_count=chapter_count,
    )
