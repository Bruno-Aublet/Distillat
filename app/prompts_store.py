"""Stockage des prompts Gemini personnalisés par l'utilisateur (fenêtre
« Prompts »), persistés sous la clé "prompts" de settings.json (dossier de
config, voir app.config). Une clé absente ou vide signifie "utiliser le
prompt par défaut" pour ce template. Les personnalisations sont stockées
séparément par langue ({"fr": {...}, "en": {...}}) : personnaliser un prompt
dans une langue ne doit jamais affecter la version de l'autre langue, sous
peine de mélanger un texte français et une consigne de sortie anglaise (ou
l'inverse) dès que l'utilisateur change la langue de l'interface."""
from app import config

PROMPT_KEYS = (
    "full_report",
    "chapter_summary",
    "consolidation",
)

# Langue à laquelle rattacher un ancien prompts.json à plat (format antérieur
# à l'introduction du bilinguisme, sans distinction de langue) : le français
# était l'unique langue de l'application à cette époque. Ce format à plat a
# pu être fusionné tel quel dans settings.json par config._merge_legacy_settings_files()
# (qui ne connaît pas la structure interne des prompts), d'où cette migration
# gérée ici plutôt que là-bas.
_LEGACY_FORMAT_LANGUAGE = "fr"


def _is_legacy_flat_format(data: dict) -> bool:
    """Un ancien stockage de prompts a directement les clés de prompt à sa
    racine (ex. "full_report"), plutôt qu'un niveau de langue ("fr"/"en")
    au-dessus."""
    return any(key in data for key in PROMPT_KEYS)


def load_custom_prompts(language: str) -> dict[str, str]:
    """Charge les prompts personnalisés présents sur disque pour cette langue
    uniquement. Les clés absentes (template jamais personnalisé pour cette
    langue, ou réinitialisé) ne figurent pas dans le résultat : à l'appelant
    d'utiliser le prompt par défaut de cette langue dans ce cas."""
    data = config.load_settings().get("prompts", {})
    if not isinstance(data, dict):
        return {}
    if _is_legacy_flat_format(data):
        data = {_LEGACY_FORMAT_LANGUAGE: data}
    language_prompts = data.get(language, {})
    if not isinstance(language_prompts, dict):
        return {}
    return {
        key: language_prompts[key]
        for key in PROMPT_KEYS
        if isinstance(language_prompts.get(key), str) and language_prompts[key].strip()
    }


def save_custom_prompts(language: str, prompts: dict[str, str]) -> None:
    """Enregistre les prompts personnalisés pour cette langue uniquement,
    sans toucher aux personnalisations déjà enregistrées pour une autre
    langue. Une valeur vide ou absente pour une clé équivaut à une
    réinitialisation (le prompt par défaut de cette langue sera utilisé)."""
    data = config.load_settings().get("prompts", {})
    if not isinstance(data, dict):
        data = {}
    if _is_legacy_flat_format(data):
        data = {_LEGACY_FORMAT_LANGUAGE: data}
    data[language] = {key: prompts[key] for key in PROMPT_KEYS if prompts.get(key, "").strip()}
    config.save_settings({"prompts": data})
