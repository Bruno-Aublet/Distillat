"""Sauvegarde de l'état intermédiaire d'une génération en lots interrompue par
un échec (voir gemini_client.PartialGenerationError), pour proposer une
reprise au lieu de reformuler depuis le début les lots déjà résumés avec
succès. Fichier unique (une seule génération en lots peut être interrompue à
la fois) : un état sans rapport avec le livre en cours (autre fichier, ou même
fichier modifié depuis) est ignoré via le hash du texte extrait."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_RESUME_FILENAME = ".generation_resume.json"


@dataclass
class ResumeState:
    book_path: str
    book_hash: str
    chapter_summaries: list[tuple[str, str]]
    batches_done: int
    batches_total: int


def compute_book_hash(full_text: str) -> str:
    return hashlib.sha256(full_text.encode("utf-8")).hexdigest()


def save_resume_state(settings_dir: Path, state: ResumeState) -> None:
    try:
        (settings_dir / _RESUME_FILENAME).write_text(
            json.dumps(
                {
                    "book_path": state.book_path,
                    "book_hash": state.book_hash,
                    "chapter_summaries": [list(pair) for pair in state.chapter_summaries],
                    "batches_done": state.batches_done,
                    "batches_total": state.batches_total,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_resume_state(settings_dir: Path) -> ResumeState | None:
    resume_path = settings_dir / _RESUME_FILENAME
    if not resume_path.exists():
        return None
    try:
        data = json.loads(resume_path.read_text(encoding="utf-8"))
        return ResumeState(
            book_path=data["book_path"],
            book_hash=data["book_hash"],
            chapter_summaries=[tuple(pair) for pair in data["chapter_summaries"]],
            batches_done=int(data["batches_done"]),
            batches_total=int(data["batches_total"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_resume_state(settings_dir: Path) -> None:
    try:
        (settings_dir / _RESUME_FILENAME).unlink(missing_ok=True)
    except OSError:
        pass
