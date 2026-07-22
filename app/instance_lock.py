"""Verrous inter-instances de Distillat, pour l'usage à plusieurs instances
lancées en parallèle sur la même machine (voir app.config) :
- verrou par profil de clé API (acquire_profile_lock()...), pour qu'une
  instance sache si un profil donné est déjà utilisé par une autre instance
  en cours d'exécution ;
- verrou par livre (acquire_book_lock()..., identifié par le hash SHA-256 du
  texte extrait, voir generation_resume.compute_book_hash()), pour que deux
  instances ne génèrent jamais la fiche du même livre en même temps - qu'il
  s'agisse d'une première génération ou de la reprise d'un même état
  interrompu, qui consommerait du quota en double pour un résultat identique
  et écraserait tour à tour le même fichier de reprise.
Contient aussi un mécanisme distinct de comptage des instances vivantes
(register_instance()/unregister_instance()/count_alive_instances()),
indépendant du profil : une instance sans profil actif (aucun profil libre,
ou aucun profil encore créé) ne détient aucun verrou de profil, donc serait
invisible à un comptage basé uniquement dessus."""
import json
import os
import platform
from pathlib import Path

import psutil

from app import config

# Limite du nombre d'instances Distillat pouvant tourner simultanément sur la
# machine, imposée par le bouton "nouvelle instance" de main_window.py : pas
# une contrainte de ressource par compte Google (contrairement aux profils),
# juste un plafond d'ergonomie pour ne pas se retrouver avec un nombre de
# fenêtres difficilement gérable à l'écran.
MAX_INSTANCES = 4

# Nombre maximal de tentatives de la boucle lecture/création atomique de
# acquire_profile_lock() : ne borne que le cas pathologique où le fichier de
# verrou apparaît et disparaît en boucle entre chaque tentative (plusieurs
# instances se battant exactement au même moment) ; en pratique la première ou
# la deuxième tentative conclut toujours.
_ACQUIRE_MAX_ATTEMPTS = 5

# Tolérance de comparaison entre la date de création de processus stockée dans
# un verrou/marqueur et celle du processus vivant portant le même PID : psutil
# renvoie un flottant en secondes stable pour un processus donné, la tolérance
# n'absorbe que d'éventuels arrondis de sérialisation JSON.
_CREATE_TIME_TOLERANCE_SECONDS = 1.0


def _lock_path(profile_id: str) -> Path:
    return config.get_settings_dir() / f".profile_lock_{profile_id}.json"


def _book_lock_path(book_hash: str) -> Path:
    return config.get_settings_dir() / f".book_lock_{book_hash}.json"


def _read_lock_file(lock_path: Path) -> dict | None:
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _process_create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _owner_content() -> dict:
    """Contenu écrit dans un fichier de verrou de profil ou de marqueur
    d'instance : le PID du processus propriétaire, sa date de création (pour
    distinguer un vrai propriétaire encore vivant d'un processus étranger
    ayant hérité du même PID après réutilisation par Windows, voir
    _owner_is_alive) et le nom de la machine (un %APPDATA% itinérant peut être
    partagé entre plusieurs machines, entre lesquelles les PID n'ont aucun
    sens croisé)."""
    pid = os.getpid()
    return {"pid": pid, "hostname": platform.node(), "created": _process_create_time(pid)}


def _owner_is_alive(data: dict) -> bool:
    """Le processus décrit par un fichier de verrou/marqueur est-il encore
    vivant ? Basé sur psutil (un simple os.kill(pid, 0) n'est pas fiable sous
    Windows, contrairement à Unix), avec deux précautions ajoutées le
    2026-07-22 :
    - si le fichier vient d'une autre machine (hostname différent, cas d'un
      %APPDATA% itinérant partagé), le PID local n'apprend rien sur le
      processus distant : considérer le propriétaire comme vivant plutôt que
      de risquer un double usage de la même clé API ;
    - si le fichier porte la date de création du processus propriétaire, la
      comparer à celle du processus vivant portant ce PID : un PID réutilisé
      par Windows pour un processus étranger n'est ainsi plus confondu avec le
      propriétaire d'origine (mort, lui), qui laissait sinon un profil marqué
      "utilisé" indéfiniment (ou un marqueur d'instance compté à tort dans
      MAX_INSTANCES).
    Un fichier sans date de création (écrit par une version antérieure de
    Distillat) reste jugé sur la seule existence du PID, comme avant. En cas
    d'erreur inattendue de psutil, considérer le propriétaire comme vivant :
    jamais de double usage d'une clé API sur un simple doute."""
    pid = data.get("pid")
    if not isinstance(pid, int):
        return False
    hostname = data.get("hostname")
    if isinstance(hostname, str) and hostname and hostname != platform.node():
        return True
    created = data.get("created")
    try:
        process = psutil.Process(pid)
        if isinstance(created, (int, float)):
            return abs(process.create_time() - created) <= _CREATE_TIME_TOLERANCE_SECONDS
        return True
    except psutil.NoSuchProcess:
        return False
    except Exception:
        return True


def _try_create_lock_file(lock_path: Path) -> bool:
    """Création exclusive et atomique (os.O_CREAT | os.O_EXCL) du fichier de
    verrou : échoue si le fichier existe déjà (une autre instance a gagné la
    course entre notre lecture et notre création), ou si l'écriture échoue
    réellement (le fichier incomplet est alors retiré, pour ne pas laisser
    traîner un verrou corrompu)."""
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(_owner_content()))
        return True
    except OSError:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _acquire_lock(lock_path: Path) -> bool:
    """Tente de réserver ce verrou (profil ou livre) pour le processus
    courant. Réussit si aucun verrou n'existe, ou si le verrou existant est
    orphelin (son propriétaire n'est plus vivant, voir _owner_is_alive) ;
    échoue si un autre processus vivant le détient déjà, sans jamais
    l'écraser dans ce cas. La création du fichier est atomique (voir
    _try_create_lock_file) : l'ancien enchaînement lire-vérifier-écrire
    laissait deux instances lancées simultanément lire toutes deux "verrou
    absent" puis l'écrire toutes deux, chacune se croyant propriétaire du
    même profil (course fermée le 2026-07-22). Échoue aussi (False) si le
    fichier de verrou ne peut pas être réellement écrit sur disque :
    retourner True sans verrou posé serait une fausse possession
    silencieuse, invisible des autres instances."""
    for _ in range(_ACQUIRE_MAX_ATTEMPTS):
        existing = _read_lock_file(lock_path)
        if existing is not None:
            if existing.get("pid") == os.getpid():
                return True
            if _owner_is_alive(existing):
                return False
        if lock_path.exists():
            # Verrou orphelin (propriétaire mort) ou fichier illisible : le
            # retirer pour laisser place à la création exclusive ci-dessous.
            # Si plusieurs instances le retirent en même temps, une seule
            # gagnera de toute façon la création atomique qui suit.
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                return False
        if _try_create_lock_file(lock_path):
            return True
        # Une autre instance a créé le verrou entre notre lecture et notre
        # tentative de création : reboucler pour examiner ce nouveau
        # propriétaire plutôt que de conclure tout de suite.
    return False


def _is_locked_elsewhere(lock_path: Path) -> bool:
    """Indique si ce verrou (profil ou livre) est actuellement détenu par un
    AUTRE processus vivant, en lecture seule (jamais de prise/relâchement de
    verrou par effet de bord). À utiliser pour un simple affichage (ex. le
    suffixe "utilisé par une autre fenêtre" de ProfilesDialog._reload_list())
    plutôt qu'un couple acquire/release : ce couple perturbait une autre
    instance en train de résoudre son propre profil au même instant (voir
    _resolve_active_profile()), en lui faisant croire momentanément que ce
    profil venait d'être libéré puis repris (audit du 2026-07-22)."""
    existing = _read_lock_file(lock_path)
    if existing is None:
        return False
    if existing.get("pid") == os.getpid():
        return False
    return _owner_is_alive(existing)


def _release_lock(lock_path: Path) -> None:
    """Ne supprime le verrou que s'il appartient bien au processus courant,
    pour ne jamais effacer par erreur le verrou d'une autre instance qui
    l'aurait entre-temps repris après un crash de la nôtre."""
    existing = _read_lock_file(lock_path)
    if existing is None or existing.get("pid") != os.getpid():
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def acquire_profile_lock(profile_id: str) -> bool:
    return _acquire_lock(_lock_path(profile_id))


def is_profile_locked_elsewhere(profile_id: str) -> bool:
    return _is_locked_elsewhere(_lock_path(profile_id))


def release_profile_lock(profile_id: str) -> None:
    _release_lock(_lock_path(profile_id))


def acquire_book_lock(book_hash: str) -> bool:
    """Réserve la génération de ce livre pour le processus courant, pris par
    SummarizeWorker.run() juste après le calcul du hash (seul moment où il
    est connu pour une première génération, l'extraction ayant lieu dans le
    worker) et avant tout appel à Gemini, puis libéré en fin de génération
    (succès comme échec : un échec partiel réécrit l'état de reprise avant
    de libérer, la reprise redevenant alors disponible pour n'importe quelle
    instance). Un crash laisse un verrou orphelin, démasqué par
    _owner_is_alive comme pour les profils."""
    return _acquire_lock(_book_lock_path(book_hash))


def is_book_locked_elsewhere(book_hash: str) -> bool:
    """Lecture seule, pour l'affichage en direct de PendingResumesDialog
    ("reprise en cours dans une autre fenêtre") et le refus anticipé au clic
    sur Résumer quand le hash est déjà connu via un état de reprise."""
    return _is_locked_elsewhere(_book_lock_path(book_hash))


def release_book_lock(book_hash: str) -> None:
    _release_lock(_book_lock_path(book_hash))


def _instance_marker_path(pid: int) -> Path:
    return config.get_settings_dir() / f".instance_{pid}.json"


def register_instance() -> None:
    """Pose le marqueur de l'instance courante (voir count_alive_instances),
    à appeler une fois au démarrage de MainWindow."""
    try:
        _instance_marker_path(os.getpid()).write_text(
            json.dumps(_owner_content()), encoding="utf-8"
        )
    except OSError:
        pass


def unregister_instance() -> None:
    """Retire le marqueur de l'instance courante, à appeler à la fermeture
    propre de MainWindow (closeEvent)."""
    try:
        _instance_marker_path(os.getpid()).unlink(missing_ok=True)
    except OSError:
        pass


def count_alive_instances() -> int:
    """Nombre d'instances Distillat actuellement vivantes sur la machine,
    utilisé par le bouton "nouvelle instance" pour ne jamais dépasser
    MAX_INSTANCES. Un marqueur dont le propriétaire n'est plus vivant (voir
    _owner_is_alive : instance qui a planté sans se désinscrire proprement,
    ou PID depuis réutilisé par un processus étranger) est un orphelin : il
    est supprimé au passage plutôt que compté, pour ne jamais accumuler
    indéfiniment des marqueurs morts (même esprit que la purge du journal
    d'appels API, voir gemini_client._trim_api_requests_log())."""
    count = 0
    for marker_path in config.get_settings_dir().glob(".instance_*.json"):
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and _owner_is_alive(data):
            count += 1
        else:
            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                pass
    return count
