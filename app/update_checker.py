"""Vérification de la disponibilité d'une nouvelle version de Distillat via
GitHub Releases. Vérification silencieuse au démarrage uniquement (pas de menu
« À propos » dans cette application) : en cas d'erreur réseau ou si
l'application est déjà à jour, rien n'est affiché à l'utilisateur ; seule une
mise à jour trouvée produit un effet visible (bandeau dans MainWindow)."""
import json
import urllib.request
from urllib.error import URLError

from PyQt5.QtCore import QObject, pyqtSignal
from packaging.version import Version

from app.__version__ import VERSION

_RELEASES_API = "https://api.github.com/repos/Bruno-Aublet/Distillat/releases/latest"
_RELEASES_PAGE = "https://github.com/Bruno-Aublet/Distillat/releases/latest"
_REPO_PAGE = "https://github.com/Bruno-Aublet/Distillat"
_PROJECT_SITE_PAGE = "https://bruno-aublet.github.io/Distillat/"
_TIMEOUT = 5


def _normalize(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def _is_newer(latest: str, current: str) -> bool:
    """Retourne False (pas d'alerte) si l'une des deux chaînes n'est pas une
    version valide, plutôt que de laisser planter le thread réseau sur un tag
    GitHub malformé."""
    try:
        return Version(_normalize(latest)) > Version(_normalize(current))
    except Exception:
        return False


def _fetch_latest_tag() -> str | None:
    try:
        request = urllib.request.Request(
            _RELEASES_API, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.load(response)
        return data.get("tag_name")
    except (URLError, TimeoutError, ValueError, OSError):
        return None


class _ResultSignal(QObject):
    ready = pyqtSignal(str)


def check_for_updates_on_startup(main_window) -> None:
    """Lance la vérification dans un thread daemon pour ne pas retarder
    l'affichage de la fenêtre principale. Silencieux si aucune mise à jour
    n'est trouvée ou en cas d'erreur réseau ; n'affiche le bandeau que si une
    version plus récente existe réellement."""
    import threading

    signal = _ResultSignal()
    # Référence gardée sur main_window : sans elle, le signal pourrait être
    # détruit par le GC avant que la réponse réseau n'arrive.
    main_window._update_check_signal = signal

    def _on_result(latest_tag: str) -> None:
        if latest_tag and _is_newer(latest_tag, VERSION):
            if hasattr(main_window, "show_update_banner"):
                main_window.show_update_banner(_normalize(latest_tag))

    signal.ready.connect(_on_result)

    def _worker() -> None:
        latest_tag = _fetch_latest_tag()
        if latest_tag:
            signal.ready.emit(latest_tag)

    threading.Thread(target=_worker, daemon=True).start()


def releases_page_url() -> str:
    return _RELEASES_PAGE


def repo_page_url() -> str:
    return _REPO_PAGE


def project_site_url() -> str:
    return _PROJECT_SITE_PAGE
