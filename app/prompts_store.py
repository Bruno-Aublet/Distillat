"""Stockage des prompts Gemini personnalisés par l'utilisateur (fenêtre
« Prompts »), persistés dans le dossier de config comme quota_limits.json.
Une clé absente ou vide dans le fichier signifie "utiliser le prompt par
défaut" pour ce template."""
import json
from pathlib import Path

PROMPT_KEYS = (
    "full_report",
    "chapter_summary",
    "consolidation",
)

_PROMPTS_FILENAME = "prompts.json"


def load_custom_prompts(settings_dir: Path) -> dict[str, str]:
    """Charge les prompts personnalisés présents sur disque. Les clés absentes
    (template jamais personnalisé, ou réinitialisé) ne figurent pas dans le
    résultat : à l'appelant d'utiliser le prompt par défaut dans ce cas."""
    prompts_path = settings_dir / _PROMPTS_FILENAME
    if not prompts_path.exists():
        return {}
    try:
        data = json.loads(prompts_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in PROMPT_KEYS if isinstance(data.get(key), str) and data[key].strip()}


def save_custom_prompts(settings_dir: Path, prompts: dict[str, str]) -> None:
    """Enregistre les prompts personnalisés. Une valeur vide ou absente pour
    une clé équivaut à une réinitialisation (le prompt par défaut sera utilisé)."""
    prompts_path = settings_dir / _PROMPTS_FILENAME
    to_save = {key: prompts[key] for key in PROMPT_KEYS if prompts.get(key, "").strip()}
    prompts_path.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
