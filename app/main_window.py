"""Fenêtre principale de l'application Distillat."""
import os
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QDragEnterEvent, QDropEvent, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.__version__ import VERSION
from app.book_report import BookReport
from app.docx_export import export_book_report_to_docx
from app.quota_tracker import QuotaSnapshot, QuotaTracker
from app.worker import SummarizeWorker


class LicenseDialog(QDialog):
    """Affiche le contenu du fichier LICENSE à la racine du projet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Licence")
        self.resize(650, 550)

        layout = QVBoxLayout(self)

        license_view = QTextEdit()
        license_view.setReadOnly(True)
        license_view.setFontFamily("Courier New")

        license_path = config.get_resource_dir() / "LICENSE"
        try:
            license_view.setPlainText(license_path.read_text(encoding="utf-8"))
        except OSError:
            license_view.setPlainText(
                f"Fichier de licence introuvable ({license_path}).\n\n"
                "Ce logiciel est distribué sous licence GNU GPL v3 : "
                "https://www.gnu.org/licenses/gpl-3.0.txt"
            )
        layout.addWidget(license_view)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def _draw_eye_icon(color: str, slashed: bool) -> QIcon:
    """Dessine une icône "œil" monochrome (dé/masquer un mot de passe),
    pour éviter l'emoji 👁 dont le rendu couleur natif ne peut pas être
    recoloré par une feuille de style Qt."""
    size = 20
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    painter.setPen(pen)

    eye_rect = QRectF(2, 6, size - 4, size - 12)
    painter.drawArc(eye_rect, 0, 180 * 16)
    painter.drawArc(eye_rect, 180 * 16, 180 * 16)
    painter.drawEllipse(QPointF(size / 2, size / 2), 2.2, 2.2)

    if slashed:
        painter.drawLine(QPointF(3, size - 4), QPointF(size - 3, 4))

    painter.end()
    return QIcon(pixmap)


class ApiKeyDialog(QDialog):
    """Boîte de dialogue de saisie de la clé API Gemini, stockée de façon
    chiffrée via le Gestionnaire d'identification Windows (keyring)."""

    def __init__(self, parent=None, current_api_key: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Clé API Gemini")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Récupérez votre clé gratuite sur "
            '<a href="https://aistudio.google.com/apikey">Google AI Studio</a>. '
            "Elle sera enregistrée de façon chiffrée via le Gestionnaire "
            "d'identification Windows — ne la partagez pas."
        )
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

        warning = QLabel(
            "⚠️ Ne cliquez jamais sur « Activer la facturation » : "
            "vous perdriez le palier gratuit de Gemini."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b02a2a; font-weight: bold;")
        warning.setAlignment(Qt.AlignCenter)
        layout.addWidget(warning)

        form = QFormLayout()
        key_row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("AIza...")
        if current_api_key:
            self.key_input.setText(current_api_key)
        key_row.addWidget(self.key_input)

        self.toggle_visibility_button = QPushButton()
        self.toggle_visibility_button.setFixedSize(32, 26)
        self.toggle_visibility_button.setCheckable(True)
        self.toggle_visibility_button.setToolTip("Afficher/masquer la clé")
        self.toggle_visibility_button.setIcon(_draw_eye_icon("#555555", slashed=False))
        self.toggle_visibility_button.setStyleSheet(
            """
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #9aa5b1;
                border-radius: 4px;
                outline: none;
            }
            QPushButton:checked, QPushButton:checked:hover, QPushButton:checked:pressed {
                background-color: #d8e4f2;
                border: 1px solid #4a90d9;
                outline: none;
            }
            QPushButton:focus {
                outline: none;
            }
            """
        )
        self.toggle_visibility_button.setFocusPolicy(Qt.NoFocus)
        self.toggle_visibility_button.toggled.connect(self._on_toggle_visibility)
        key_row.addWidget(self.toggle_visibility_button)

        form.addRow("Clé API :", key_row)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_toggle_visibility(self, checked: bool) -> None:
        self.key_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.toggle_visibility_button.setIcon(_draw_eye_icon("#2a5fa0" if checked else "#555555", slashed=checked))

    def api_key(self) -> str:
        return self.key_input.text().strip()


SUPPORTED_EXTENSIONS = (".epub", ".pdf")
REPORT_EXTENSION = ".distillat.json"
DROPPABLE_EXTENSIONS = SUPPORTED_EXTENSIONS + (REPORT_EXTENSION,)


class DropZone(QLabel):
    """Zone de glisser-déposer pour les fichiers EPUB/PDF (à résumer) et les
    fiches Distillat déjà générées (.distillat.json, à ouvrir directement)."""

    def __init__(self, on_file_dropped, on_report_dropped, parent=None):
        super().__init__(parent)
        self.on_file_dropped = on_file_dropped
        self.on_report_dropped = on_report_dropped
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(180)
        self.setText("📚\n\nGlissez-déposez un fichier EPUB, PDF ou une fiche .distillat.json ici\nou cliquez pour parcourir")
        self._set_style(active=False)

    def _set_style(self, active: bool) -> None:
        border_color = "#4a90d9" if active else "#9aa5b1"
        background = "#eaf2fb" if active else "#f7f9fb"
        self.setStyleSheet(
            f"""
            QLabel {{
                border: 2px dashed {border_color};
                border-radius: 12px;
                background-color: {background};
                font-size: 15px;
                color: #333;
                padding: 20px;
            }}
            """
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(url.toLocalFile().lower().endswith(DROPPABLE_EXTENSIONS) for url in urls):
                self._set_style(active=True)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_style(active=False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_style(active=False)
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            lower_path = path.lower()
            if lower_path.endswith(REPORT_EXTENSION):
                self.on_report_dropped(path)
                event.acceptProposedAction()
                return
            if lower_path.endswith(SUPPORTED_EXTENSIONS):
                self.on_file_dropped(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def mousePressEvent(self, event) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir un fichier EPUB, PDF ou une fiche",
            "",
            "Tous les formats pris en charge (*.epub *.pdf *.distillat.json);;"
            "Fichiers EPUB/PDF (*.epub *.pdf);;"
            "Fiches Distillat (*.distillat.json)",
        )
        if not path:
            return
        if path.lower().endswith(REPORT_EXTENSION):
            self.on_report_dropped(path)
        else:
            self.on_file_dropped(path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Distillat v{VERSION} — Résumé de livres en français avec Gemini")
        self._size_to_available_screen()

        self.selected_book_path: str | None = None
        self.worker: SummarizeWorker | None = None
        self.last_result: BookReport | None = None
        self._last_result_source_stem: str | None = None
        self._report_dirty = False
        self.quota_tracker = QuotaTracker(daily_state_path=config.get_settings_dir() / ".quota_state.json")

        self._retry_seconds_left = 0
        self._retry_quota_id = ""
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(1000)
        self._retry_timer.timeout.connect(self._on_retry_tick)

        self._build_ui()
        self._update_quota_display(self.quota_tracker.snapshot())
        self._ensure_api_key(prompt_if_missing=False)

    def _size_to_available_screen(self) -> None:
        target_width, target_height = 820, 950

        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if available is None:
            self.resize(target_width, target_height)
            return

        width = min(target_width, available.width())
        height = min(target_height, available.height())
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 6)

        header = QHBoxLayout()
        title_label = QLabel("Distillat")
        title_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.addWidget(title_label)
        header.addStretch()
        self.api_key_button = QPushButton("Clé API…")
        self.api_key_button.clicked.connect(self._on_edit_api_key)
        header.addWidget(self.api_key_button)
        layout.addLayout(header)

        self.drop_zone = DropZone(
            on_file_dropped=self._on_file_selected,
            on_report_dropped=self._on_report_dropped,
        )
        layout.addWidget(self.drop_zone)

        file_row = QHBoxLayout()
        self.file_label = QLabel("Aucun fichier sélectionné")
        self.file_label.setStyleSheet("color: #555;")
        file_row.addWidget(self.file_label)
        file_row.addStretch()

        self.remove_file_button = QPushButton("✕ Retirer")
        self.remove_file_button.setCursor(Qt.PointingHandCursor)
        self.remove_file_button.setStyleSheet(
            """
            QPushButton {
                color: #b02a2a;
                border: none;
                background: transparent;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
            """
        )
        self.remove_file_button.clicked.connect(self._on_remove_file_clicked)
        self.remove_file_button.hide()
        file_row.addWidget(self.remove_file_button)
        layout.addLayout(file_row)

        action_row = QHBoxLayout()
        self.summarize_button = QPushButton("Résumer")
        self.summarize_button.setMinimumHeight(36)
        self.summarize_button.clicked.connect(self._on_summarize_clicked)
        action_row.addWidget(self.summarize_button)
        self._set_summarize_button_enabled(False)

        self.save_button = QPushButton("Sauvegarder en .docx")
        self.save_button.setEnabled(False)
        self.save_button.setMinimumHeight(36)
        self.save_button.clicked.connect(self._on_save_clicked)
        action_row.addWidget(self.save_button)
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #555;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        quota_block = QVBoxLayout()
        quota_block.setSpacing(0)

        self.quota_label = QLabel("")
        self.quota_label.setStyleSheet("color: #555; font-size: 13px;")
        self.quota_label.setWordWrap(True)
        self.quota_label.setAlignment(Qt.AlignCenter)
        quota_block.addWidget(self.quota_label)

        quota_disclaimer = QLabel(
            "Ces compteurs sont des estimations locales à cette application. "
            "Ils seront faussés si la même clé API est aussi utilisée ailleurs en parallèle."
        )
        quota_disclaimer.setStyleSheet("color: #888; font-size: 10px; font-style: italic;")
        quota_disclaimer.setWordWrap(True)
        quota_disclaimer.setAlignment(Qt.AlignCenter)
        quota_block.addWidget(quota_disclaimer)

        layout.addLayout(quota_block)

        self.quota_threshold_label = QLabel("")
        self.quota_threshold_label.setStyleSheet("color: #a06a00; font-weight: bold; font-size: 13px;")
        self.quota_threshold_label.setWordWrap(True)
        self.quota_threshold_label.setAlignment(Qt.AlignCenter)
        self.quota_threshold_label.hide()
        layout.addWidget(self.quota_threshold_label)

        self.quota_warning_label = QLabel("")
        self.quota_warning_label.setStyleSheet("color: #b02a2a; font-weight: bold; font-size: 13px;")
        self.quota_warning_label.setWordWrap(True)
        self.quota_warning_label.setAlignment(Qt.AlignCenter)
        self.quota_warning_label.hide()
        layout.addWidget(self.quota_warning_label)

        result_row = QHBoxLayout()
        self.load_report_button = QPushButton("Charger une fiche…")
        self.load_report_button.clicked.connect(self._on_load_report_clicked)
        result_row.addWidget(self.load_report_button)

        self.save_report_button = QPushButton("Sauvegarder la fiche…")
        self.save_report_button.setEnabled(False)
        self.save_report_button.clicked.connect(self._on_save_report_clicked)
        result_row.addWidget(self.save_report_button)

        self.close_report_button = QPushButton("Fermer la fiche")
        self.close_report_button.setEnabled(False)
        self.close_report_button.clicked.connect(self._on_close_report_clicked)
        result_row.addWidget(self.close_report_button)
        result_row.addStretch()
        layout.addLayout(result_row)

        self.result_tabs = QTabWidget()
        layout.addWidget(self.result_tabs, stretch=1)

        self._build_cover_tab()
        self._build_summary_tab()
        self._build_detailed_summary_tab()
        self._build_characters_tab()
        self._build_analysis_tab()

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, -10, 0, 0)
        self.footer_button = QPushButton("Copyright 2026 Bruno Aublet - Licence GNU GPL v3")
        self.footer_button.setCursor(Qt.PointingHandCursor)
        self.footer_button.setStyleSheet(
            """
            QPushButton {
                color: #999;
                font-size: 11px;
                border: none;
                background: transparent;
                text-align: left;
                padding: 0;
            }
            QPushButton:hover {
                color: #4a90d9;
                text-decoration: underline;
            }
            """
        )
        self.footer_button.clicked.connect(self._on_show_license)
        footer_row.addWidget(self.footer_button)
        footer_row.addStretch()
        layout.addLayout(footer_row)

    def _build_cover_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.addStretch()

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(220, 320)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet("border: 1px solid #ccc; background-color: #f0f0f0;")
        self.cover_label.setText("Pas de couverture")
        cover_row = QHBoxLayout()
        cover_row.addStretch()
        cover_row.addWidget(self.cover_label)
        cover_row.addStretch()
        tab_layout.addLayout(cover_row)

        self.book_title_label = QLabel("")
        self.book_title_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 14px;")
        self.book_title_label.setWordWrap(True)
        self.book_title_label.setAlignment(Qt.AlignCenter)
        tab_layout.addWidget(self.book_title_label)

        self.book_author_label = QLabel("")
        self.book_author_label.setStyleSheet("font-size: 14px; color: #555;")
        self.book_author_label.setWordWrap(True)
        self.book_author_label.setAlignment(Qt.AlignCenter)
        tab_layout.addWidget(self.book_author_label)

        tab_layout.addStretch()
        self.result_tabs.addTab(tab, "Couverture")

    def _build_summary_tab(self) -> None:
        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setStyleSheet("font-size: 15px;")
        self.summary_view.setPlaceholderText("Le résumé court en français apparaîtra ici après traitement.")
        self.result_tabs.addTab(self.summary_view, "Résumé court")

    def _build_detailed_summary_tab(self) -> None:
        self.detailed_summary_view = QTextEdit()
        self.detailed_summary_view.setReadOnly(True)
        self.detailed_summary_view.setStyleSheet("font-size: 15px;")
        self.detailed_summary_view.setPlaceholderText(
            "Le résumé détaillé en français apparaîtra ici après traitement."
        )
        self.result_tabs.addTab(self.detailed_summary_view, "Résumé détaillé")

    def _build_characters_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: white;")
        self.characters_layout = QVBoxLayout(container)
        self.characters_layout.setContentsMargins(6, 6, 6, 6)
        self.characters_layout.addStretch()
        scroll.setWidget(container)

        self.characters_placeholder = QLabel(
            "Les fiches des personnages principaux apparaîtront ici après traitement."
        )
        self.characters_placeholder.setStyleSheet("color: #888; font-size: 15px;")
        self.characters_placeholder.setWordWrap(True)
        self.characters_layout.insertWidget(0, self.characters_placeholder)

        self.result_tabs.addTab(scroll, "Personnages")

    def _build_analysis_tab(self) -> None:
        self.analysis_view = QTextEdit()
        self.analysis_view.setReadOnly(True)
        self.analysis_view.setStyleSheet("font-size: 15px;")
        self.analysis_view.setPlaceholderText("L'analyse littéraire apparaîtra ici après traitement.")
        self.result_tabs.addTab(self.analysis_view, "Analyse")

    def _clear_characters_tab(self) -> None:
        while self.characters_layout.count() > 1:  # garder le stretch final
            item = self.characters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _populate_characters_tab(self, characters: list) -> None:
        self._clear_characters_tab()
        if not characters:
            placeholder = QLabel("Aucun personnage principal identifié.")
            placeholder.setStyleSheet("color: #888; padding: 12px;")
            self.characters_layout.insertWidget(0, placeholder)
            return

        for index, character in enumerate(characters):
            card = QWidget()
            card.setStyleSheet(
                """
                QWidget {
                    background-color: #f7f9fb;
                    border: 1px solid #dde3ea;
                    border-radius: 8px;
                }
                """
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)

            name_label = QLabel(character.name)
            name_label.setStyleSheet("font-size: 16px; font-weight: bold; border: none;")
            name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card_layout.addWidget(name_label)

            description_label = QLabel(character.description)
            description_label.setWordWrap(True)
            description_label.setStyleSheet("font-size: 15px; color: #333; border: none;")
            description_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card_layout.addWidget(description_label)

            self.characters_layout.insertWidget(index, card)
            self.characters_layout.insertSpacing(index + 1, 10)

    def _ensure_api_key(self, prompt_if_missing: bool = True) -> str | None:
        api_key = config.load_api_key()
        if api_key:
            return api_key
        if not prompt_if_missing:
            return None
        return self._prompt_for_api_key()

    def _prompt_for_api_key(self) -> str | None:
        dialog = ApiKeyDialog(self, current_api_key=config.load_api_key())
        if dialog.exec_() == QDialog.Accepted:
            api_key = dialog.api_key()
            if not api_key:
                QMessageBox.warning(self, "Clé API manquante", "Veuillez saisir une clé API valide.")
                return None
            config.save_api_key(api_key)
            return api_key
        return None

    def _on_edit_api_key(self) -> None:
        self._prompt_for_api_key()

    def _on_show_license(self) -> None:
        LicenseDialog(self).exec_()

    def _set_summarize_button_enabled(self, enabled: bool) -> None:
        self.summarize_button.setEnabled(enabled)
        if enabled:
            self.summarize_button.setStyleSheet(
                """
                QPushButton {
                    background-color: #2ea04f;
                    color: white;
                    font-weight: bold;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #268245;
                }
                """
            )
        else:
            self.summarize_button.setStyleSheet("")

    def _on_file_selected(self, path: str) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        self.selected_book_path = path
        self.file_label.setText(f"Fichier sélectionné : {os.path.basename(path)}")
        self.remove_file_button.show()
        self._set_summarize_button_enabled(True)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText("")
        self.last_result = None
        self._report_dirty = False
        self._clear_result_tabs()

    def _on_remove_file_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        self.selected_book_path = None
        self.file_label.setText("Aucun fichier sélectionné")
        self.remove_file_button.hide()
        self._set_summarize_button_enabled(False)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText("")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.last_result = None
        self._report_dirty = False
        self._clear_result_tabs()

    def _on_close_report_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        self.last_result = None
        self._report_dirty = False
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText("")
        self._clear_result_tabs()
        self.result_tabs.setCurrentIndex(0)

    def _confirm_discard_unsaved_report(self) -> bool:
        """Retourne True si on peut continuer (rien à perdre, ou l'utilisateur confirme)."""
        if not self._report_dirty or not self.last_result:
            return True
        answer = QMessageBox.question(
            self,
            "Fiche non sauvegardée",
            "La fiche actuelle n'a pas été sauvegardée. Voulez-vous continuer sans la sauvegarder ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def closeEvent(self, event) -> None:
        if self._confirm_discard_unsaved_report():
            event.accept()
        else:
            event.ignore()

    def _on_summarize_clicked(self) -> None:
        if not self.selected_book_path:
            return

        if not self._confirm_discard_unsaved_report():
            return

        api_key = self._ensure_api_key(prompt_if_missing=True)
        if not api_key:
            return

        self._set_summarize_button_enabled(False)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self._clear_result_tabs()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label.setText("Démarrage du traitement…")

        self._retry_timer.stop()
        self.quota_warning_label.hide()
        self.worker = SummarizeWorker(self.selected_book_path, api_key, self.quota_tracker)
        self.worker.progress.connect(self._on_progress)
        self.worker.quota_updated.connect(self._update_quota_display)
        self.worker.retry_wait.connect(self._on_retry_wait)
        self.worker.finished_ok.connect(self._on_finished_ok)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.status_label.setText(message)

    def _on_retry_wait(self, wait_seconds: float, quota_id: str) -> None:
        self._retry_seconds_left = max(1, round(wait_seconds))
        self._retry_quota_id = quota_id
        self._render_retry_countdown()
        self._retry_timer.start()

    def _on_retry_tick(self) -> None:
        self._retry_seconds_left -= 1
        if self._retry_seconds_left <= 0:
            self._retry_timer.stop()
            self.quota_warning_label.hide()
            return
        self._render_retry_countdown()

    def _render_retry_countdown(self) -> None:
        quota_part = f" (quota : {self._retry_quota_id})" if self._retry_quota_id else ""
        self.quota_warning_label.setText(
            f"⏳ Quota atteint{quota_part}, nouvelle tentative dans {self._retry_seconds_left}s…"
        )
        self.quota_warning_label.show()

    QUOTA_WARNING_THRESHOLD = 0.8

    def _update_quota_display(self, snapshot: QuotaSnapshot) -> None:
        self.quota_label.setText(
            f"Tokens — entrée : {snapshot.input_tokens_total:,} · "
            f"sortie : {snapshot.output_tokens_total:,} · "
            f"total : {snapshot.input_tokens_total + snapshot.output_tokens_total:,}\n"
            f"Requêtes/minute : {snapshot.requests_per_minute}/{snapshot.rpm_limit} · "
            f"Tokens/minute : {snapshot.tokens_per_minute:,}/{snapshot.tpm_limit:,} · "
            f"Requêtes/jour : {snapshot.requests_today}/{snapshot.rpd_limit}"
            .replace(",", " ")
        )
        self._check_quota_thresholds(snapshot)

    def _check_quota_thresholds(self, snapshot: QuotaSnapshot) -> None:
        warnings = []
        if snapshot.requests_today >= snapshot.rpd_limit * self.QUOTA_WARNING_THRESHOLD:
            warnings.append(
                f"{snapshot.requests_today}/{snapshot.rpd_limit} requêtes utilisées aujourd'hui"
            )
        if snapshot.requests_per_minute >= snapshot.rpm_limit * self.QUOTA_WARNING_THRESHOLD:
            warnings.append(
                f"{snapshot.requests_per_minute}/{snapshot.rpm_limit} requêtes sur la minute en cours"
            )
        if snapshot.tokens_per_minute >= snapshot.tpm_limit * self.QUOTA_WARNING_THRESHOLD:
            tpm_text = f"{snapshot.tokens_per_minute:,}/{snapshot.tpm_limit:,} tokens sur la minute en cours"
            warnings.append(tpm_text.replace(",", " "))

        if warnings:
            new_text = "⚠️ Quota bientôt atteint : " + " · ".join(warnings)
            if self.quota_threshold_label.text() != new_text:
                self.quota_threshold_label.setText(new_text)
            if self.quota_threshold_label.isHidden():
                self.quota_threshold_label.show()
        elif not self.quota_threshold_label.isHidden():
            self.quota_threshold_label.hide()

    def _clear_result_tabs(self) -> None:
        self.book_title_label.setText("")
        self.book_author_label.setText("")
        self.cover_label.clear()
        self.cover_label.setText("Pas de couverture")
        self.summary_view.clear()
        self.detailed_summary_view.clear()
        self.analysis_view.clear()
        self._clear_characters_tab()
        placeholder = QLabel("Les fiches des personnages principaux apparaîtront ici après traitement.")
        placeholder.setStyleSheet("color: #888; padding: 12px;")
        placeholder.setWordWrap(True)
        self.characters_layout.insertWidget(0, placeholder)

    def _display_book_report(self, result: BookReport) -> None:
        self.book_title_label.setText(result.book_title)
        self.book_author_label.setText(result.author)

        if result.cover_image:
            pixmap = QPixmap()
            if pixmap.loadFromData(result.cover_image):
                self.cover_label.setPixmap(
                    pixmap.scaled(
                        self.cover_label.width(),
                        self.cover_label.height(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            else:
                self.cover_label.setText("Pas de couverture")
        else:
            self.cover_label.setText("Pas de couverture")

        self.summary_view.setPlainText(result.summary_text)
        self.detailed_summary_view.setPlainText(
            result.detailed_summary_text or "Aucun résumé détaillé disponible pour cette fiche."
        )
        self.analysis_view.setPlainText(result.analysis_text)
        self._populate_characters_tab(result.characters)

    def _on_finished_ok(self, result: BookReport) -> None:
        self._retry_timer.stop()
        self.last_result = result
        self._last_result_source_stem = (
            Path(self.selected_book_path).stem if self.selected_book_path else None
        )
        self._report_dirty = True
        self._display_book_report(result)
        self.result_tabs.setCurrentIndex(1)
        mode = (
            f"Résumé consolidé à partir de {result.chapter_count} chapitres."
            if result.was_split
            else "Résumé produit en une seule requête."
        )
        self.status_label.setText(f"Terminé. {mode}")
        self.progress_bar.setValue(self.progress_bar.maximum())
        self._set_summarize_button_enabled(True)
        self.save_button.setEnabled(True)
        self.save_report_button.setEnabled(True)
        self.close_report_button.setEnabled(True)
        # Filet de sécurité : après une rafale de show()/hide()/setText() sur les
        # labels de quota pendant le traitement, force un repaint pour garantir
        # que le contenu final s'affiche (observé bloqué visuellement sur Windows
        # après un traitement long, bien que les données soient à jour en mémoire).
        self.repaint()

    def _on_failed(self, error_message: str) -> None:
        self._retry_timer.stop()
        self.status_label.setText("Une erreur est survenue.")
        self._set_summarize_button_enabled(True)
        if "quota" in error_message.lower():
            self.quota_warning_label.setText(
                "🚫 Quota Gemini dépassé, l'appli ne peut plus faire de requêtes pour l'instant. "
                "Réessayez plus tard (le quota par minute se réinitialise en 60 s, "
                "le quota journalier à minuit)."
            )
            self.quota_warning_label.show()
        QMessageBox.critical(self, "Erreur", error_message)

    def _on_save_clicked(self) -> None:
        if not self.last_result:
            return

        base_name = self._last_result_source_stem or self.last_result.book_title
        default_path = str(config.get_reports_dir() / f"{base_name}.docx")
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le résumé", default_path, "Documents Word (*.docx)"
        )
        if not path:
            return
        if not path.lower().endswith(".docx"):
            path += ".docx"

        try:
            export_book_report_to_docx(self.last_result, path)
            QMessageBox.information(self, "Sauvegarde réussie", f"Document enregistré :\n{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erreur de sauvegarde", str(exc))

    def _on_save_report_clicked(self) -> None:
        if not self.last_result:
            return

        default_path = str(
            config.get_reports_dir() / self.last_result.suggested_filename(self._last_result_source_stem)
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer la fiche", default_path, "Fiches Distillat (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(REPORT_EXTENSION):
            path = path[: -len(".json")] if path.lower().endswith(".json") else path
            path += REPORT_EXTENSION

        try:
            self.last_result.save(Path(path))
            self._report_dirty = False
            QMessageBox.information(self, "Sauvegarde réussie", f"Fiche enregistrée :\n{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erreur de sauvegarde", str(exc))

    def _on_load_report_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger une fiche", str(config.get_reports_dir()), "Fiches Distillat (*.json)"
        )
        if not path:
            return
        self._load_report_from_path(path)

    def _on_report_dropped(self, path: str) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        self._load_report_from_path(path)

    def _load_report_from_path(self, path: str) -> None:
        try:
            result = BookReport.load(Path(path))
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "Erreur de chargement", f"Fichier illisible : {exc}")
            return

        self.last_result = result
        self._last_result_source_stem = Path(path).stem.removesuffix(".distillat")
        self._report_dirty = False
        self._display_book_report(result)
        self.status_label.setText(f"Fiche chargée depuis {os.path.basename(path)}.")
        self.save_button.setEnabled(True)
        self.save_report_button.setEnabled(True)
        self.close_report_button.setEnabled(True)
