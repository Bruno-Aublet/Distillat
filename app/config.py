"""Gestion de la clé API Gemini (stockée chiffrée via le Gestionnaire
d'identification Windows, par le module keyring - jamais en clair sur disque),
et des emplacements de stockage persistants (indépendants du dossier de
l'application, pour ne pas perdre ces données si l'utilisateur
supprime/remplace le dossier de l'exe)."""
import os
import shutil
import sys
from pathlib import Path

import keyring
from keyring.errors import KeyringError

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
    """Dossier pour les données techniques (compteur de quota, prompts et
    limites personnalisés) : %APPDATA%\\Distillat en mode gelé, dossier du
    projet en développement. Indépendant du dossier de l'exe pour survivre à
    une réinstallation."""
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else get_app_dir()
        settings_dir = base / APP_FOLDER_NAME
        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir
    return get_app_dir()


def get_reports_dir() -> Path:
    """Dossier où sauvegarder/charger les fiches (.distillat.json) et les
    exports PDF : Documents\\Distillat\\Fiches, en mode développement comme
    en mode compilé. Indépendant du dossier de l'exe pour survivre à une
    réinstallation."""
    reports_dir = Path.home() / "Documents" / APP_FOLDER_NAME / "Fiches"
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
