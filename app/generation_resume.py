"""Sauvegarde de l'état intermédiaire d'une génération en lots interrompue par
un échec (voir gemini_client.PartialGenerationError), pour proposer une
reprise au lieu de reformuler depuis le début les lots déjà résumés avec
succès. Un fichier par livre interrompu (nommé d'après le hash de son texte
extrait) : plusieurs livres peuvent donc être en attente de reprise en même
temps, chacun dans son propre fichier."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from send2trash import send2trash

_RESUME_FILENAME_PREFIX = ".generation_resume_"
_RESUME_FILENAME_SUFFIX = ".json"
# Nom fixe utilisé avant l'introduction d'un fichier par livre (un seul livre
# en attente de reprise pouvait exister à la fois) : migré automatiquement
# vers le nouveau format au premier chargement suivant la mise à jour, pour ne
# pas perdre silencieusement une reprise déjà en attente chez un utilisateur.
_LEGACY_RESUME_FILENAME = ".generation_resume.json"


@dataclass
class ResumeState:
    book_path: str
    book_hash: str
    chapter_summaries: list[tuple[str, str]]
    batches_done: int
    batches_total: int


def compute_book_hash(full_text: str) -> str:
    return hashlib.sha256(full_text.encode("utf-8")).hexdigest()


def _resume_path(settings_dir: Path, book_hash: str) -> Path:
    return settings_dir / f"{_RESUME_FILENAME_PREFIX}{book_hash}{_RESUME_FILENAME_SUFFIX}"


def save_resume_state(settings_dir: Path, state: ResumeState) -> None:
    try:
        _resume_path(settings_dir, state.book_hash).write_text(
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


def _load_resume_state_from_path(resume_path: Path) -> ResumeState | None:
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


def load_resume_state(settings_dir: Path, book_hash: str) -> ResumeState | None:
    resume_path = _resume_path(settings_dir, book_hash)
    if not resume_path.exists():
        return None
    return _load_resume_state_from_path(resume_path)


def _migrate_legacy_resume_state(settings_dir: Path) -> None:
    legacy_path = settings_dir / _LEGACY_RESUME_FILENAME
    if not legacy_path.exists():
        return
    state = _load_resume_state_from_path(legacy_path)
    if state is not None:
        save_resume_state(settings_dir, state)
    try:
        send2trash(str(legacy_path))
    except OSError:
        pass


def load_all_resume_states(settings_dir: Path) -> list[ResumeState]:
    """Charge tous les états de reprise en attente, un par livre interrompu."""
    if not settings_dir.exists():
        return []
    _migrate_legacy_resume_state(settings_dir)
    states = []
    for resume_path in sorted(
        settings_dir.glob(f"{_RESUME_FILENAME_PREFIX}*{_RESUME_FILENAME_SUFFIX}")
    ):
        state = _load_resume_state_from_path(resume_path)
        if state is not None:
            states.append(state)
    return states


def clear_resume_state(settings_dir: Path, book_hash: str) -> None:
    resume_path = _resume_path(settings_dir, book_hash)
    if not resume_path.exists():
        return
    try:
        send2trash(str(resume_path))
    except OSError:
        pass
