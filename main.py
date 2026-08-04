"""Point d'entrée de l'application Distillat."""
import sys

from PyQt5.QtCore import QEventLoop, Qt, QTimer
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox, QSplashScreen

from app import config, i18n, instance_lock
from app.i18n import tr
from app.main_window import LEGACY_REPORT_EXTENSION, REPORT_EXTENSION, MainWindow

# Délai maximal d'attente d'une réponse "empty"/"busy" d'une instance
# existante interrogée lors du routage d'un fichier ouvert par double-clic
# (voir _find_empty_instance_and_route()) : couvre le cas d'une instance
# injoignable (venant de planter, canal pas encore prêt) sans jamais bloquer
# indéfiniment le nouveau process candidat.
_PROBE_TIMEOUT_MS = 800


def _detect_report_argument() -> str | None:
    """Chemin de fiche passé en argument (ouverture par double-clic Windows
    sur un .dstl ou, encore, un .distillat.json), ou None si l'application a
    été lancée normalement (aucun argument, ou argument non reconnu comme
    fiche)."""
    if len(sys.argv) < 2:
        return None
    candidate = sys.argv[1]
    if candidate.lower().endswith((REPORT_EXTENSION, LEGACY_REPORT_EXTENSION)):
        return candidate
    return None


def _probe_instance_is_empty(pid: int) -> bool | None:
    """Interroge l'instance de ce PID via son canal QLocalServer (voir
    main_window._setup_new_instance_channel()) pour savoir si elle est un
    écran d'accueil libre. Retourne True/False sur réponse reçue à temps,
    None si injoignable ou si le délai _PROBE_TIMEOUT_MS est dépassé (instance
    plantée, lente à répondre, ou canal pas encore ouvert)."""
    socket = QLocalSocket()
    loop = QEventLoop()
    result: dict[str, bool | None] = {"value": None}

    def on_ready_read():
        reply = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
        result["value"] = reply == "empty"
        loop.quit()

    def on_error(_error):
        loop.quit()

    socket.readyRead.connect(on_ready_read)
    socket.errorOccurred.connect(on_error)
    socket.connected.connect(lambda: (socket.write(b"is_empty?"), socket.flush()))
    socket.connectToServer(instance_lock.local_server_name_for_pid(pid))

    QTimer.singleShot(_PROBE_TIMEOUT_MS, loop.quit)
    loop.exec_()

    socket.disconnectFromServer()
    socket.deleteLater()
    return result["value"]


def _route_to_empty_instance(report_path: str) -> bool:
    """Cherche, parmi les instances Distillat actuellement vivantes, la
    première dont l'écran d'accueil est libre, et lui transmet le chemin de
    la fiche à ouvrir. Retourne True si une instance a effectivement pris en
    charge la fiche (le nouveau process candidat n'a alors plus qu'à quitter
    sans jamais afficher sa propre fenêtre), False sinon (aucune instance
    vivante, ou toutes occupées : à l'appelant de démarrer une nouvelle
    instance).

    Le verrou instance_lock.acquire_open_target_lock(pid) réserve un PID le
    temps de l'interroger et, le cas échéant, de lui transmettre le chemin :
    sans lui, deux fichiers .dstl ouverts en rafale pourraient tous les deux
    recevoir "empty" de la même instance et lui envoyer chacun un chemin
    différent, avec un résultat dépendant de l'ordre d'arrivée."""
    for pid in instance_lock.list_alive_instance_pids():
        if not instance_lock.acquire_open_target_lock(pid):
            continue
        try:
            if _probe_instance_is_empty(pid) is not True:
                continue
            socket = QLocalSocket()
            socket.connectToServer(instance_lock.local_server_name_for_pid(pid))
            if not socket.waitForConnected(_PROBE_TIMEOUT_MS):
                continue
            socket.write(f"open_report:{report_path}".encode("utf-8"))
            socket.flush()
            socket.disconnectFromServer()
            socket.deleteLater()
            return True
        finally:
            instance_lock.release_open_target_lock(pid)
    return False


def main() -> None:
    config.copy_sample_reports()
    config.ensure_dstl_file_association()
    i18n.init_language()

    app = QApplication(sys.argv)
    app.setApplicationName("Distillat")

    icon_path = config.get_app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    report_path = _detect_report_argument()
    if report_path is not None:
        if _route_to_empty_instance(report_path):
            return
        if instance_lock.count_alive_instances() >= instance_lock.MAX_INSTANCES:
            QMessageBox.warning(
                None,
                tr("main_window.max_instances_title"),
                tr("main_window.max_instances_message", max_instances=instance_lock.MAX_INSTANCES),
            )
            return

    splash = None
    if icon_path.exists():
        splash = QSplashScreen(QPixmap(str(icon_path)), Qt.WindowStaysOnTopHint)
        splash.show()
        app.processEvents()

    window = MainWindow(initial_report_path=report_path)
    if splash is not None:
        splash.finish(window)
    window.show()
    window._offer_pending_resumes()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
