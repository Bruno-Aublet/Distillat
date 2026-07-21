"""Gestion de la clé API Gemini (stockée chiffrée via le Gestionnaire
d'identification Windows, par le module keyring - jamais en clair sur disque),
et des emplacements de stockage persistants (indépendants du dossier de
l'application, pour ne pas perdre ces données si l'utilisateur
supprime/remplace le dossier de l'exe)."""
import json
import os
import shutil
import sys
from pathlib import Path

import keyring
from keyring.errors import KeyringError
from send2trash import send2trash

APP_FOLDER_NAME = "Distillat"
KEYRING_SERVICE_NAME = "Distillat"
KEYRING_USERNAME = "gemini_api_key"
_SETTINGS_FILENAME = "settings.json"

# Anciens fichiers regroupés dans settings.json (un seul cycle lecture-fusion-
# écriture pour ces réglages peu fréquemment modifiés, plutôt qu'un fichier
# par réglage) : conservés ici uniquement pour la migration ponctuelle de
# ceux qui existent encore sur disque, voir migrate_legacy_files().
_LEGACY_LAST_DIRS_FILENAME = "last_dirs.json"
_LEGACY_PROMPTS_FILENAME = "prompts.json"


def get_app_dir() -> Path:
    """Répertoire de l'exécutable (ou du script en développement)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_resource_dir() -> Path:
    """Répertoire des ressources embarquées à la compilation (ex: LICENSE).
    En mode gelé, PyInstaller les place dans sys._MEIPASS (souvent un
    sous-dossier _internal/), distinct du dossier de l'exécutable."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", get_app_dir()))
    return Path(__file__).resolve().parent.parent


def get_app_icon_path() -> Path:
    """Chemin de l'icône de l'application (bandeau de toutes les fenêtres)."""
    return get_resource_dir() / "icons" / "open-book_4681875.png"


def get_settings_dir() -> Path:
    """Dossier pour les données techniques (compteur de quota, prompts et
    limites personnalisés, derniers dossiers utilisés) : %APPDATA%\\Distillat,
    en mode gelé comme en développement. Indépendant du dossier de l'exe (ou
    du projet) pour survivre à une réinstallation."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else get_app_dir()
    settings_dir = base / APP_FOLDER_NAME
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir


def get_reports_dir() -> Path:
    """Dossier où sauvegarder/charger les fiches (.distillat.json) et les
    exports PDF : Documents\\Distillat\\Fiches, en mode développement comme
    en mode compilé. Indépendant du dossier de l'exe pour survivre à une
    réinstallation."""
    reports_dir = Path.home() / "Documents" / APP_FOLDER_NAME / "Fiches"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def get_debug_logs_dir() -> Path:
    """Sous-dossier de `get_settings_dir()` où sont journalisées les réponses
    Gemini brutes en cas d'échec de parsing JSON (voir
    `gemini_client._log_unparsable_response()`), pour permettre un diagnostic
    après coup sans avoir à reproduire l'appel API. Même emplacement unique
    quel que soit le mode de lancement, comme le reste des données techniques
    (règle 6 du projet)."""
    logs_dir = get_settings_dir() / "debug_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def load_settings() -> dict:
    """Charge settings.json (dossier de config), qui regroupe la langue de
    l'UI, les prompts personnalisés par langue et les derniers dossiers
    utilisés : des réglages peu fréquemment modifiés, à la différence du
    compteur de quota (.quota_state.json) ou des limites RPM/TPM/RPD
    (quota_limits.json), réécrits bien plus souvent et donc gardés dans des
    fichiers séparés. Retourne {} si le fichier est absent, illisible ou de
    forme inattendue, sans jamais lever d'erreur."""
    settings_path = get_settings_dir() / _SETTINGS_FILENAME
    if not settings_path.exists():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(update: dict) -> None:
    """Fusionne `update` dans settings.json déjà présent (les autres clés de
    premier niveau, ex. "prompts" ou "last_dirs", sont préservées telles
    quelles) et réécrit le fichier."""
    settings_path = get_settings_dir() / _SETTINGS_FILENAME
    data = load_settings()
    data.update(update)
    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_last_report_dir() -> Path | None:
    """Dernier dossier utilisé pour sauvegarder/charger une fiche
    (.distillat.json), ou None si aucun n'a encore été mémorisé ou si le
    dossier mémorisé n'existe plus."""
    return _load_last_dir("report_dir")


def save_last_report_dir(directory: Path) -> None:
    _save_last_dir("report_dir", directory)


def load_last_pdf_dir() -> Path | None:
    """Dernier dossier utilisé pour exporter un PDF, ou None si aucun n'a
    encore été mémorisé ou si le dossier mémorisé n'existe plus."""
    return _load_last_dir("pdf_dir")


def save_last_pdf_dir(directory: Path) -> None:
    _save_last_dir("pdf_dir", directory)


def _load_last_dir(key: str) -> Path | None:
    last_dirs = load_settings().get("last_dirs", {})
    if not isinstance(last_dirs, dict):
        return None
    value = last_dirs.get(key)
    if not value:
        return None
    directory = Path(value)
    return directory if directory.is_dir() else None


def _save_last_dir(key: str, directory: Path) -> None:
    last_dirs = load_settings().get("last_dirs", {})
    if not isinstance(last_dirs, dict):
        last_dirs = {}
    last_dirs[key] = str(directory)
    save_settings({"last_dirs": last_dirs})


def migrate_legacy_files() -> None:
    """Déplace vers les nouveaux emplacements (AppData/Documents) les fichiers
    laissés par d'anciennes versions de Distillat qui les créaient à côté de
    l'exe (ne s'applique qu'en mode compilé), et fusionne dans settings.json
    les anciens last_dirs.json/prompts.json s'ils existent encore (s'applique
    aussi en développement, ces fichiers vivaient déjà dans %APPDATA%\\Distillat
    dans les deux modes). Ne remplace/n'écrase jamais une donnée déjà présente
    au nouvel emplacement (ne perd jamais de données)."""
    _merge_legacy_settings_files()

    if not getattr(sys, "frozen", False):
        return

    legacy_dir = get_app_dir()

    legacy_path = legacy_dir / ".quota_state.json"
    new_path = get_settings_dir() / ".quota_state.json"
    if legacy_path.exists() and not new_path.exists():
        try:
            shutil.move(str(legacy_path), str(new_path))
        except OSError:
            pass

    legacy_reports_dir = legacy_dir / "Fiches"
    if legacy_reports_dir.is_dir():
        new_reports_dir = get_reports_dir()
        for item in legacy_reports_dir.iterdir():
            target = new_reports_dir / item.name
            if not target.exists():
                try:
                    shutil.move(str(item), str(target))
                except OSError:
                    pass
        try:
            next(legacy_reports_dir.iterdir())
        except StopIteration:
            legacy_reports_dir.rmdir()
        except OSError:
            pass


def _merge_legacy_settings_files() -> None:
    """Fusionne dans settings.json les anciens fichiers last_dirs.json et
    prompts.json (chacun dans son propre fichier avant leur regroupement),
    s'ils existent encore. Contrairement au reste de migrate_legacy_files(),
    s'applique aussi en développement (ces fichiers vivaient déjà dans
    %APPDATA%\\Distillat dans les deux modes, ce n'est pas un changement de
    dossier mais de structure). Ne perd jamais de donnée déjà présente dans
    settings.json (une clé qui y existe déjà n'est jamais écrasée) ; supprime
    l'ancien fichier une fois sa fusion effectuée avec succès."""
    settings_dir = get_settings_dir()
    last_dirs_path = settings_dir / _LEGACY_LAST_DIRS_FILENAME
    prompts_path = settings_dir / _LEGACY_PROMPTS_FILENAME
    if not last_dirs_path.exists() and not prompts_path.exists():
        return

    settings = load_settings()
    changed = False

    if last_dirs_path.exists() and "last_dirs" not in settings:
        try:
            legacy_last_dirs = json.loads(last_dirs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            legacy_last_dirs = None
        if isinstance(legacy_last_dirs, dict):
            settings["last_dirs"] = legacy_last_dirs
            changed = True
        try:
            send2trash(str(last_dirs_path))
        except OSError:
            pass

    if prompts_path.exists() and "prompts" not in settings:
        try:
            legacy_prompts = json.loads(prompts_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            legacy_prompts = None
        if isinstance(legacy_prompts, dict):
            settings["prompts"] = legacy_prompts
            changed = True
        try:
            send2trash(str(prompts_path))
        except OSError:
            pass

    if changed:
        save_settings(settings)


def load_language_setting() -> str | None:
    """Langue de l'interface choisie par l'utilisateur (code "fr"/"en"), ou
    None si aucune n'a encore été enregistrée (premier démarrage : la langue
    sera alors déterminée depuis la langue système, voir app.i18n)."""
    return load_settings().get("language")


def save_language_setting(language: str) -> None:
    save_settings({"language": language})


def load_api_key() -> str | None:
    """Retourne None aussi si le Gestionnaire d'identification Windows est
    indisponible (KeyringError) : sans ce garde-fou, l'appel fait au
    démarrage de l'application (avant même l'affichage de la fenêtre) la
    ferait planter au lancement, sans aucun recours pour l'utilisateur."""
    try:
        return keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
    except KeyringError:
        return None


def save_api_key(api_key: str) -> bool:
    """Retourne False (au lieu de laisser l'exception remonter) si le
    Gestionnaire d'identification Windows est indisponible ; à l'appelant
    d'en informer l'utilisateur."""
    try:
        keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, api_key)
        return True
    except KeyringError:
        return False
