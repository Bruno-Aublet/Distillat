"""Stockage des prompts Gemini personnalisés par l'utilisateur (fenêtre
« Prompts »), persistés sous la clé "prompts_by_profile" de settings.json
(dossier de config, voir app.config), un jeu de personnalisations distinct par
profil de clé API (2026-07-22, support des profils multiples, voir
app/instance_lock.py) : personnaliser un prompt pour un profil ne doit jamais
affecter celui d'un autre profil, sous peine de basculer par erreur les
consignes envoyées à Gemini en changeant simplement de compte. Une clé absente
ou vide signifie "utiliser le prompt par défaut" pour ce template. À
l'intérieur d'un profil, les personnalisations restent en outre stockées
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


def _migrate_legacy_global_prompts_to_profile(profile_id: str) -> None:
    """Reprend l'ancienne personnalisation globale (stockée sous la clé
    "prompts" de settings.json, avant l'introduction des profils multiples)
    dans le PREMIER profil qui la consulte ou la modifie, uniquement si ce
    profil n'a encore aucune personnalisation propre sous
    "prompts_by_profile" et que l'ancienne clé globale existe encore.
    L'ancienne clé "prompts" est supprimée de settings.json dès cette
    première migration : sans quoi un second profil, consulté après le
    premier mais lui non plus jamais encore présent dans
    "prompts_by_profile", hériterait à tort de la même donnée globale que le
    premier profil migré (constaté le 2026-07-22 : deux profils distincts se
    retrouvaient avec exactement la même personnalisation "héritée"), alors
    qu'un seul profil - le premier à en avoir eu besoin - doit la recevoir.
    Ne migre donc plus qu'une seule fois, tous profils confondus. Le
    contrôle et l'écriture se font sous le verrou inter-processus de
    settings.json (config.update_settings, 2026-07-22) pour ne jamais
    écraser une écriture concurrente d'une autre instance."""
    # Pré-contrôle sans verrou : cas courant (migration déjà faite, ou rien à
    # migrer), inutile de prendre le verrou inter-processus à chaque lecture
    # de prompts.
    settings = config.load_settings()
    by_profile = settings.get("prompts_by_profile", {})
    if isinstance(by_profile, dict) and profile_id in by_profile:
        return
    if not settings.get("prompts", {}):
        return

    def _mutate(data: dict) -> bool:
        by_profile = data.get("prompts_by_profile", {})
        if not isinstance(by_profile, dict):
            by_profile = {}
        if profile_id in by_profile:
            return False
        legacy = data.get("prompts", {})
        if not legacy:
            return False
        by_profile[profile_id] = legacy if isinstance(legacy, dict) else {}
        data["prompts_by_profile"] = by_profile
        data["prompts"] = {}
        return True

    config.update_settings(_mutate)


def load_custom_prompts(language: str, profile_id: str) -> dict[str, str]:
    """Charge les prompts personnalisés présents sur disque pour ce profil et
    cette langue uniquement. Les clés absentes (template jamais personnalisé
    pour ce profil et cette langue, ou réinitialisé) ne figurent pas dans le
    résultat : à l'appelant d'utiliser le prompt par défaut de cette langue
    dans ce cas."""
    _migrate_legacy_global_prompts_to_profile(profile_id)
    by_profile = config.load_settings().get("prompts_by_profile", {})
    if not isinstance(by_profile, dict):
        return {}
    data = by_profile.get(profile_id, {})
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


def save_custom_prompts(language: str, prompts: dict[str, str], profile_id: str) -> None:
    """Enregistre les prompts personnalisés pour ce profil et cette langue
    uniquement, sans toucher aux personnalisations déjà enregistrées pour un
    autre profil ou une autre langue. Une valeur vide, absente, ou identique
    au prompt par défaut actuel équivaut à une réinitialisation (le prompt
    par défaut de cette langue sera utilisé) : valider la fenêtre "Prompts"
    sans avoir modifié un onglet ne doit jamais figer une copie qui
    divergerait silencieusement d'un futur changement du prompt par défaut
    dans le code. L'écriture passe par config.update_settings (relecture et
    fusion sous le verrou inter-processus de settings.json, 2026-07-22) pour
    ne jamais écraser les personnalisations sauvées au même moment par une
    autre instance pour un autre profil."""
    from app.gemini_client import default_prompt_templates

    _migrate_legacy_global_prompts_to_profile(profile_id)
    defaults = default_prompt_templates()

    def _mutate(data: dict) -> bool:
        by_profile = data.get("prompts_by_profile", {})
        if not isinstance(by_profile, dict):
            by_profile = {}
        profile_data = by_profile.get(profile_id, {})
        if not isinstance(profile_data, dict):
            profile_data = {}
        if _is_legacy_flat_format(profile_data):
            profile_data = {_LEGACY_FORMAT_LANGUAGE: profile_data}
        profile_data[language] = {
            key: prompts[key]
            for key in PROMPT_KEYS
            if prompts.get(key, "").strip() and prompts[key] != defaults.get(key)
        }
        by_profile[profile_id] = profile_data
        data["prompts_by_profile"] = by_profile
        return True

    config.update_settings(_mutate)


def reset_custom_prompt(language: str, key: str, profile_id: str) -> None:
    """Efface définitivement, sur le disque, la personnalisation existante
    pour ce profil, cette langue et ce prompt précis, sans toucher aux
    personnalisations des autres prompts, langues ou profils. Utilisé par le
    bouton "Réinitialiser ce prompt" : l'effacement est immédiat et
    permanent, il n'attend pas la validation de la fenêtre "Prompts".
    L'écriture passe par config.update_settings (même protection
    inter-processus que save_custom_prompts, 2026-07-22)."""
    _migrate_legacy_global_prompts_to_profile(profile_id)

    def _mutate(data: dict) -> bool:
        by_profile = data.get("prompts_by_profile", {})
        if not isinstance(by_profile, dict):
            return False
        profile_data = by_profile.get(profile_id, {})
        if not isinstance(profile_data, dict):
            return False
        if _is_legacy_flat_format(profile_data):
            profile_data = {_LEGACY_FORMAT_LANGUAGE: profile_data}
        language_prompts = profile_data.get(language, {})
        if not isinstance(language_prompts, dict) or key not in language_prompts:
            return False
        language_prompts = dict(language_prompts)
        del language_prompts[key]
        profile_data[language] = language_prompts
        by_profile[profile_id] = profile_data
        data["prompts_by_profile"] = by_profile
        return True

    config.update_settings(_mutate)
