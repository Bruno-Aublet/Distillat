"""Gestion de la clé API Gemini (stockée chiffrée via le Gestionnaire
d'identification Windows, par le module keyring - jamais en clair sur disque),
et des emplacements de stockage persistants (indépendants du dossier de
l'application, pour ne pas perdre ces données si l'utilisateur
supprime/remplace le dossier de l'exe)."""
import json
import msvcrt
import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import keyring
from keyring.errors import KeyringError
from send2trash import send2trash

APP_FOLDER_NAME = "Distillat"
KEYRING_SERVICE_NAME = "Distillat"
KEYRING_USERNAME = "gemini_api_key"
_SETTINGS_FILENAME = "settings.json"
# Petit fichier dédié au verrou inter-processus de settings.json (voir
# _settings_lock()) : jamais lu, seul son octet 0 est verrouillé via
# msvcrt.locking pour sérialiser les cycles lecture-fusion-écriture entre
# plusieurs instances de Distillat lancées en parallèle (app.instance_lock).
_SETTINGS_LOCK_FILENAME = ".settings.lock"

_DEFAULT_PROFILE_NAME = "Défaut"

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


def get_success_sound_path() -> Path:
    """Chemin du son joué lorsqu'une fiche est générée avec succès."""
    return get_resource_dir() / "assets" / "success.wav"


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


@contextmanager
def _settings_lock():
    """Verrou inter-processus autour de tout cycle lecture-fusion-écriture de
    settings.json : plusieurs instances de Distillat (voir app.instance_lock)
    écrivent le même fichier, et deux cycles concurrents non sérialisés se
    font perdre mutuellement leurs mises à jour - ex. un profil ajouté par une
    instance effacé par la sauvegarde d'un dernier dossier utilisé dans une
    autre, son entrée keyring devenant alors orpheline (course fermée le
    2026-07-22). Verrouille l'octet 0 d'un petit fichier dédié
    (.settings.lock) via msvcrt.locking(LK_LOCK), qui réessaie environ 10
    secondes avant d'abandonner. Best-effort : si le verrou ne peut pas être
    pris (délai dépassé, erreur disque), on continue sans verrou plutôt que de
    faire échouer la sauvegarde - le comportement redevient alors simplement
    celui d'avant l'introduction de ce verrou."""
    lock_path = get_settings_dir() / _SETTINGS_LOCK_FILENAME
    handle = None
    locked = False
    try:
        handle = open(lock_path, "a")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        locked = True
    except OSError:
        pass
    try:
        yield
    finally:
        if handle is not None:
            if locked:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            try:
                handle.close()
            except OSError:
                pass


def _write_settings_file(data: dict) -> None:
    """Écriture atomique de settings.json : le contenu est d'abord écrit dans
    un fichier temporaire du même dossier (suffixé par le PID pour ne jamais
    entrer en collision avec celui d'une autre instance), puis substitué d'un
    bloc via os.replace(), atomique sous Windows. Un crash ou une coupure en
    pleine écriture laisse ainsi l'ancien fichier intact, au lieu d'un
    settings.json tronqué que load_settings() lirait ensuite comme {} - ce qui
    faisait silencieusement disparaître d'un coup tous les réglages ET la
    liste des profils de clé API, leurs entrées keyring devenant orphelines,
    irrécupérables depuis l'application (risque fermé le 2026-07-22)."""
    settings_path = get_settings_dir() / _SETTINGS_FILENAME
    tmp_path = settings_path.parent / f"{_SETTINGS_FILENAME}.{os.getpid()}.tmp"
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, settings_path)


def save_settings(update: dict) -> None:
    """Fusionne `update` dans settings.json déjà présent (les autres clés de
    premier niveau, ex. "prompts" ou "last_dirs", sont préservées telles
    quelles) et réécrit le fichier. La relecture et la fusion se font sous le
    verrou inter-processus (_settings_lock) et l'écriture est atomique
    (_write_settings_file). Attention : `update` remplace les clés de premier
    niveau entières ; pour une modification qui dépend du contenu existant
    d'une clé (ajout à une liste, mise à jour d'un sous-dictionnaire), passer
    par update_settings() plutôt que par un load_settings() préalable, qui
    recréerait la fenêtre de mise à jour perdue que le verrou ferme."""
    with _settings_lock():
        data = load_settings()
        data.update(update)
        _write_settings_file(data)


def update_settings(mutate) -> None:
    """Cycle lecture-modification-écriture complet de settings.json sous le
    verrou inter-processus : `mutate(data)` reçoit le contenu fraîchement relu
    du fichier, le modifie en place, et retourne True si quelque chose a
    réellement changé (False/None : rien n'est réécrit sur disque). À utiliser
    pour toute modification qui dépend du contenu existant (ajout à une liste,
    mise à jour d'un sous-dictionnaire...) : relire en dehors du verrou puis
    passer le résultat à save_settings() laisserait une autre instance écrire
    entre la relecture et l'écriture, et sa mise à jour serait perdue."""
    with _settings_lock():
        data = load_settings()
        if mutate(data):
            _write_settings_file(data)


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


def load_last_cover_dir() -> Path | None:
    """Dernier dossier utilisé pour choisir manuellement une image de
    couverture, ou None si aucun n'a encore été mémorisé ou si le dossier
    mémorisé n'existe plus."""
    return _load_last_dir("cover_dir")


def save_last_cover_dir(directory: Path) -> None:
    _save_last_dir("cover_dir", directory)


def load_last_book_dir() -> Path | None:
    """Dernier dossier utilisé pour choisir un livre (EPUB/PDF) ou une fiche
    via le sélecteur de fichier de la zone de dépôt, ou None si aucun n'a
    encore été mémorisé ou si le dossier mémorisé n'existe plus."""
    return _load_last_dir("book_dir")


def save_last_book_dir(directory: Path) -> None:
    _save_last_dir("book_dir", directory)


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
    def _mutate(data: dict) -> bool:
        last_dirs = data.get("last_dirs", {})
        if not isinstance(last_dirs, dict):
            last_dirs = {}
        last_dirs[key] = str(directory)
        data["last_dirs"] = last_dirs
        return True

    update_settings(_mutate)


def migrate_legacy_files() -> None:
    """Déplace vers les nouveaux emplacements (AppData/Documents) les fichiers
    laissés par d'anciennes versions de Distillat qui les créaient à côté de
    l'exe (ne s'applique qu'en mode compilé), et fusionne dans settings.json
    les anciens last_dirs.json/prompts.json s'ils existent encore (s'applique
    aussi en développement, ces fichiers vivaient déjà dans %APPDATA%\\Distillat
    dans les deux modes). Ne remplace/n'écrase jamais une donnée déjà présente
    au nouvel emplacement (ne perd jamais de données)."""
    _merge_legacy_settings_files()
    _migrate_legacy_api_key_to_profile()
    _cleanup_legacy_api_key_entry()

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

    # Tout le cycle (relecture, fusion, écriture, mise à la corbeille des
    # anciens fichiers) sous le verrou inter-processus : deux instances
    # lancées simultanément ne peuvent plus fusionner puis réécrire chacune
    # sa propre vision périmée de settings.json.
    with _settings_lock():
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
            _write_settings_file(settings)


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


def _profile_keyring_username(profile_id: str) -> str:
    """Chaque profil de clé API a sa propre entrée dans le Gestionnaire
    d'identification Windows, distincte de l'ancienne entrée unique
    KEYRING_USERNAME (conservée telle quelle pour la migration, voir
    _migrate_legacy_api_key_to_profile), pour que plusieurs clés puissent
    coexister sans jamais s'écraser entre elles."""
    return f"gemini_api_key_{profile_id}"


def list_profiles() -> list[dict]:
    """Liste des profils de clé API enregistrés (chacun {"id": str, "name":
    str, "model": str optionnel}, jamais la clé elle-même qui reste uniquement
    dans keyring), dans leur ordre d'enregistrement. "model" est absent des
    profils créés avant l'introduction du choix de modèle par profil :
    l'appelant doit résoudre cette absence via .get("model", MODEL_NAME) (voir
    app/gemini_client.py). Liste vide si aucun profil n'existe encore
    (première utilisation, ou migration pas encore effectuée)."""
    profiles = load_settings().get("api_profiles", [])
    return profiles if isinstance(profiles, list) else []


def save_profiles(profiles: list[dict]) -> None:
    """Remplace la liste complète des profils. À réserver aux cas où la liste
    cible ne dépend pas de la liste existante : pour ajouter, renommer ou
    supprimer un profil, utiliser add_profile()/rename_profile()/
    remove_profile(), qui relisent la liste sous le verrou inter-processus de
    settings.json - un enchaînement list_profiles() puis save_profiles() dans
    l'appelant perdrait le profil ajouté par une autre instance entre les
    deux (course fermée le 2026-07-22)."""
    save_settings({"api_profiles": profiles})


def add_profile(profile: dict) -> None:
    """Ajoute un profil ({"id": str, "name": str}) à la liste enregistrée,
    sous le verrou inter-processus de settings.json (voir save_profiles)."""
    def _mutate(data: dict) -> bool:
        profiles = data.get("api_profiles", [])
        if not isinstance(profiles, list):
            profiles = []
        profiles.append(profile)
        data["api_profiles"] = profiles
        return True

    update_settings(_mutate)


def rename_profile(profile_id: str, name: str) -> None:
    """Renomme un profil enregistré, sous le verrou inter-processus de
    settings.json (voir save_profiles). Un profil disparu entre-temps
    (supprimé par une autre instance) est ignoré sans erreur."""
    def _mutate(data: dict) -> bool:
        profiles = data.get("api_profiles", [])
        if not isinstance(profiles, list):
            return False
        changed = False
        for stored in profiles:
            if isinstance(stored, dict) and stored.get("id") == profile_id and stored.get("name") != name:
                stored["name"] = name
                changed = True
        return changed

    update_settings(_mutate)


def update_profile_model(profile_id: str, model: str) -> None:
    """Change le modèle Gemini choisi pour un profil enregistré, sous le
    verrou inter-processus de settings.json (voir save_profiles). Un profil
    disparu entre-temps (supprimé par une autre instance) est ignoré sans
    erreur."""
    def _mutate(data: dict) -> bool:
        profiles = data.get("api_profiles", [])
        if not isinstance(profiles, list):
            return False
        changed = False
        for stored in profiles:
            if isinstance(stored, dict) and stored.get("id") == profile_id and stored.get("model") != model:
                stored["model"] = model
                changed = True
        return changed

    update_settings(_mutate)


def remove_profile(profile_id: str) -> None:
    """Retire un profil de la liste enregistrée, sous le verrou
    inter-processus de settings.json (voir save_profiles). L'entrée keyring
    associée est à supprimer séparément via delete_profile_api_key()."""
    def _mutate(data: dict) -> bool:
        profiles = data.get("api_profiles", [])
        if not isinstance(profiles, list):
            return False
        kept = [
            stored
            for stored in profiles
            if not (isinstance(stored, dict) and stored.get("id") == profile_id)
        ]
        if len(kept) == len(profiles):
            return False
        data["api_profiles"] = kept
        return True

    update_settings(_mutate)


def load_profile_api_key(profile_id: str) -> str | None:
    """Retourne None aussi si le Gestionnaire d'identification Windows est
    indisponible (KeyringError), même garde-fou que load_api_key()."""
    try:
        return keyring.get_password(KEYRING_SERVICE_NAME, _profile_keyring_username(profile_id))
    except KeyringError:
        return None


def save_profile_api_key(profile_id: str, api_key: str) -> bool:
    """Retourne False (au lieu de laisser l'exception remonter) si le
    Gestionnaire d'identification Windows est indisponible ; à l'appelant
    d'en informer l'utilisateur."""
    try:
        keyring.set_password(KEYRING_SERVICE_NAME, _profile_keyring_username(profile_id), api_key)
        return True
    except KeyringError:
        return False


def delete_profile_api_key(profile_id: str) -> None:
    """Best-effort : si le Gestionnaire d'identification Windows est
    indisponible ou que l'entrée n'existe déjà plus, ne fait rien plutôt que
    de faire échouer la suppression du profil dans settings.json."""
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, _profile_keyring_username(profile_id))
    except KeyringError:
        pass


def find_profile_by_api_key(api_key: str, exclude_profile_id: str | None = None) -> dict | None:
    """Cherche, parmi les profils enregistrés, celui dont la clé API réelle
    (relue via keyring, jamais comparée sur autre chose que sa valeur) est
    identique à `api_key` - pour empêcher qu'une même clé Google se retrouve
    enregistrée sous deux profils distincts (2026-07-22), source de confusion
    (quel profil "possède" réellement ce compte ?) même si le suivi de quota
    par hash de clé (voir quota_tracker.py) resterait, lui, cohérent dans ce
    cas. `exclude_profile_id` ignore un profil précis (celui en cours de
    modification), pour qu'une clé inchangée ne se signale pas elle-même
    comme un doublon."""
    for profile in list_profiles():
        if profile["id"] == exclude_profile_id:
            continue
        if load_profile_api_key(profile["id"]) == api_key:
            return profile
    return None


def find_profile_by_name(name: str, exclude_profile_id: str | None = None) -> dict | None:
    """Cherche, parmi les profils enregistrés, celui dont le nom est
    identique à `name` (comparaison stricte, sensible à la casse et aux
    espaces superflus non retirés par l'appelant) - pour empêcher que deux
    profils distincts partagent le même nom, source de confusion dans la
    liste affichée par ProfilesDialog. `exclude_profile_id` ignore un profil
    précis (celui en cours de modification), pour qu'un nom inchangé ne se
    signale pas lui-même comme un doublon."""
    for profile in list_profiles():
        if profile["id"] == exclude_profile_id:
            continue
        if profile["name"] == name:
            return profile
    return None


def _migrate_legacy_api_key_to_profile() -> None:
    """Reprend l'ancienne clé API unique (KEYRING_USERNAME, avant l'ajout des
    profils multiples) dans un premier profil nommé "Défaut", si aucun profil
    n'a encore été créé. Cette migration ne fait elle-même qu'une copie vers
    la nouvelle entrée par profil, jamais de suppression : c'est
    _cleanup_legacy_api_key_entry() (appelée juste après par
    migrate_legacy_files()) qui supprime l'ancienne entrée, uniquement après
    avoir vérifié qu'une copie identique existe bien dans un profil. Le contrôle "aucun profil encore créé" et
    l'écriture se font sous le verrou inter-processus de settings.json : deux
    instances lancées simultanément au premier démarrage suivant la mise à
    jour créaient sinon chacune son propre profil "Défaut" (UUID différents),
    la dernière écriture écrasant l'autre et laissant son entrée keyring
    orpheline (course fermée le 2026-07-22)."""
    if list_profiles():
        # Pré-contrôle sans verrou : cas courant à chaque démarrage suivant,
        # aucune migration à faire, inutile de sérialiser quoi que ce soit.
        return
    with _settings_lock():
        settings = load_settings()
        profiles = settings.get("api_profiles", [])
        if isinstance(profiles, list) and profiles:
            return
        legacy_api_key = load_api_key()
        if not legacy_api_key:
            return
        profile_id = str(uuid.uuid4())
        if not save_profile_api_key(profile_id, legacy_api_key):
            return
        settings["api_profiles"] = [{"id": profile_id, "name": _DEFAULT_PROFILE_NAME}]
        _write_settings_file(settings)


def _cleanup_legacy_api_key_entry() -> None:
    """Supprime l'ancienne entrée keyring unique (KEYRING_USERNAME) une fois
    qu'une copie identique de sa valeur existe de façon vérifiée dans l'entrée
    keyring d'un des profils (relecture réelle via find_profile_by_api_key(),
    jamais une simple supposition que la migration a eu lieu) : laisser cette
    entrée indéfiniment gardait une copie du secret dans le Gestionnaire
    d'identification Windows, qui survivait même à une rotation de clé faite
    depuis l'UI (celle-ci ne modifie que l'entrée du profil), signalé à
    l'audit de sécurité du 2026-07-22. Si l'ancienne entrée contient une
    valeur qui ne correspond à aucun profil (cas imprévu : seule copie
    restante d'une clé), elle est laissée intacte, aucune suppression sur un
    simple doute. Appelée à chaque démarrage par migrate_legacy_files(),
    juste après _migrate_legacy_api_key_to_profile() : couvre donc aussi bien
    une migration qui vient d'avoir lieu que les installations migrées par
    une version antérieure, qui laissait toujours cette entrée derrière
    elle."""
    legacy_api_key = load_api_key()
    if not legacy_api_key:
        return
    if find_profile_by_api_key(legacy_api_key) is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
    except KeyringError:
        pass
