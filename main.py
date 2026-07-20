"""Point d'entrée de l'application Distillat."""
import sys

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from app import config, i18n
from app.main_window import MainWindow


def main() -> None:
    config.migrate_legacy_files()
    i18n.init_language()

    app = QApplication(sys.argv)
    app.setApplicationName("Distillat")

    icon_path = config.get_app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
