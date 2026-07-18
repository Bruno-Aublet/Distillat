"""Gestion de la clé API Gemini (stockée chiffrée via le Gestionnaire
d'identification Windows, par le module keyring — jamais en clair sur disque),
et des emplacements de stockage persistants (indépendants du dossier de
l'application, pour ne pas perdre ces données si l'utilisateur
supprime/remplace le dossier de l'exe)."""
import os
import shutil
import sys
from pathlib import Path

import keyring
from dotenv import load_dotenv

ENV_VAR_NAME = "GEMINI_API_KEY"
APP_FOLDER_NAME = "Distillat"
KEYRING_SERVICE_NAME = "Distillat"
KEYRING_USERNAME = "gemini_api_key"


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
    """Dossier pour les données techniques (.env, compteur de quota) :
    %APPDATA%\\Distillat en mode gelé, dossier du projet en développement.
    Indépendant du dossier de l'exe pour survivre à une réinstallation."""
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else get_app_dir()
        settings_dir = base / APP_FOLDER_NAME
        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir
    return get_app_dir()


def get_reports_dir() -> Path:
    """Dossier où sauvegarder/charger les fiches (.distillat.json) et les
    exports Word : Documents\\Distillat\\Fiches en mode gelé, dossier du
    projet en développement. Indépendant du dossier de l'exe pour survivre
    à une réinstallation."""
    if getattr(sys, "frozen", False):
        documents = Path.home() / "Documents"
        reports_dir = documents / APP_FOLDER_NAME / "Fiches"
    else:
        reports_dir = get_app_dir() / "Fiches"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def migrate_legacy_files() -> None:
    """Déplace vers les nouveaux emplacements (AppData/Documents) les fichiers
    laissés par d'anciennes versions de Distillat qui les créaient à côté de
    l'exe. Ne s'applique qu'en mode compilé ; ne remplace jamais un fichier
    déjà présent au nouvel emplacement (ne perd jamais de données)."""
    if not getattr(sys, "frozen", False):
        return

    legacy_dir = get_app_dir()

    for filename in (".env", ".quota_state.json"):
        legacy_path = legacy_dir / filename
        new_path = get_settings_dir() / filename
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


def get_env_path() -> Path:
    """Ancien emplacement de stockage de la clé API (fichier .env en clair),
    conservé uniquement pour la migration ponctuelle vers keyring."""
    return get_settings_dir() / ".env"


def _migrate_legacy_env_api_key() -> None:
    """Reprend une clé API laissée en clair dans un .env par une ancienne
    version, la stocke via keyring, puis supprime le fichier .env."""
    env_path = get_env_path()
    if not env_path.exists():
        return
    load_dotenv(dotenv_path=env_path, override=True)
    legacy_key = os.environ.get(ENV_VAR_NAME)
    if legacy_key and not keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME):
        keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, legacy_key)
    try:
        env_path.unlink()
    except OSError:
        pass


def load_api_key() -> str | None:
    _migrate_legacy_env_api_key()
    api_key = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
    if api_key:
        os.environ[ENV_VAR_NAME] = api_key
    return api_key


def save_api_key(api_key: str) -> None:
    keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, api_key)
    os.environ[ENV_VAR_NAME] = api_key
