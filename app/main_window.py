"""Fenêtre principale de l'application Distillat."""
import hashlib
import os
import platform
import re
import subprocess
import sys
import uuid
import webbrowser
import winsound
from pathlib import Path

from PyQt5.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
)
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QAction,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import config, generation_resume, i18n, instance_lock
from app.__version__ import VERSION
from app.i18n import tr
from app.update_checker import check_for_updates_on_startup, releases_page_url, repo_page_url
from app.book_report import BookReport, Character, sanitize_filename
from app.cover_image import shrink_cover_image
from app.gemini_client import MODEL_NAME, default_prompt_templates, log_api_event
from app.pdf_export import export_book_report_to_pdf
from app.prompts_store import load_custom_prompts, reset_custom_prompt, save_custom_prompts
from app.quota_tracker import QuotaSnapshot, QuotaTracker, save_quota_limits
from app.worker import SummarizeWorker


class LicenseDialog(QDialog):
    """Affiche le contenu du fichier LICENSE à la racine du projet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("license_dialog.window_title"))
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
                tr("license_dialog.file_not_found", license_path=license_path)
            )
        layout.addWidget(license_view)

        ok_row = QHBoxLayout()
        ok_row.addStretch()
        ok_button = QPushButton(tr("license_dialog.ok_button"))
        ok_button.clicked.connect(self.accept)
        ok_row.addWidget(ok_button)
        layout.addLayout(ok_row)


class ChangelogDialog(QDialog):
    """Affiche le contenu du fichier CHANGELOG.md à la racine du projet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("changelog_dialog.window_title"))
        self.resize(650, 550)

        layout = QVBoxLayout(self)

        changelog_view = QTextEdit()
        changelog_view.setReadOnly(True)

        changelog_path = config.get_resource_dir() / "CHANGELOG.md"
        try:
            changelog_view.setPlainText(changelog_path.read_text(encoding="utf-8"))
        except OSError:
            changelog_view.setPlainText(
                tr("changelog_dialog.file_not_found", changelog_path=changelog_path)
            )
        layout.addWidget(changelog_view)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton(tr("changelog_dialog.close_button"))
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)


class QuotaHelpDialog(QDialog):
    """Explique en langage clair, sans jargon technique, le fonctionnement
    des quotas et des requêtes Gemini (public visé : non technique)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("quota_help_dialog.window_title"))
        self.resize(480, 420)

        layout = QVBoxLayout(self)

        intro = QLabel(tr("quota_help_dialog.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for title_key, body_key in (
            ("quota_help_dialog.requests_title", "quota_help_dialog.requests_body"),
            ("quota_help_dialog.failures_title", "quota_help_dialog.failures_body"),
            ("quota_help_dialog.resume_title", "quota_help_dialog.resume_body"),
        ):
            title = QLabel(tr(title_key))
            title.setStyleSheet("font-weight: bold;")
            title.setWordWrap(True)
            layout.addWidget(title)

            body = QLabel(tr(body_key))
            body.setWordWrap(True)
            layout.addWidget(body)

        layout.addStretch()

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton(tr("quota_help_dialog.close_button"))
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)


class ExtraTextDialog(QDialog):
    """Fenêtre non modale affichant le texte que Gemini a produit en trop
    après le premier objet JSON exploité, pour que l'utilisateur puisse le
    lire et le copier à son rythme sans bloquer le reste de l'application
    (il peut continuer à consulter/éditer la fiche pendant que cette fenêtre
    reste ouverte)."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle(tr("extra_text_dialog.window_title"))
        self.resize(600, 450)

        layout = QVBoxLayout(self)

        explanation = QLabel(tr("extra_text_dialog.explanation"))
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #555;")
        layout.addWidget(explanation)

        text_view = QTextEdit()
        text_view.setPlainText(text)
        text_view.setReadOnly(True)
        layout.addWidget(text_view)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton(tr("extra_text_dialog.close_button"))
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)


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


class _ApiKeyInputRow:
    """Champ de saisie de clé API avec bouton oeil (dé/masquer), factorisé
    entre ProfileEditDialog (seul appelant désormais) : un seul endroit à
    faire évoluer si ce composant change."""

    def __init__(self, layout: QFormLayout, label: str, placeholder: str, initial_value: str = "") -> None:
        key_row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText(placeholder)
        if initial_value:
            self.key_input.setText(initial_value)
        key_row.addWidget(self.key_input)

        self.toggle_visibility_button = QPushButton()
        self.toggle_visibility_button.setFixedSize(32, 26)
        self.toggle_visibility_button.setCheckable(True)
        self.toggle_visibility_button.setToolTip(tr("eye_icon.tooltip"))
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

        layout.addRow(label, key_row)

    def _on_toggle_visibility(self, checked: bool) -> None:
        self.key_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.toggle_visibility_button.setIcon(_draw_eye_icon("#2a5fa0" if checked else "#555555", slashed=checked))

    def value(self) -> str:
        return self.key_input.text().strip()


class ProfileEditDialog(QDialog):
    """Boîte de dialogue d'ajout ou de modification d'un profil de clé API
    Gemini (nom + clé), la clé restant stockée de façon chiffrée via le
    Gestionnaire d'identification Windows (keyring, voir app.config)."""

    def __init__(self, parent=None, current_name: str = "", current_api_key: str | None = None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("profiles_dialog.edit_window_title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        info = QLabel(tr("profiles_dialog.info"))
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

        account_warning = QLabel(tr("profiles_dialog.account_warning"))
        account_warning.setWordWrap(True)
        account_warning.setAlignment(Qt.AlignCenter)
        layout.addWidget(account_warning)

        warning = QLabel(tr("profiles_dialog.warning"))
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b02a2a; font-weight: bold;")
        warning.setAlignment(Qt.AlignCenter)
        layout.addWidget(warning)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(tr("profiles_dialog.name_placeholder"))
        if current_name:
            self.name_input.setText(current_name)
        form.addRow(tr("profiles_dialog.name_label"), self.name_input)

        self._key_row = _ApiKeyInputRow(
            form,
            tr("profiles_dialog.key_label"),
            tr("profiles_dialog.key_placeholder"),
            current_api_key or "",
        )
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton(tr("profiles_dialog.save_button"), QDialogButtonBox.AcceptRole)
        buttons.addButton(tr("profiles_dialog.cancel_button"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def profile_name(self) -> str:
        return self.name_input.text().strip()

    def api_key(self) -> str:
        return self._key_row.value()


class _DeselectableListWidget(QListWidget):
    """QListWidget dont un clic dans une zone vide (hors de tout item)
    désélectionne l'item courant, plutôt que de laisser la sélection
    précédente active sans retour visuel de survol (comportement Qt par
    défaut, jugé peu clair dans ProfilesDialog)."""

    def mousePressEvent(self, event) -> None:
        if self.itemAt(event.pos()) is None:
            self.clearSelection()
            self.setCurrentRow(-1)
        super().mousePressEvent(event)


class ProfilesDialog(QDialog):
    """Gestion des profils de clé API Gemini (ajout, renommage, modification
    de la clé, suppression) et sélection du profil actif pour l'instance
    courante de l'application. Remplace l'ancien dialogue à clé unique
    (ApiKeyDialog) depuis l'introduction du support multi-instances (une clé
    différente par instance, voir app.config et app.instance_lock).

    active_profile (attribut public, relu par l'appelant après exec_()) est
    initialisé au profil actuellement actif de l'instance, et mis à jour si
    l'utilisateur en sélectionne un autre via le bouton "Utiliser" - jamais
    modifié par un simple Ajouter/Renommer/Supprimer d'un profil différent."""

    def __init__(
        self,
        parent=None,
        quota_tracker: QuotaTracker | None = None,
        active_profile: dict | None = None,
        generation_in_progress: bool = False,
    ):
        super().__init__(parent)
        self._quota_tracker = quota_tracker
        self.active_profile = active_profile
        self._generation_in_progress = generation_in_progress
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("profiles_dialog.window_title"))
        self.setMinimumWidth(460)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)

        info = QLabel(tr("profiles_dialog.list_info"))
        info.setWordWrap(True)
        layout.addWidget(info)

        self.list_widget = _DeselectableListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

        buttons_row = QHBoxLayout()
        self.use_button = QPushButton(tr("profiles_dialog.use_button"))
        self.use_button.clicked.connect(self._on_use_clicked)
        buttons_row.addWidget(self.use_button)
        self.add_button = QPushButton(tr("profiles_dialog.add_button"))
        self.add_button.clicked.connect(self._on_add_clicked)
        buttons_row.addWidget(self.add_button)
        self.edit_button = QPushButton(tr("profiles_dialog.edit_button"))
        self.edit_button.clicked.connect(self._on_edit_clicked)
        buttons_row.addWidget(self.edit_button)
        self.delete_button = QPushButton(tr("profiles_dialog.delete_button"))
        self.delete_button.clicked.connect(self._on_delete_clicked)
        buttons_row.addWidget(self.delete_button)
        layout.addLayout(buttons_row)

        close_buttons = QDialogButtonBox()
        close_buttons.addButton(tr("profiles_dialog.close_button"), QDialogButtonBox.AcceptRole)
        close_buttons.accepted.connect(self.accept)
        layout.addWidget(close_buttons)

        self._reload_list()

    def _reload_list(self) -> None:
        self.list_widget.clear()
        for profile in config.list_profiles():
            label = profile["name"]
            if self.active_profile is not None and profile["id"] == self.active_profile["id"]:
                label += tr("profiles_dialog.active_suffix")
            elif instance_lock.is_profile_locked_elsewhere(profile["id"]):
                label += tr("profiles_dialog.locked_suffix")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, profile)
            self.list_widget.addItem(item)
        # QListWidget sélectionne automatiquement la première ligne dès
        # qu'un item y est ajouté : sans ce clearSelection(), les boutons
        # Utiliser/Modifier/Supprimer restaient actifs et agissaient sur le
        # premier profil de la liste même sans clic explicite de
        # l'utilisateur (bug signalé le 2026-07-22). setCurrentRow(-1) seul
        # ne suffit pas : currentItem() continue de renvoyer le premier item
        # tant que clearSelection() n'a pas aussi vidé la sélection.
        self.list_widget.clearSelection()
        self.list_widget.setCurrentRow(-1)
        self._on_selection_changed()

    def _current_profile(self) -> dict | None:
        items = self.list_widget.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _on_selection_changed(self, *_args) -> None:
        has_selection = self._current_profile() is not None
        self.use_button.setEnabled(has_selection)
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _on_add_clicked(self) -> None:
        dialog = ProfileEditDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        name = dialog.profile_name()
        api_key = dialog.api_key()
        if not name or not api_key:
            QMessageBox.warning(
                self, tr("profiles_dialog.missing_fields_title"), tr("profiles_dialog.missing_fields_message")
            )
            return
        if config.find_profile_by_name(name) is not None:
            QMessageBox.warning(
                self, tr("profiles_dialog.duplicate_name_title"), tr("profiles_dialog.duplicate_name_message", name=name)
            )
            return
        duplicate = config.find_profile_by_api_key(api_key)
        if duplicate is not None:
            QMessageBox.warning(
                self,
                tr("profiles_dialog.duplicate_key_title"),
                tr("profiles_dialog.duplicate_key_message", name=duplicate["name"]),
            )
            return
        profile_id = str(uuid.uuid4())
        if not config.save_profile_api_key(profile_id, api_key):
            QMessageBox.critical(
                self, tr("profiles_dialog.save_error_title"), tr("profiles_dialog.save_error_message")
            )
            return
        # config.add_profile() relit et réécrit la liste sous le verrou
        # inter-processus de settings.json : un list_profiles() suivi d'un
        # save_profiles() ici perdait le profil ajouté au même moment par une
        # autre instance (course fermée le 2026-07-22).
        config.add_profile({"id": profile_id, "name": name})
        self._reload_list()

    def _on_edit_clicked(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        is_active_here = self.active_profile is not None and self.active_profile["id"] == profile["id"]
        if is_active_here and self._generation_in_progress:
            QMessageBox.warning(
                self,
                tr("profiles_dialog.in_use_title"),
                tr("profiles_dialog.in_use_generation_message", name=profile["name"]),
            )
            return
        if not is_active_here and not instance_lock.acquire_profile_lock(profile["id"]):
            QMessageBox.warning(
                self,
                tr("profiles_dialog.in_use_title"),
                tr("profiles_dialog.locked_message", name=profile["name"]),
            )
            return
        # Le verrou pris ci-dessus (profil non actif dans cette fenêtre) est
        # conservé pendant toute la durée du sous-dialogue d'édition, puis
        # relâché dans le finally : le relâcher aussitôt (comme avant le
        # 2026-07-22) rouvrait une fenêtre de course à échelle humaine - une
        # autre instance pouvait s'attribuer ce profil pendant que
        # l'utilisateur éditait, et la validation écrasait alors la clé d'un
        # profil devenu actif ailleurs.
        try:
            current_api_key = config.load_profile_api_key(profile["id"])
            dialog = ProfileEditDialog(self, current_name=profile["name"], current_api_key=current_api_key)
            if dialog.exec_() != QDialog.Accepted:
                return
            name = dialog.profile_name()
            api_key = dialog.api_key()
            if not name or not api_key:
                QMessageBox.warning(
                    self, tr("profiles_dialog.missing_fields_title"), tr("profiles_dialog.missing_fields_message")
                )
                return
            if config.find_profile_by_name(name, exclude_profile_id=profile["id"]) is not None:
                QMessageBox.warning(
                    self, tr("profiles_dialog.duplicate_name_title"), tr("profiles_dialog.duplicate_name_message", name=name)
                )
                return
            duplicate = config.find_profile_by_api_key(api_key, exclude_profile_id=profile["id"])
            if duplicate is not None:
                QMessageBox.warning(
                    self,
                    tr("profiles_dialog.duplicate_key_title"),
                    tr("profiles_dialog.duplicate_key_message", name=duplicate["name"]),
                )
                return
            if not config.save_profile_api_key(profile["id"], api_key):
                QMessageBox.critical(
                    self, tr("profiles_dialog.save_error_title"), tr("profiles_dialog.save_error_message")
                )
                return
            # config.rename_profile() relit et réécrit la liste sous le verrou
            # inter-processus de settings.json (voir _on_add_clicked()).
            config.rename_profile(profile["id"], name)
            if self.active_profile is not None and self.active_profile["id"] == profile["id"]:
                self.active_profile = {"id": profile["id"], "name": name}
                if self._quota_tracker is not None:
                    self._quota_tracker.switch_api_key(api_key)
        finally:
            if not is_active_here:
                instance_lock.release_profile_lock(profile["id"])
        self._reload_list()

    def _on_delete_clicked(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        is_active_here = self.active_profile is not None and self.active_profile["id"] == profile["id"]
        if is_active_here and self._generation_in_progress:
            QMessageBox.warning(
                self,
                tr("profiles_dialog.in_use_title"),
                tr("profiles_dialog.in_use_generation_message", name=profile["name"]),
            )
            return
        if not is_active_here and not instance_lock.acquire_profile_lock(profile["id"]):
            QMessageBox.warning(
                self,
                tr("profiles_dialog.in_use_title"),
                tr("profiles_dialog.locked_message", name=profile["name"]),
            )
            return
        # Le verrou pris ci-dessus (profil non actif dans cette fenêtre) est
        # conservé pendant toute la durée de la confirmation, puis relâché
        # dans le finally : le relâcher aussitôt (comme avant le 2026-07-22)
        # laissait une autre instance s'attribuer ce profil pendant que la
        # confirmation restait ouverte, et le clic sur "Supprimer" effaçait
        # alors la clé keyring d'un profil activement utilisé ailleurs. Après
        # une suppression effective, ce release retire aussi le fichier de
        # verrou du profil disparu, qui ne serait sinon nettoyé par personne.
        try:
            confirm = QMessageBox(self)
            confirm.setWindowTitle(tr("profiles_dialog.delete_confirm_title"))
            confirm.setText(tr("profiles_dialog.delete_confirm_message", name=profile["name"]))
            yes_button = confirm.addButton(tr("profiles_dialog.delete_confirm_yes"), QMessageBox.YesRole)
            confirm.addButton(tr("profiles_dialog.delete_confirm_no"), QMessageBox.NoRole)
            confirm.exec_()
            if confirm.clickedButton() is not yes_button:
                return
            if self.active_profile is not None and self.active_profile["id"] == profile["id"]:
                instance_lock.release_profile_lock(profile["id"])
                self.active_profile = None
            config.delete_profile_api_key(profile["id"])
            # config.remove_profile() relit et réécrit la liste sous le verrou
            # inter-processus de settings.json (voir _on_add_clicked()).
            config.remove_profile(profile["id"])
        finally:
            if not is_active_here:
                instance_lock.release_profile_lock(profile["id"])
        self._reload_list()

    def _on_use_clicked(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        if self.active_profile is not None and self.active_profile["id"] == profile["id"]:
            return
        # Changer de profil actif pendant une génération est refusé (ajouté
        # le 2026-07-22, même garde-fou que Modifier/Supprimer). Protection
        # locale à CETTE instance uniquement (generation_in_progress vient de
        # self.worker.isRunning() de cette fenêtre, voir _prompt_for_api_key)
        # : le switch_api_key() ci-dessous rebasculerait aussitôt le suivi de
        # quota sur le fichier de la nouvelle clé alors que le worker de
        # cette même fenêtre continue d'enregistrer les requêtes de la
        # génération en cours (lancée avec l'ancienne clé), créditant le
        # mauvais compte ; et le verrou de l'ancien profil serait libéré
        # alors que sa clé est encore activement utilisée par CETTE
        # instance, permettant à une autre instance de prendre ce profil et
        # d'utiliser la même clé en parallèle de la génération en cours.
        if self._generation_in_progress:
            QMessageBox.warning(
                self,
                tr("profiles_dialog.in_use_title"),
                tr("profiles_dialog.switch_during_generation_message"),
            )
            return
        if not instance_lock.acquire_profile_lock(profile["id"]):
            QMessageBox.warning(
                self, tr("profiles_dialog.locked_title"), tr("profiles_dialog.locked_message", name=profile["name"])
            )
            return
        if self.active_profile is not None:
            instance_lock.release_profile_lock(self.active_profile["id"])
        self.active_profile = profile
        api_key = config.load_profile_api_key(profile["id"])
        # Si la clé n'est pas lisible ici (Gestionnaire d'identification
        # Windows indisponible), active_profile est quand même mis à jour
        # ci-dessus (l'utilisateur a bien choisi ce profil), mais
        # quota_tracker doit rester sur son état actuel plutôt que de rester
        # signalé comme bascule effectuée sans que le suivi de quota affiché
        # ne corresponde réellement au nouveau profil actif (audit du
        # 2026-07-22) : à défaut de pouvoir basculer proprement, mieux vaut
        # ne pas basculer du tout que de laisser l'affichage désynchronisé
        # entre le nom de profil affiché et le compteur de quota affiché.
        if api_key and self._quota_tracker is not None:
            self._quota_tracker.switch_api_key(api_key)
        self._reload_list()


class QuotaLimitsDialog(QDialog):
    """Boîte de dialogue pour ajuster manuellement les limites RPM/TPM/RPD
    affichées dans l'application, si Google modifie le palier gratuit."""

    def __init__(self, parent=None, current_rpm: int = 0, current_tpm: int = 0, current_rpd: int = 0):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("quota_limits_dialog.window_title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        model_label = QLabel(tr("quota_limits_dialog.model_label", model_name=MODEL_NAME))
        model_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(model_label)

        info = QLabel(tr("quota_limits_dialog.info"))
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.rpm_input = QSpinBox()
        self.rpm_input.setRange(1, 100_000)
        self.rpm_input.setValue(current_rpm)
        self.rpm_input.setFixedWidth(100)
        form.addRow(tr("quota_limits_dialog.rpm_label"), self.rpm_input)

        self.tpm_input = QSpinBox()
        self.tpm_input.setRange(1, 100_000_000)
        self.tpm_input.setSingleStep(1000)
        self.tpm_input.setValue(current_tpm)
        self.tpm_input.setFixedWidth(100)
        form.addRow(tr("quota_limits_dialog.tpm_label"), self.tpm_input)

        self.rpd_input = QSpinBox()
        self.rpd_input.setRange(1, 1_000_000)
        self.rpd_input.setValue(current_rpd)
        self.rpd_input.setFixedWidth(100)
        form.addRow(tr("quota_limits_dialog.rpd_label"), self.rpd_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton(tr("quota_limits_dialog.ok_button"), QDialogButtonBox.AcceptRole)
        buttons.addButton(tr("quota_limits_dialog.cancel_button"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def limits(self) -> tuple[int, int, int]:
        return self.rpm_input.value(), self.tpm_input.value(), self.rpd_input.value()


def _prompt_tabs() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (key, tr(f"prompts_dialog.tabs.{key}.title"), tr(f"prompts_dialog.tabs.{key}.explanation"))
        for key in ("full_report", "chapter_summary", "consolidation")
    )


class PromptsDialog(QDialog):
    """Fenêtre permettant de consulter et modifier les prompts envoyés à
    Gemini. Chaque prompt a sa propre zone de saisie et son propre bouton de
    réinitialisation (n'affecte que cette zone). Le bouton de sauvegarde est
    inactif tant qu'aucun texte n'a été modifié par rapport à l'état initial
    (personnalisé ou par défaut) de la fenêtre, pour ne jamais graver de
    personnalisation identique au prompt par défaut du moment - voir
    prompts_store.save_custom_prompts. De même, le bouton de réinitialisation
    de chaque onglet est inactif tant que son texte affiché est déjà celui
    par défaut."""

    def __init__(self, parent=None, current_prompts: dict[str, str] | None = None, profile_id: str | None = None):
        super().__init__(parent)
        self._profile_id = profile_id
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("prompts_dialog.window_title"))
        self.resize(750, 600)

        current_prompts = current_prompts or {}

        layout = QVBoxLayout(self)

        warning = QLabel(tr("prompts_dialog.warning"))
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b02a2a; font-weight: bold;")
        layout.addWidget(warning)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        buttons = QDialogButtonBox()
        self._save_button = buttons.addButton(tr("prompts_dialog.save_button"), QDialogButtonBox.AcceptRole)
        self._save_button.setEnabled(False)
        buttons.addButton(tr("prompts_dialog.cancel_button"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        self._text_edits: dict[str, QTextEdit] = {}
        self._initial_texts: dict[str, str] = {}
        self._reset_buttons: dict[str, QPushButton] = {}
        for key, tab_title, tab_explanation in _prompt_tabs():
            initial_text = current_prompts.get(key, "") or default_prompt_templates()[key]
            self._initial_texts[key] = initial_text
            self._build_tab(key, tab_title, tab_explanation, initial_text)

        layout.addWidget(buttons)

    def _build_tab(self, key: str, tab_title: str, tab_explanation: str, initial_text: str) -> QTextEdit:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)

        explanation = QLabel(tab_explanation)
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #555; font-style: italic;")
        tab_layout.addWidget(explanation)

        text_edit = QTextEdit()
        # setFont AVANT tout setPlainText : setPlainText réinitialise le
        # document en repartant de la police par défaut du widget, donc sans
        # cet appel initial, un futur reset retomberait sur la police système
        # même si le format de caractère est corrigé après coup.
        text_edit.setFont(QFont("Courier New"))

        def _set_prompt_text(text: str) -> None:
            # setFontFamily (ou setCurrentFont) ne s'applique qu'au point
            # d'insertion courant, sans garantie d'effet sur le texte du
            # document une fois setPlainText appelé : on force donc
            # explicitement la police sur tout le document déjà rempli, en
            # sélectionnant tout son contenu après coup. document().setDefaultFont
            # et un repaint forcé du viewport, en plus du mergeCharFormat,
            # pour contourner un défaut de rafraîchissement du rendu observé
            # sur certaines machines (texte affiché dans une police différente
            # de l'état logique du widget tant qu'aucun repaint n'est forcé).
            courier = QFont("Courier New")
            text_edit.document().setDefaultFont(courier)
            text_edit.setPlainText(text)
            cursor = text_edit.textCursor()
            cursor.select(QTextCursor.Document)
            char_format = cursor.charFormat()
            char_format.setFontFamily("Courier New")
            cursor.mergeCharFormat(char_format)
            text_edit.setCurrentFont(courier)
            text_edit.viewport().update()
            text_edit.update()

        tab_layout.addWidget(text_edit)
        _set_prompt_text(initial_text)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_button = QPushButton(tr("prompts_dialog.reset_button"))
        reset_button.clicked.connect(lambda: self._on_reset_clicked(key, _set_prompt_text))
        reset_row.addWidget(reset_button)
        tab_layout.addLayout(reset_row)
        self._reset_buttons[key] = reset_button
        self._text_edits[key] = text_edit

        text_edit.textChanged.connect(lambda: self._on_text_changed(key))
        self._on_text_changed(key)

        self.tabs.addTab(tab, tab_title)
        return text_edit

    def _on_reset_clicked(self, key: str, set_text) -> None:
        # Effacement immédiat et permanent sur disque, indépendant du bouton
        # Sauvegarder/Annuler de la fenêtre : comportement voulu, "Réinitialiser"
        # doit agir tout de suite plutôt qu'attendre la validation du dialogue.
        default_text = default_prompt_templates()[key]
        reset_custom_prompt(i18n.current_language(), key, self._profile_id)
        self._initial_texts[key] = default_text
        set_text(default_text)

    def _on_text_changed(self, key: str) -> None:
        current_text = self._text_edits[key].toPlainText()
        self._reset_buttons[key].setEnabled(current_text != default_prompt_templates()[key])
        any_modified = any(
            self._text_edits[k].toPlainText() != self._initial_texts[k] for k in self._text_edits
        )
        self._save_button.setEnabled(any_modified)

    def prompts(self) -> dict[str, str]:
        """Renvoie les prompts saisis. Une valeur identique au défaut équivaut
        à une réinitialisation (voir prompts_store.save_custom_prompts)."""
        return {key: text_edit.toPlainText() for key, text_edit in self._text_edits.items()}


SUPPORTED_EXTENSIONS = (".epub", ".pdf")
REPORT_EXTENSION = ".distillat.json"
DROPPABLE_EXTENSIONS = SUPPORTED_EXTENSIONS + (REPORT_EXTENSION,)


class DropZone(QLabel):
    """Zone de glisser-déposer pour les fichiers EPUB/PDF (à résumer) et les
    fiches Distillat déjà générées (.distillat.json, à ouvrir directement)."""

    _ICON_HTML = None  # balise <img> en data URI, construite une fois au premier usage

    def __init__(self, on_file_dropped, on_report_dropped, parent=None):
        super().__init__(parent)
        self.on_file_dropped = on_file_dropped
        self.on_report_dropped = on_report_dropped
        self.busy = False
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(180)
        self.setTextFormat(Qt.RichText)
        self._set_message(tr("drop_zone.prompt"))
        self._set_style(active=False)

    @classmethod
    def _icon_html(cls) -> str:
        if cls._ICON_HTML is None:
            icon_path = config.get_app_icon_path()
            pixmap = QPixmap(str(icon_path)) if icon_path.exists() else QPixmap()
            if pixmap.isNull():
                cls._ICON_HTML = ""
            else:
                pixmap = pixmap.scaledToHeight(64, Qt.SmoothTransformation)
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                pixmap.save(buffer, "PNG")
                data_base64 = buffer.data().toBase64().data().decode("ascii")
                cls._ICON_HTML = f'<img src="data:image/png;base64,{data_base64}"><br><br>'
        return cls._ICON_HTML

    def _set_message(self, message: str) -> None:
        self.setText(f"{self._icon_html()}{message}")

    def set_busy(self, busy: bool) -> None:
        """Désactive le dépôt et le clic (choix de fichier) tant qu'un résumé
        est en cours de génération, pour éviter de remplacer le fichier
        sélectionné ou d'écraser la fiche affichée pendant le traitement."""
        self.busy = busy
        if busy:
            self._set_message(tr("drop_zone.busy"))
        else:
            self._set_message(tr("drop_zone.prompt"))

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
        if self.busy:
            event.ignore()
            return
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
                event.acceptProposedAction()
                # Le traitement réel (qui peut ouvrir un QMessageBox modal via
                # _confirm_discard_unsaved_report) est différé après la fin du
                # dropEvent : ouvrir un dialogue modal en plein milieu du
                # traitement OLE du drag-and-drop natif Windows fait planter
                # l'application.
                QTimer.singleShot(0, lambda p=path: self.on_report_dropped(p))
                return
            if lower_path.endswith(SUPPORTED_EXTENSIONS):
                event.acceptProposedAction()
                QTimer.singleShot(0, lambda p=path: self.on_file_dropped(p))
                return
        event.ignore()

    def mousePressEvent(self, event) -> None:
        if self.busy:
            return
        default_dir = config.load_last_book_dir()
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("drop_zone.file_dialog_title"),
            str(default_dir) if default_dir else "",
            tr("drop_zone.file_dialog_filter"),
        )
        if not path:
            return
        config.save_last_book_dir(Path(path).parent)
        if path.lower().endswith(REPORT_EXTENSION):
            self.on_report_dropped(path)
        else:
            self.on_file_dropped(path)


def _style_display_paragraphs(text_edit: QTextEdit, margin: float = 8.0) -> None:
    """Justifie le texte affiché et ajoute un espacement visuel sous chaque
    paragraphe. Purement cosmétique : le format de bloc n'est pas restitué par
    toMarkdown ni toPlainText, le texte sauvegardé et l'export PDF restent
    inchangés. Les nouveaux paragraphes créés à la saisie (Entrée) héritent du
    format du bloc courant."""
    cursor = QTextCursor(text_edit.document())
    while True:
        block_format = cursor.blockFormat()
        block_format.setBottomMargin(margin)
        block_format.setAlignment(Qt.AlignJustify)
        cursor.setBlockFormat(block_format)
        if not cursor.movePosition(QTextCursor.NextBlock):
            break


def _from_display_plain_text(plain_text: str, source_text: str) -> str:
    """Reconstruit le texte à sauvegarder à partir du texte réellement affiché
    (toPlainText : un vrai retour à la ligne par paragraphe ou titre, jamais de
    retour ajouté par un simple wrap visuel de l'affichage, contrairement à
    toMarkdown qui recoupe artificiellement les lignes trop longues autour de
    80 colonnes). toPlainText ne restitue pas les balises #/##/### (le titre
    n'est rendu qu'en gras/grande taille) : chaque bloc réaffiché est donc
    réassocié dans l'ordre au préfixe du bloc correspondant dans le texte
    source d'origine, pour ne reporter que les vraies éditions de
    l'utilisateur, jamais un artefact de mise en forme visuelle."""
    def prefix_of(line: str) -> str:
        for marker in ("### ", "## ", "# "):
            if line.startswith(marker):
                return marker
        return ""

    source_blocks = [line.strip() for line in source_text.splitlines() if line.strip()]
    source_prefixes = [prefix_of(line) for line in source_blocks]
    displayed_blocks = [line.strip() for line in plain_text.splitlines() if line.strip()]

    result: list[str] = []
    for index, block in enumerate(displayed_blocks):
        prefix = source_prefixes[index] if index < len(source_prefixes) else ""
        # Si l'utilisateur a tapé le # lui-même (nouveau titre), pas de double préfixe.
        result.append(block if prefix and block.startswith(prefix) else prefix + block)
    return "\n".join(result)


_MARKDOWN_LINK_IMAGE_CHARS = re.compile(r"([!\[\]()])")


def _escape_markdown_links_and_images(text: str) -> str:
    """Échappe les caractères qui forment la syntaxe Markdown de lien/image
    (![alt](url) ou [texte](url)). Sans ça, un texte généré par Gemini (donc
    influençable par le contenu du livre traité) pourrait faire charger par
    setMarkdown une image locale via une URL file:// - Qt lit alors bien le
    contenu du fichier référencé (vérifié), ce qui pourrait révéler
    l'existence d'un fichier local à l'utilisateur ou à un tiers voyant la
    fiche. Les seuls usages Markdown voulus dans ce texte (titres #/##/###)
    ne sont pas affectés, ces caractères n'y figurant pas."""
    return _MARKDOWN_LINK_IMAGE_CHARS.sub(r"\\\1", text)


def _to_display_markdown(text: str) -> str:
    """Prépare le texte stocké (une ligne = un paragraphe ou un titre #) pour
    setMarkdown : en Markdown un simple saut de ligne est un retour souple qui
    fusionnerait les paragraphes, il faut des lignes vides entre eux. Même
    interprétation ligne à ligne que l'export PDF."""
    lines = [_escape_markdown_links_and_images(line.strip()) for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)


class AutoHeightTextEdit(QTextEdit):
    """QTextEdit qui ajuste sa hauteur à son contenu, y compris au tout premier
    affichage : resizeEvent se déclenche dès que la largeur réelle du widget
    est connue, contrairement à un simple recalcul sur textChanged qui utilise
    une largeur provisoire tant que le widget n'a pas été posé par le layout."""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adjust_height()

    def _adjust_height(self) -> None:
        self.document().setTextWidth(self.viewport().width())
        margins = self.contentsMargins()
        height = self.document().size().height() + margins.top() + margins.bottom()
        if self.height() != int(height):
            self.setFixedHeight(int(height))


class PendingResumesDialog(QDialog):
    """Liste au démarrage les livres dont une génération précédente s'est
    arrêtée en cours de route (voir generation_resume), pour proposer de
    reprendre l'un d'eux là où il en était plutôt que de reformuler depuis le
    début les lots de chapitres déjà résumés avec succès."""

    def __init__(self, states: list, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr("pending_resumes_dialog.window_title"))
        self.setMinimumWidth(480)
        self.selected_state = None
        self.states_to_clear: list = []

        self._states = states

        layout = QVBoxLayout(self)

        intro = QLabel(tr("pending_resumes_dialog.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list_widget = QListWidget()
        for state in states:
            book_exists = Path(state.book_path).exists()
            label = tr(
                "pending_resumes_dialog.item_label",
                filename=os.path.basename(state.book_path),
                batches_done=state.batches_done,
                batches_total=state.batches_total,
            )
            if not book_exists:
                label += " " + tr("pending_resumes_dialog.item_missing_suffix")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, state)
            if not book_exists:
                item.setForeground(QColor("#b02a2a"))
            self.list_widget.addItem(item)
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

        buttons_layout = QHBoxLayout()

        self.delete_button = QPushButton(tr("pending_resumes_dialog.delete_button"))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        buttons_layout.addWidget(self.delete_button)

        buttons_layout.addStretch(1)

        self.close_button = QPushButton(tr("pending_resumes_dialog.close_button"))
        self.close_button.clicked.connect(self.reject)
        buttons_layout.addWidget(self.close_button)

        self.resume_button = QPushButton(tr("pending_resumes_dialog.resume_button"))
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self._on_resume_clicked)
        buttons_layout.addWidget(self.resume_button)

        layout.addLayout(buttons_layout)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        self.delete_button.setAutoDefault(False)
        self.close_button.setAutoDefault(False)
        self.resume_button.setAutoDefault(False)
        self.close_button.setFocus()

    def _current_state(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _on_selection_changed(self, row: int) -> None:
        state = self._current_state()
        self.delete_button.setEnabled(state is not None)
        self.resume_button.setEnabled(state is not None and Path(state.book_path).exists())

    def _on_delete_clicked(self) -> None:
        state = self._current_state()
        if state is None:
            return
        self.states_to_clear.append(state)
        row = self.list_widget.currentRow()
        self.list_widget.takeItem(row)
        if self.list_widget.count() == 0:
            self.reject()

    def _on_resume_clicked(self) -> None:
        state = self._current_state()
        if state is None:
            return
        self.selected_state = state
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("main_window.window_title", version=VERSION))
        # Sans ceci, Qt calcule automatiquement une taille minimale à partir
        # du contenu du layout (header, onglets...), assez grande pour
        # empêcher Windows Snap de réduire la fenêtre à 1/4 d'écran sur
        # certains moniteurs. Cette valeur ne force aucune réorganisation du
        # contenu : elle autorise seulement Qt/Windows à redimensionner plus
        # petit que ce calcul automatique ; le contenu peut alors se
        # chevaucher ou être partiellement coupé en dessous de sa taille
        # confortable, comme pour toute fenêtre Windows redimensionnée
        # au-delà de son contenu.
        self.setMinimumSize(400, 300)
        self._size_to_available_screen()

        self.selected_book_path: str | None = None
        self.worker: SummarizeWorker | None = None
        self.last_result: BookReport | None = None
        self._last_result_source_stem: str | None = None
        # Chemin du fichier .distillat.json d'où provient la fiche affichée
        # (None si elle vient d'être générée) : sert de dossier par défaut à
        # l'enregistrement et permet d'écraser la fiche d'origine sans
        # confirmation superflue.
        self._last_report_source_path: Path | None = None
        self._report_dirty = False
        # daily_state_path est un chemin provisoire (aucun compte connu tant
        # qu'aucune génération n'a démarré) : switch_api_key(), appelée dans
        # _on_summarize_clicked() dès que la clé API est connue, le remplace
        # par le fichier propre à cette clé (un fichier par compte, voir
        # quota_tracker.daily_state_path_for_key() - séparation ajoutée le
        # 2026-07-21 après avoir constaté qu'un changement de clé API en
        # cours de journée continuait sinon d'accumuler sur le même
        # compteur, mélangeant deux comptes Google distincts).
        self.quota_tracker = QuotaTracker(
            daily_state_path=config.get_settings_dir() / ".quota_state_pending.json",
            settings_dir=config.get_settings_dir(),
        )
        # Marqueur de comptage des instances vivantes (voir
        # instance_lock.count_alive_instances()), indépendant du verrou par
        # profil ci-dessous : une instance sans profil actif doit quand même
        # être comptée par le bouton "nouvelle instance" pour respecter
        # instance_lock.MAX_INSTANCES.
        instance_lock.register_instance()
        # Attribue automatiquement à cette instance le premier profil de clé
        # API non verrouillé par une autre instance en cours d'exécution (voir
        # app.instance_lock), pour permettre de lancer plusieurs instances de
        # Distillat en parallèle (une clé différente chacune) sans risquer
        # qu'une instance écrase silencieusement le profil actif d'une autre.
        # Bascule aussi immédiatement le suivi de quota sur le fichier du
        # profil attribué : sans ça, l'affichage du quota au démarrage
        # montrerait encore .quota_state_pending.json (toujours à 0) jusqu'au
        # premier clic sur "Résumer".
        self.active_profile: dict | None = None
        self._resolve_active_profile()
        # Ligne de session dans le journal d'appels API : délimite les
        # lancements de l'application (version, machine, profil de clé API
        # attribué, compteur quotidien tel que rechargé du disque), pour
        # situer chaque génération dans sa session et rendre détectable un
        # double lancement simultané (deux lignes DEMARRAGE sans fermeture
        # entre - le pid, distinguant déjà deux instances, est ajouté
        # automatiquement en préfixe de chaque ligne du journal par
        # _log_api_call(), voir gemini_client.py). Le nom de la machine
        # permet d'attribuer son origine à un log recueilli sur un autre PC
        # (ex : tests croisés sur la machine d'un tiers avec sa propre clé
        # API).
        log_api_event(
            f"application DEMARRAGE version={VERSION} "
            f"machine={platform.node()} "
            f"profil={self.active_profile['name'] if self.active_profile else '(aucun)'} "
            f"requetes_jour_chargees={self.quota_tracker.snapshot().requests_today}"
        )

        self._latest_version_available: str | None = None
        self._elapsed_seconds = 0
        self._last_progress_message = ""
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._on_elapsed_tick)

        # La fenêtre glissante « requêtes/tokens par minute » décroît avec le
        # temps même sans nouvel appel Gemini : sans ce timer, l'affichage
        # restait figé sur la valeur du dernier appel jusqu'à la requête
        # suivante, ce qui est trompeur (ex : « 3/5 requêtes/minute » affiché
        # alors que la fenêtre est en fait redescendue à 0).
        self._quota_refresh_timer = QTimer(self)
        self._quota_refresh_timer.setInterval(2000)
        self._quota_refresh_timer.timeout.connect(
            lambda: self._update_quota_display(self.quota_tracker.snapshot())
        )
        self._quota_refresh_timer.start()

        self._build_ui()
        self._update_quota_display(self.quota_tracker.snapshot())
        check_for_updates_on_startup(self)

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
        vertical_margin = max(0, (available.height() - height) // 2 - 40)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + vertical_margin,
        )

    def _on_language_changed(self, index: int) -> None:
        language = self.language_selector.itemData(index)
        if language is None or language == i18n.current_language():
            return
        i18n.set_language(language)
        config.save_language_setting(language)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """Réapplique les textes statiques (titres, labels fixes, placeholders,
        titres d'onglets, boutons) dans la langue actuellement chargée, sans
        toucher à l'état dynamique actuellement affiché (fiche en cours,
        statut de génération, quota affiché...) qui se retraduira de lui-même
        à sa prochaine mise à jour naturelle."""
        self.setWindowTitle(tr("main_window.window_title", version=VERSION))
        self.title_label.setText(tr("main_window.title_label"))
        self.new_instance_button.setText(tr("main_window.new_instance_button"))
        self.language_label.setText(tr("language_selector.label"))
        self.language_selector.setItemText(0, tr("language_selector.french"))
        self.language_selector.setItemText(1, tr("language_selector.english"))
        self.prompts_button.setText(tr("main_window.prompts_button"))
        self.quota_limits_button.setText(tr("main_window.quota_limits_button"))
        self.api_key_button.setText(tr("main_window.profiles_button"))
        self._update_profile_label()
        self.output_language_hint_label.setText(tr("main_window.output_language_hint"))
        if self._latest_version_available:
            self.update_banner_label.setText(
                tr("main_window.update_banner", version=self._latest_version_available)
            )
        self.drop_zone.set_busy(self.drop_zone.busy)
        if self.selected_book_path:
            self.file_label.setText(
                tr("main_window.file_selected", filename=os.path.basename(self.selected_book_path))
            )
        else:
            self.file_label.setText(tr("main_window.no_file_selected"))
        self.remove_file_button.setText(tr("main_window.remove_file_button"))
        self.summarize_button.setText(tr("main_window.summarize_button"))
        self.quota_help_button.setToolTip(tr("quota_help_dialog.tooltip"))
        self.extra_text_label.setText(tr("main_window.extra_text_label"))
        self.quota_disclaimer_label.setText(tr("main_window.quota_disclaimer"))
        self.load_report_button.setText(tr("main_window.load_report_button"))
        self.save_report_button.setText(tr("main_window.save_report_button"))
        self.close_report_button.setText(tr("main_window.close_report_button"))
        self.save_button.setText(tr("main_window.export_pdf_button"))
        self.footer_button.setText(tr("main_window.footer_button"))
        self.source_code_link_button.setText(tr("main_window.source_code_link"))
        self.download_link_button.setText(tr("main_window.download_link"))
        self.changelog_link_button.setText(tr("main_window.changelog_link"))
        if not self.last_result:
            self.cover_label.setText(tr("main_window.no_cover"))
        self.summary_view.setPlaceholderText(tr("main_window.summary_placeholder"))
        self.detailed_summary_view.setPlaceholderText(tr("main_window.detailed_summary_placeholder"))
        self.analysis_view.setPlaceholderText(tr("main_window.analysis_placeholder"))
        self.result_tabs.setTabText(0, tr("main_window.tab_cover"))
        self.result_tabs.setTabText(1, tr("main_window.tab_summary"))
        self.result_tabs.setTabText(2, tr("main_window.tab_detailed_summary"))
        self.result_tabs.setTabText(3, tr("main_window.tab_characters"))
        self.result_tabs.setTabText(4, tr("main_window.tab_analysis"))
        if not self.last_result:
            self.characters_placeholder.setText(tr("main_window.characters_placeholder"))
        self._update_quota_display(self.quota_tracker.snapshot())

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 6)

        header = QHBoxLayout()
        self.title_label = QLabel(tr("main_window.title_label"))
        self.title_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.addWidget(self.title_label)
        self.new_instance_button = QPushButton(tr("main_window.new_instance_button"))
        self.new_instance_button.clicked.connect(self._on_new_instance_clicked)
        header.addWidget(self.new_instance_button)
        header.addStretch()
        self.language_label = QLabel(tr("language_selector.label"))
        header.addWidget(self.language_label)
        self.language_selector = QComboBox()
        self.language_selector.addItem(tr("language_selector.french"), "fr")
        self.language_selector.addItem(tr("language_selector.english"), "en")
        current_index = self.language_selector.findData(i18n.current_language())
        self.language_selector.setCurrentIndex(max(current_index, 0))
        self.language_selector.currentIndexChanged.connect(self._on_language_changed)
        header.addWidget(self.language_selector)
        self.prompts_button = QPushButton(tr("main_window.prompts_button"))
        self.prompts_button.clicked.connect(self._on_edit_prompts)
        header.addWidget(self.prompts_button)
        self.quota_limits_button = QPushButton(tr("main_window.quota_limits_button"))
        self.quota_limits_button.clicked.connect(self._on_edit_quota_limits)
        header.addWidget(self.quota_limits_button)
        self.active_profile_label = QLabel()
        self.active_profile_label.setStyleSheet("color: #555;")
        header.addWidget(self.active_profile_label)
        self.api_key_button = QPushButton(tr("main_window.profiles_button"))
        self.api_key_button.clicked.connect(self._on_edit_api_key)
        header.addWidget(self.api_key_button)
        layout.addLayout(header)
        self._update_profile_label()

        # Rappel discret mais permanent : la langue de l'UI détermine aussi la
        # langue dans laquelle Gemini rédige la fiche, ce qui n'est pas évident
        # pour l'utilisateur sans cette précision explicite.
        output_language_row = QHBoxLayout()
        output_language_row.addStretch()
        self.output_language_hint_label = QLabel(tr("main_window.output_language_hint"))
        self.output_language_hint_label.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        output_language_row.addWidget(self.output_language_hint_label)
        layout.addLayout(output_language_row)

        # Discret et masqué par défaut : n'apparaît que si une vérification
        # au démarrage détecte réellement une version plus récente sur
        # GitHub Releases (silencieux en cas d'erreur réseau ou si à jour).
        self.update_banner_label = QLabel("")
        self.update_banner_label.setStyleSheet("color: #1a6b1a; font-size: 12px;")
        self.update_banner_label.setWordWrap(True)
        self.update_banner_label.setTextFormat(Qt.RichText)
        self.update_banner_label.setAlignment(Qt.AlignCenter)
        self.update_banner_label.linkActivated.connect(self._on_open_releases_page)
        self.update_banner_label.hide()
        layout.addWidget(self.update_banner_label)

        self.drop_zone = DropZone(
            on_file_dropped=self._on_file_selected,
            on_report_dropped=self._on_report_dropped,
        )
        layout.addWidget(self.drop_zone)

        file_row = QHBoxLayout()
        self.file_label = QLabel(tr("main_window.no_file_selected"))
        self.file_label.setStyleSheet("color: #555;")
        self.file_label.setAlignment(Qt.AlignCenter)
        file_row.addStretch()
        file_row.addWidget(self.file_label)
        file_row.addStretch()

        self.remove_file_button = QPushButton(tr("main_window.remove_file_button"))
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
        self.summarize_button = QPushButton(tr("main_window.summarize_button"))
        self.summarize_button.setMinimumHeight(36)
        self.summarize_button.clicked.connect(self._on_summarize_clicked)
        action_row.addWidget(self.summarize_button)
        self._set_summarize_button_enabled(False)
        layout.addLayout(action_row)

        status_row = QHBoxLayout()

        self.quota_help_button = QPushButton("?")
        self.quota_help_button.setFixedSize(22, 22)
        self.quota_help_button.setToolTip(tr("quota_help_dialog.tooltip"))
        self.quota_help_button.clicked.connect(self._on_show_quota_help)
        status_row.addWidget(self.quota_help_button)

        self.status_label = QLabel(tr("main_window.idle_status"))
        self.status_label.setStyleSheet("color: #555;")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, stretch=1)

        layout.addLayout(status_row)

        # Discret et masqué par défaut : n'apparaît que si Gemini a produit du
        # texte en trop après le premier objet JSON exploité (cas rare d'une
        # réponse mal formée). Ce texte peut être légitime, ce n'est pas à
        # l'application de décider silencieusement qu'il ne sert à rien.
        self.extra_text_label = QLabel(tr("main_window.extra_text_label"))
        self.extra_text_label.setStyleSheet("color: #a06a00; font-size: 12px;")
        self.extra_text_label.setWordWrap(True)
        self.extra_text_label.setTextFormat(Qt.RichText)
        self.extra_text_label.linkActivated.connect(self._on_show_extra_generated_text)
        self.extra_text_label.hide()
        layout.addWidget(self.extra_text_label)

        quota_block = QVBoxLayout()
        quota_block.setSpacing(0)

        self.quota_label = QLabel("")
        self.quota_label.setStyleSheet("color: #555; font-size: 13px;")
        self.quota_label.setWordWrap(True)
        self.quota_label.setAlignment(Qt.AlignCenter)
        quota_block.addWidget(self.quota_label)

        self.quota_disclaimer_label = QLabel(tr("main_window.quota_disclaimer"))
        self.quota_disclaimer_label.setStyleSheet("color: #888; font-size: 10px; font-style: italic;")
        self.quota_disclaimer_label.setWordWrap(True)
        self.quota_disclaimer_label.setAlignment(Qt.AlignCenter)
        quota_block.addWidget(self.quota_disclaimer_label)

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
        result_row.addStretch()

        self.load_report_button = QPushButton(tr("main_window.load_report_button"))
        self.load_report_button.clicked.connect(self._on_load_report_clicked)
        result_row.addWidget(self.load_report_button)

        self.save_report_button = QPushButton(tr("main_window.save_report_button"))
        self.save_report_button.setEnabled(False)
        self.save_report_button.clicked.connect(self._on_save_report_clicked)
        result_row.addWidget(self.save_report_button)

        self.close_report_button = QPushButton(tr("main_window.close_report_button"))
        self.close_report_button.setEnabled(False)
        self.close_report_button.clicked.connect(self._on_close_report_clicked)
        result_row.addWidget(self.close_report_button)

        self.save_button = QPushButton(tr("main_window.export_pdf_button"))
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._on_save_clicked)
        result_row.addWidget(self.save_button)
        layout.addLayout(result_row)

        self.result_tabs = QTabWidget()
        layout.addWidget(self.result_tabs, stretch=1)

        self._build_cover_tab()
        self._build_summary_tab()
        self._build_detailed_summary_tab()
        self._build_characters_tab()
        self._build_analysis_tab()

        footer_link_style = """
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

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, -10, 0, 0)
        self.footer_button = QPushButton(tr("main_window.footer_button"))
        self.footer_button.setCursor(Qt.PointingHandCursor)
        self.footer_button.setStyleSheet(footer_link_style)
        self.footer_button.clicked.connect(self._on_show_license)
        footer_row.addWidget(self.footer_button)
        footer_row.addStretch()

        self.source_code_link_button = QPushButton(tr("main_window.source_code_link"))
        self.source_code_link_button.setCursor(Qt.PointingHandCursor)
        self.source_code_link_button.setStyleSheet(footer_link_style)
        self.source_code_link_button.clicked.connect(self._on_open_repo_page)
        footer_row.addWidget(self.source_code_link_button)

        self.download_link_button = QPushButton(tr("main_window.download_link"))
        self.download_link_button.setCursor(Qt.PointingHandCursor)
        self.download_link_button.setStyleSheet(footer_link_style)
        self.download_link_button.clicked.connect(self._on_open_releases_page)
        footer_row.addWidget(self.download_link_button)

        self.changelog_link_button = QPushButton(tr("main_window.changelog_link"))
        self.changelog_link_button.setCursor(Qt.PointingHandCursor)
        self.changelog_link_button.setStyleSheet(footer_link_style)
        self.changelog_link_button.clicked.connect(self._on_show_changelog)
        footer_row.addWidget(self.changelog_link_button)

        layout.addLayout(footer_row)

    def _build_cover_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.addStretch()

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(220, 320)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet("border: 1px solid #ccc; background-color: #f0f0f0;")
        self.cover_label.setText(tr("main_window.no_cover"))
        self.cover_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cover_label.customContextMenuRequested.connect(self._on_cover_context_menu)
        cover_row = QHBoxLayout()
        cover_row.addStretch()
        cover_row.addWidget(self.cover_label)
        cover_row.addStretch()
        tab_layout.addLayout(cover_row)

        self.book_title_input = QLineEdit("")
        self.book_title_input.setStyleSheet(
            "font-size: 18px; font-weight: bold; margin-top: 14px;"
            " border: none; background: transparent;"
        )
        self.book_title_input.setAlignment(Qt.AlignCenter)
        self.book_title_input.textEdited.connect(self._on_result_edited)
        tab_layout.addWidget(self.book_title_input)

        # QLineEdit (et non QLabel) pour que le nom de l'auteur soit éditable,
        # comme le texte des autres onglets. textEdited (et non textChanged) ne
        # se déclenche que sur une frappe de l'utilisateur, pas sur le
        # remplissage programmatique : pas de faux « fiche modifiée ».
        self.book_author_input = QLineEdit("")
        self.book_author_input.setStyleSheet(
            "font-size: 14px; color: #555; border: none; background: transparent;"
        )
        self.book_author_input.setAlignment(Qt.AlignCenter)
        self.book_author_input.textEdited.connect(self._on_result_edited)
        tab_layout.addWidget(self.book_author_input)

        tab_layout.addStretch()
        self.result_tabs.addTab(tab, tr("main_window.tab_cover"))

    def _build_summary_tab(self) -> None:
        self.summary_view = QTextEdit()
        self.summary_view.setStyleSheet("font-size: 15px;")
        self.summary_view.setPlaceholderText(tr("main_window.summary_placeholder"))
        self.summary_view.textChanged.connect(self._on_result_edited)
        self.result_tabs.addTab(self.summary_view, tr("main_window.tab_summary"))

    def _build_detailed_summary_tab(self) -> None:
        self.detailed_summary_view = QTextEdit()
        self.detailed_summary_view.setStyleSheet("font-size: 15px;")
        self.detailed_summary_view.setPlaceholderText(tr("main_window.detailed_summary_placeholder"))
        self.detailed_summary_view.textChanged.connect(self._on_result_edited)
        self.result_tabs.addTab(self.detailed_summary_view, tr("main_window.tab_detailed_summary"))

    def _build_characters_tab(self) -> None:
        self.character_name_inputs: list[QLineEdit] = []
        self.character_description_inputs: list[QTextEdit] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: white;")
        self.characters_layout = QVBoxLayout(container)
        self.characters_layout.setContentsMargins(6, 6, 6, 6)
        self.characters_layout.addStretch()
        scroll.setWidget(container)

        self.characters_placeholder = QLabel(tr("main_window.characters_placeholder"))
        self.characters_placeholder.setStyleSheet("color: #888; font-size: 15px;")
        self.characters_placeholder.setWordWrap(True)
        self.characters_layout.insertWidget(0, self.characters_placeholder)

        self.result_tabs.addTab(scroll, tr("main_window.tab_characters"))

    def _build_analysis_tab(self) -> None:
        self.analysis_view = QTextEdit()
        self.analysis_view.setStyleSheet("font-size: 15px;")
        self.analysis_view.setPlaceholderText(tr("main_window.analysis_placeholder"))
        self.analysis_view.textChanged.connect(self._on_result_edited)
        self.result_tabs.addTab(self.analysis_view, tr("main_window.tab_analysis"))

    def _clear_characters_tab(self) -> None:
        while self.characters_layout.count() > 1:  # garder le stretch final
            item = self.characters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _populate_characters_tab(self, characters: list) -> None:
        self._clear_characters_tab()
        self.character_name_inputs = []
        self.character_description_inputs = []
        if not characters:
            placeholder = QLabel(tr("main_window.no_characters"))
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

            name_input = QLineEdit(character.name)
            name_input.setStyleSheet(
                "font-size: 16px; font-weight: bold; border: none; background: transparent;"
            )
            name_input.textChanged.connect(self._on_result_edited)
            card_layout.addWidget(name_input)
            self.character_name_inputs.append(name_input)

            description_input = AutoHeightTextEdit()
            description_input.setPlainText(character.description)
            description_input.setStyleSheet(
                "font-size: 15px; color: #333; border: none; background: transparent;"
            )
            description_input.document().setDocumentMargin(0)
            description_input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # Avant la connexion de textChanged : la justification est un
            # changement de format qui déclencherait un faux « fiche modifiée ».
            _style_display_paragraphs(description_input, margin=0.0)
            description_input.textChanged.connect(self._on_result_edited)
            card_layout.addWidget(description_input)
            self.character_description_inputs.append(description_input)

            self.characters_layout.insertWidget(index, card)
            self.characters_layout.insertSpacing(index + 1, 10)

    def _resolve_active_profile(self) -> None:
        """Attribue à cette instance le premier profil de clé API dont le
        verrou n'est pas détenu par une autre instance vivante (voir
        app.instance_lock) ET dont la clé API est réellement lisible via
        keyring, dans l'ordre d'enregistrement des profils. Bascule aussitôt
        le suivi de quota sur le profil attribué. Si des profils existent
        mais sont tous verrouillés ailleurs, l'instance démarre simplement
        sans profil actif (self.active_profile reste None) : l'utilisateur
        en sera informé au moment où il en aura réellement besoin (clic sur
        "Résumer" -> _ensure_api_key() -> ProfilesDialog, qui affiche déjà
        quels profils sont occupés), plutôt que par un avertissement au
        démarrage qui apparaîtrait avant même l'affichage de la fenêtre
        (modale bloquante avant window.show(), déroutant - constaté le
        2026-07-22). Un profil dont le verrou est acquis mais dont la clé
        n'est pas lisible (Gestionnaire d'identification Windows
        indisponible pour cette entrée précise) libère aussitôt son verrou
        et cède la place au profil suivant (audit du 2026-07-22) : le
        garder comme profil actif sans clé utilisable bloquerait ce profil
        pour toute autre instance sans qu'aucune génération ne soit
        possible avec lui depuis celle-ci."""
        for profile in config.list_profiles():
            if not instance_lock.acquire_profile_lock(profile["id"]):
                continue
            api_key = config.load_profile_api_key(profile["id"])
            if not api_key:
                instance_lock.release_profile_lock(profile["id"])
                continue
            self.active_profile = profile
            self.quota_tracker.switch_api_key(api_key)
            return

    def _update_profile_label(self) -> None:
        if self.active_profile is not None:
            self.active_profile_label.setText(
                tr("main_window.active_profile_label", name=self.active_profile["name"])
            )
        else:
            self.active_profile_label.setText(tr("main_window.no_profile_label"))

    def _ensure_api_key(self, prompt_if_missing: bool = True) -> str | None:
        if self.active_profile is not None:
            api_key = config.load_profile_api_key(self.active_profile["id"])
            if api_key:
                return api_key
        if not prompt_if_missing:
            return None
        return self._prompt_for_api_key()

    def _prompt_for_api_key(self) -> str | None:
        generation_in_progress = self.worker is not None and self.worker.isRunning()
        dialog = ProfilesDialog(
            self,
            quota_tracker=self.quota_tracker,
            active_profile=self.active_profile,
            generation_in_progress=generation_in_progress,
        )
        dialog.exec_()
        self.active_profile = dialog.active_profile
        self._update_profile_label()
        self._update_quota_display(self.quota_tracker.snapshot())
        if self.active_profile is not None:
            return config.load_profile_api_key(self.active_profile["id"])
        return None

    def _on_new_instance_clicked(self) -> None:
        """Ouvre une nouvelle instance de Distillat directement depuis
        l'application (bouton en haut de la fenêtre, à droite du titre),
        sans avoir à relancer l'exe/le script manuellement de l'extérieur.
        Ne vérifie pas au préalable qu'un profil sera disponible pour la
        nouvelle instance : celle-ci affichera elle-même l'avertissement
        "aucun profil disponible" déjà existant (_resolve_active_profile())
        si besoin, sans dupliquer cette logique ici. La seule vérification
        faite ici est le nombre d'instances déjà vivantes
        (instance_lock.MAX_INSTANCES) : contrairement à la disponibilité de
        profil, ce n'est pas une ressource par compte Google mais un simple
        plafond d'ergonomie, qu'il vaut mieux vérifier avant de lancer un
        processus inutile plutôt qu'après."""
        if instance_lock.count_alive_instances() >= instance_lock.MAX_INSTANCES:
            QMessageBox.warning(
                self,
                tr("main_window.max_instances_title"),
                tr("main_window.max_instances_message", max_instances=instance_lock.MAX_INSTANCES),
            )
            return
        # En mode compilé, sys.executable pointe déjà sur Distillat.exe ; en
        # développement, il pointe sur l'interpréteur Python du venv, à qui
        # il faut alors passer le chemin de main.py en argument (résolu via
        # config.get_app_dir(), qui renvoie la racine du projet en dev).
        if getattr(sys, "frozen", False):
            args = [sys.executable]
        else:
            args = [sys.executable, str(config.get_app_dir() / "main.py")]
        try:
            subprocess.Popen(args)
        except OSError as exc:
            QMessageBox.critical(
                self,
                tr("main_window.new_instance_error_title"),
                tr("main_window.new_instance_error_message", error=exc),
            )

    def _on_edit_api_key(self) -> None:
        self._prompt_for_api_key()

    def _on_edit_prompts(self) -> None:
        # Les prompts personnalisés sont propres à chaque profil de clé API
        # (2026-07-22, support des profils multiples) : sans profil actif,
        # aucune personnalisation cohérente à afficher ni à sauvegarder.
        if self.active_profile is None:
            QMessageBox.warning(
                self,
                tr("prompts_dialog.no_profile_title"),
                tr("prompts_dialog.no_profile_message"),
            )
            return
        profile_id = self.active_profile["id"]
        current_prompts = load_custom_prompts(i18n.current_language(), profile_id)
        dialog = PromptsDialog(self, current_prompts=current_prompts, profile_id=profile_id)
        if dialog.exec_() == QDialog.Accepted:
            try:
                save_custom_prompts(i18n.current_language(), dialog.prompts(), profile_id)
            except OSError as exc:
                QMessageBox.critical(
                    self,
                    tr("prompts_dialog.save_error_title"),
                    tr("prompts_dialog.save_error_message", error=exc),
                )

    def _on_edit_quota_limits(self) -> None:
        # Les limites RPM/TPM/RPD sont propres à chaque clé API (voir
        # quota_tracker.quota_limits_path_for_key, 2026-07-22) : sans profil
        # actif, aucun fichier de limites n'est encore résolu pour cette
        # instance, donc rien de cohérent à éditer ni à sauvegarder.
        if self.quota_tracker.quota_limits_path is None:
            QMessageBox.warning(
                self,
                tr("quota_limits_dialog.no_profile_title"),
                tr("quota_limits_dialog.no_profile_message"),
            )
            return
        snapshot = self.quota_tracker.snapshot()
        dialog = QuotaLimitsDialog(
            self,
            current_rpm=snapshot.rpm_limit,
            current_tpm=snapshot.tpm_limit,
            current_rpd=snapshot.rpd_limit,
        )
        if dialog.exec_() == QDialog.Accepted:
            rpm_limit, tpm_limit, rpd_limit = dialog.limits()
            try:
                save_quota_limits(self.quota_tracker.quota_limits_path, rpm_limit, tpm_limit, rpd_limit)
            except OSError as exc:
                QMessageBox.critical(
                    self,
                    tr("quota_limits_dialog.save_error_title"),
                    tr("quota_limits_dialog.save_error_message", error=exc),
                )
                return
            self.quota_tracker.reload_limits()
            self._update_quota_display(self.quota_tracker.snapshot())

    def _on_show_license(self) -> None:
        LicenseDialog(self).exec_()

    def _on_show_changelog(self) -> None:
        ChangelogDialog(self).exec_()

    def _on_show_quota_help(self) -> None:
        QuotaHelpDialog(self).exec_()

    def _on_open_repo_page(self) -> None:
        webbrowser.open(repo_page_url())

    def show_update_banner(self, latest_version: str) -> None:
        self._latest_version_available = latest_version
        self.update_banner_label.setText(tr("main_window.update_banner", version=latest_version))
        self.update_banner_label.show()

    def _on_open_releases_page(self) -> None:
        webbrowser.open(releases_page_url())

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
        self.file_label.setText(tr("main_window.file_selected", filename=os.path.basename(path)))
        self.remove_file_button.show()
        self._set_summarize_button_enabled(True)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText(tr("main_window.idle_status"))
        self.status_label.setStyleSheet("color: #555;")
        self.extra_text_label.hide()
        self.last_result = None
        self._last_report_source_path = None
        self._report_dirty = False
        self._clear_result_tabs()

    def _on_remove_file_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        self.selected_book_path = None
        self.file_label.setText(tr("main_window.no_file_selected"))
        self.remove_file_button.hide()
        self._set_summarize_button_enabled(False)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText(tr("main_window.idle_status"))
        self.status_label.setStyleSheet("color: #555;")
        self.extra_text_label.hide()
        self.last_result = None
        self._last_report_source_path = None
        self._report_dirty = False
        self._clear_result_tabs()

    def _on_close_report_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        self.last_result = None
        self._last_report_source_path = None
        self._report_dirty = False
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText(tr("main_window.idle_status"))
        self.status_label.setStyleSheet("color: #555;")
        self.extra_text_label.hide()
        self._clear_result_tabs()
        self.result_tabs.setCurrentIndex(0)

    def _confirm_discard_unsaved_report(self) -> bool:
        """Retourne True si on peut continuer (rien à perdre, modifications
        abandonnées, ou fiche sauvegardée avec succès)."""
        if not self._report_dirty or not self.last_result:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("main_window.unsaved_report_dialog.window_title"))
        box.setText(tr("main_window.unsaved_report_dialog.text"))

        discard_button = box.addButton(
            tr("main_window.unsaved_report_dialog.discard_button"), QMessageBox.YesRole
        )
        discard_button.setStyleSheet(
            "background-color: #d9362e; color: white; font-weight: bold;"
        )
        cancel_button = box.addButton(
            tr("main_window.unsaved_report_dialog.cancel_button"), QMessageBox.RejectRole
        )
        cancel_button.setStyleSheet("background-color: #9aa5b1; color: white;")
        save_button = box.addButton(
            tr("main_window.unsaved_report_dialog.save_button"), QMessageBox.ActionRole
        )
        save_button.setStyleSheet(
            "background-color: #2ea04f; color: white; font-weight: bold;"
        )
        box.setDefaultButton(cancel_button)
        box.exec_()

        clicked = box.clickedButton()
        if clicked is discard_button:
            return True
        if clicked is save_button:
            return self._on_save_report_clicked()
        return False

    def _on_result_edited(self) -> None:
        """Marque la fiche comme modifiée dès qu'une édition manuelle a lieu
        dans un des onglets de résultat (résumés, analyse, personnages,
        auteur sur l'onglet Couverture)."""
        if self.last_result is not None:
            self._report_dirty = True

    def _sync_edits_to_last_result(self) -> None:
        """Reprend le contenu actuellement affiché (potentiellement édité à la
        main) dans self.last_result, avant toute sauvegarde (fiche JSON ou
        export .pdf) pour que le texte modifié soit bien celui conservé."""
        if self.last_result is None:
            return

        book_title = self.book_title_input.text().strip()
        if book_title:
            self.last_result.book_title = book_title
        author = self.book_author_input.text().strip()
        if author:
            self.last_result.author = author

        # toPlainText (et non toMarkdown) : le contenu est affiché en rendu
        # Markdown (titres stylés sans les #), mais toMarkdown recoupe les
        # lignes trop longues autour de 80 colonnes (wrap purement visuel,
        # sans signification), ce qui casserait un titre long en 2 blocs.
        # toPlainText restitue un vrai retour à la ligne par paragraphe/titre
        # sans cet artefact ; _from_display_plain_text réassocie chaque bloc
        # au préfixe #/##/### du texte source d'origine.
        self.last_result.summary_text = _from_display_plain_text(
            self.summary_view.toPlainText(), self.last_result.summary_text
        )
        self.last_result.detailed_summary_text = _from_display_plain_text(
            self.detailed_summary_view.toPlainText(), self.last_result.detailed_summary_text
        )
        self.last_result.analysis_text = _from_display_plain_text(
            self.analysis_view.toPlainText(), self.last_result.analysis_text
        )

        characters = []
        for name_input, description_input in zip(
            self.character_name_inputs, self.character_description_inputs
        ):
            name = name_input.text().strip()
            description = description_input.toPlainText().strip()
            if name and description:
                characters.append(Character(name=name, description=description))
        self.last_result.characters = characters

    def _confirm_abort_running_generation(self) -> bool:
        """Retourne True si on peut fermer l'application (aucune génération en
        cours, ou l'utilisateur accepte de l'interrompre). Sans cette étape,
        fermer la fenêtre pendant une génération laissait le QThread actif au
        moment de sa destruction ("QThread: Destroyed while thread is still
        running"), avec plantage possible à la sortie."""
        if self.worker is None or not self.worker.isRunning():
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("main_window.abort_generation_dialog.window_title"))
        box.setText(tr("main_window.abort_generation_dialog.text"))
        quit_button = box.addButton(
            tr("main_window.abort_generation_dialog.quit_button"), QMessageBox.YesRole
        )
        quit_button.setStyleSheet(
            "background-color: #d9362e; color: white; font-weight: bold;"
        )
        stay_button = box.addButton(
            tr("main_window.abort_generation_dialog.stay_button"), QMessageBox.RejectRole
        )
        stay_button.setStyleSheet("background-color: #9aa5b1; color: white;")
        box.setDefaultButton(stay_button)
        box.exec_()
        if box.clickedButton() is not quit_button:
            return False

        # Plus aucun signal du worker ne doit atteindre la fenêtre en cours de
        # fermeture (ex : failed, qui ouvrirait un QMessageBox).
        for signal in (
            self.worker.progress,
            self.worker.quota_updated,
            self.worker.finished_ok,
            self.worker.failed,
        ):
            try:
                signal.disconnect()
            except TypeError:
                pass
        # terminate() est brutal (le thread est tué en plein appel réseau ou
        # parsing), acceptable ici uniquement parce que le processus se
        # termine juste après ; wait() garantit que le QThread n'est plus
        # actif au moment de sa destruction.
        self.worker.terminate()
        self.worker.wait()
        return True

    def closeEvent(self, event) -> None:
        if not self._confirm_abort_running_generation():
            event.ignore()
            return
        if self._confirm_discard_unsaved_report():
            if self.active_profile is not None:
                instance_lock.release_profile_lock(self.active_profile["id"])
            instance_lock.unregister_instance()
            event.accept()
        else:
            event.ignore()

    def _offer_pending_resumes(self) -> None:
        states = generation_resume.load_all_resume_states(config.get_settings_dir())
        if not states:
            return

        dialog = PendingResumesDialog(states, self)
        accepted = dialog.exec_() == QDialog.Accepted

        for cleared_state in dialog.states_to_clear:
            generation_resume.clear_resume_state(config.get_settings_dir(), cleared_state.book_hash)

        if accepted and dialog.selected_state is not None:
            self._on_file_selected(dialog.selected_state.book_path)

    def _find_resume_state_for(self, book_path: str | None) -> generation_resume.ResumeState | None:
        if not book_path:
            return None
        for state in generation_resume.load_all_resume_states(config.get_settings_dir()):
            if state.book_path == book_path:
                return state
        return None

    def _on_summarize_clicked(self) -> None:
        if not self.selected_book_path:
            return

        if not self._confirm_discard_unsaved_report():
            return

        api_key = self._ensure_api_key(prompt_if_missing=True)
        if not api_key:
            return
        self.quota_tracker.switch_api_key(api_key)

        resume_state = self._find_resume_state_for(self.selected_book_path)

        self._set_summarize_button_enabled(False)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        # Charger une fiche pendant une génération l'affichait aussitôt, pour
        # la voir écrasée sans confirmation dès que finished_ok arrivait ;
        # modifier les prompts pendant le traitement changeait le
        # comportement des lots restants d'une même génération.
        self.load_report_button.setEnabled(False)
        self.prompts_button.setEnabled(False)
        self.drop_zone.set_busy(True)
        # last_result est remis à None AVANT de vider les onglets : leur
        # clear() émet textChanged, que _on_result_edited interpréterait
        # sinon comme une édition manuelle de la fiche précédente. Ce faux
        # « fiche modifiée » ressortait après un échec de génération
        # (dialogue « fiche non sauvegardée » injustifié à la fermeture),
        # avec pire encore : « sauvegarder et fermer » recopiait alors les
        # onglets vidés dans la fiche précédente et pouvait écraser son
        # fichier d'origine sur disque avec du contenu vide.
        self.last_result = None
        self._last_report_source_path = None
        self._report_dirty = False
        self.extra_text_label.hide()
        self._clear_result_tabs()

        self._last_progress_message = tr("main_window.starting_status")
        self._elapsed_seconds = 0
        self.status_label.setStyleSheet("color: #555;")
        self._render_status_with_elapsed()
        self._elapsed_timer.start()

        self.quota_warning_label.hide()
        # Hash de la clé API (jamais la clé en clair) en tête de chaque
        # génération : permet de distinguer dans le journal d'appels API
        # quelles requêtes proviennent de quel compte Google/clé, en testant
        # avec plusieurs comptes (un même hash = même clé, sans jamais
        # exposer la clé elle-même si le fichier de log est partagé).
        api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
        log_api_event(f"generation DEMARRAGE cle_api_hash={api_key_hash}")
        profile_id = self.active_profile["id"] if self.active_profile is not None else None
        self.worker = SummarizeWorker(self.selected_book_path, api_key, self.quota_tracker, profile_id, resume_state)
        self.worker.progress.connect(self._on_progress)
        self.worker.quota_updated.connect(self._update_quota_display)
        self.worker.finished_ok.connect(self._on_finished_ok)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        minutes, seconds = divmod(max(total_seconds, 0), 60)
        if minutes:
            return tr("main_window.duration_minutes", minutes=minutes, seconds=seconds)
        return tr("main_window.duration_seconds", seconds=seconds)

    def _render_status_with_elapsed(self) -> None:
        elapsed = self._format_duration(self._elapsed_seconds)
        self.status_label.setText(
            tr("main_window.elapsed_suffix", message=self._last_progress_message, elapsed=elapsed)
        )

    def _on_elapsed_tick(self) -> None:
        self._elapsed_seconds += 1
        self._render_status_with_elapsed()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._last_progress_message = message
        self._render_status_with_elapsed()

    QUOTA_WARNING_THRESHOLD = 0.8

    def _update_quota_display(self, snapshot: QuotaSnapshot) -> None:
        in_flight_suffix = (
            tr("main_window.quota_display_in_flight_suffix", count=snapshot.requests_in_flight)
            if snapshot.requests_in_flight > 0
            else ""
        )
        self.quota_label.setText(
            tr(
                "main_window.quota_display",
                input_tokens=f"{snapshot.input_tokens_total:,}".replace(",", " "),
                output_tokens=f"{snapshot.output_tokens_total:,}".replace(",", " "),
                total_tokens=f"{snapshot.input_tokens_total + snapshot.output_tokens_total:,}".replace(",", " "),
                rpm=snapshot.requests_per_minute,
                rpm_limit=snapshot.rpm_limit,
                tpm=f"{snapshot.tokens_per_minute:,}".replace(",", " "),
                tpm_limit=f"{snapshot.tpm_limit:,}".replace(",", " "),
                rpd=snapshot.requests_today,
                rpd_limit=snapshot.rpd_limit,
                in_flight_suffix=in_flight_suffix,
            )
        )
        self._check_quota_thresholds(snapshot)

    def _check_quota_thresholds(self, snapshot: QuotaSnapshot) -> None:
        warnings = []
        if snapshot.requests_today >= snapshot.rpd_limit * self.QUOTA_WARNING_THRESHOLD:
            warnings.append(
                tr("main_window.quota_threshold_rpd", used=snapshot.requests_today, limit=snapshot.rpd_limit)
            )
        if snapshot.requests_per_minute >= snapshot.rpm_limit * self.QUOTA_WARNING_THRESHOLD:
            warnings.append(
                tr(
                    "main_window.quota_threshold_rpm",
                    used=snapshot.requests_per_minute,
                    limit=snapshot.rpm_limit,
                )
            )
        if snapshot.tokens_per_minute >= snapshot.tpm_limit * self.QUOTA_WARNING_THRESHOLD:
            tpm_used = f"{snapshot.tokens_per_minute:,}".replace(",", " ")
            tpm_limit = f"{snapshot.tpm_limit:,}".replace(",", " ")
            warnings.append(tr("main_window.quota_threshold_tpm", used=tpm_used, limit=tpm_limit))

        if warnings:
            new_text = tr("main_window.quota_threshold_prefix") + " · ".join(warnings)
            if self.quota_threshold_label.text() != new_text:
                self.quota_threshold_label.setText(new_text)
            if self.quota_threshold_label.isHidden():
                self.quota_threshold_label.show()
        elif not self.quota_threshold_label.isHidden():
            self.quota_threshold_label.hide()

    def _clear_result_tabs(self) -> None:
        self.book_title_input.setText("")
        self.book_author_input.setText("")
        self.cover_label.clear()
        self.cover_label.setText(tr("main_window.no_cover"))
        self.summary_view.clear()
        self.detailed_summary_view.clear()
        self.analysis_view.clear()
        self._clear_characters_tab()
        placeholder = QLabel(tr("main_window.characters_placeholder"))
        placeholder.setStyleSheet("color: #888; padding: 12px;")
        placeholder.setWordWrap(True)
        self.characters_layout.insertWidget(0, placeholder)

    def _display_book_report(self, result: BookReport) -> None:
        self.book_title_input.setText(result.book_title)
        self.book_author_input.setText(result.author)

        self._display_cover(result)

        # blockSignals évite que le remplissage programmatique du contenu
        # (setMarkdown) soit interprété comme une édition manuelle de
        # l'utilisateur, ce qui marquerait à tort la fiche comme modifiée.
        for widget in (self.summary_view, self.detailed_summary_view, self.analysis_view):
            widget.blockSignals(True)
        self.summary_view.setMarkdown(_to_display_markdown(result.summary_text))
        self.detailed_summary_view.setMarkdown(
            _to_display_markdown(
                result.detailed_summary_text or tr("main_window.no_detailed_summary")
            )
        )
        self.analysis_view.setMarkdown(_to_display_markdown(result.analysis_text))
        for widget in (self.summary_view, self.detailed_summary_view, self.analysis_view):
            _style_display_paragraphs(widget)
            widget.blockSignals(False)

        self._populate_characters_tab(result.characters)

    def _on_show_extra_generated_text(self) -> None:
        if not self.last_result or not self.last_result.extra_generated_text:
            return
        # Référence gardée sur self : sans elle, PyQt détruirait la fenêtre
        # dès la fin de cette méthode puisque non modale (pas d'exec_()
        # bloquant qui la garde vivante).
        self._extra_text_dialog = ExtraTextDialog(self.last_result.extra_generated_text, self)
        self._extra_text_dialog.show()

    def _display_cover(self, result: BookReport) -> None:
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
                self.cover_label.setText(tr("main_window.no_cover"))
        else:
            self.cover_label.setText(tr("main_window.no_cover"))

    def _on_cover_context_menu(self, pos) -> None:
        if not self.last_result:
            return
        menu = QMenu(self)
        set_cover_action = menu.addAction(tr("main_window.set_cover_action"))
        set_cover_action.triggered.connect(self._on_set_cover_manually)
        menu.exec_(self.cover_label.mapToGlobal(pos))

    def _on_set_cover_manually(self) -> None:
        if not self.last_result:
            return
        default_dir = config.load_last_cover_dir()
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("main_window.set_cover_dialog_title"),
            str(default_dir) if default_dir else "",
            tr("main_window.set_cover_dialog_filter"),
        )
        if not path:
            return
        raw_bytes = Path(path).read_bytes()
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw_bytes):
            QMessageBox.warning(
                self, tr("main_window.set_cover_action"), tr("main_window.set_cover_invalid_image")
            )
            return
        self.last_result.cover_image = shrink_cover_image(raw_bytes)
        self._report_dirty = True
        self._display_cover(self.last_result)
        config.save_last_cover_dir(Path(path).parent)

    def _on_finished_ok(self, result: BookReport) -> None:
        self._elapsed_timer.stop()
        self.last_result = result
        self._last_result_source_stem = (
            Path(self.selected_book_path).stem if self.selected_book_path else None
        )
        self._last_report_source_path = None
        self._report_dirty = True
        self._display_book_report(result)
        self.result_tabs.setCurrentIndex(0)
        winsound.PlaySound(
            str(config.get_success_sound_path()), winsound.SND_FILENAME | winsound.SND_ASYNC
        )
        mode = (
            tr("main_window.mode_split", chapter_count=result.chapter_count)
            if result.was_split
            else tr("main_window.mode_single")
        )
        duration = self._format_duration(self._elapsed_seconds)
        self.status_label.setStyleSheet("color: #2ea04f; font-weight: bold;")
        self.status_label.setText(tr("main_window.finished_status", duration=duration, mode=mode))
        if result.extra_generated_text:
            self.extra_text_label.show()
        else:
            self.extra_text_label.hide()
        self._set_summarize_button_enabled(True)
        self.save_button.setEnabled(True)
        self.save_report_button.setEnabled(True)
        self.close_report_button.setEnabled(True)
        self.load_report_button.setEnabled(True)
        self.prompts_button.setEnabled(True)
        self.drop_zone.set_busy(False)
        # Filet de sécurité : après une rafale de show()/hide()/setText() sur les
        # labels de quota pendant le traitement, force un repaint pour garantir
        # que le contenu final s'affiche (observé bloqué visuellement sur Windows
        # après un traitement long, bien que les données soient à jour en mémoire).
        self.repaint()

    def _on_failed(self, error_message: str, error_kind: str | None) -> None:
        self._elapsed_timer.stop()
        duration = self._format_duration(self._elapsed_seconds)
        self.status_label.setStyleSheet("color: #b02a2a; font-weight: bold;")
        self.status_label.setText(tr("main_window.failed_status", duration=duration))
        self._set_summarize_button_enabled(True)
        self.load_report_button.setEnabled(True)
        self.prompts_button.setEnabled(True)
        self.drop_zone.set_busy(False)
        if error_kind in ("daily_quota", "rate_quota"):
            self.quota_warning_label.setText(tr("main_window.quota_exceeded_warning"))
            self.quota_warning_label.show()
        resume_state = self._find_resume_state_for(self.selected_book_path)
        if resume_state is not None:
            if error_kind == "daily_quota":
                wait_hint = tr("main_window.resume_wait_hint_daily")
            elif error_kind == "rate_quota":
                wait_hint = tr("main_window.resume_wait_hint_minute")
            else:
                # Pas un problème de quota (ex : réponse mal formée renvoyée par Gemini,
                # aléa ponctuel) : aucun délai à attendre, un nouvel essai peut suffire
                # immédiatement, contrairement aux cas de quota traités ci-dessus.
                wait_hint = tr("main_window.resume_wait_hint_other")
            error_message += tr(
                "main_window.resume_hint_suffix",
                batches_done=resume_state.batches_done,
                batches_total=resume_state.batches_total,
                wait_hint=wait_hint,
            )
        QMessageBox.critical(self, tr("main_window.error_title"), error_message)

    def _default_save_dir(self) -> Path:
        """Dossier proposé par défaut à l'enregistrement d'une fiche : celui
        d'où provient la fiche affichée si elle a été chargée, sinon le
        dernier dossier utilisé pour une fiche, sinon Documents\\Distillat\\Fiches."""
        if self._last_report_source_path is not None:
            return self._last_report_source_path.parent
        return config.load_last_report_dir() or config.get_reports_dir()

    def _default_pdf_dir(self) -> Path:
        """Dossier proposé par défaut à l'export PDF : celui d'où provient la
        fiche affichée si elle a été chargée, sinon le dernier dossier utilisé
        pour un export PDF, sinon Documents\\Distillat\\Fiches."""
        if self._last_report_source_path is not None:
            return self._last_report_source_path.parent
        return config.load_last_pdf_dir() or config.get_reports_dir()

    def _on_save_clicked(self) -> None:
        if not self.last_result:
            return
        self._sync_edits_to_last_result()

        base_name = self._last_result_source_stem or self.last_result.book_title
        default_path = str(self._default_pdf_dir() / f"{sanitize_filename(base_name)}.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("main_window.save_pdf_dialog_title"), default_path, tr("main_window.save_pdf_filter")
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            export_book_report_to_pdf(self.last_result, path)
            config.save_last_pdf_dir(Path(path).parent)
            QMessageBox.information(
                self, tr("main_window.save_success_title"), tr("main_window.pdf_saved_message", path=path)
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("main_window.save_error_title"), str(exc))

    def _on_save_report_clicked(self) -> bool:
        """Retourne True si la fiche a bien été sauvegardée (False si annulé
        par l'utilisateur ou en erreur), pour permettre à l'appelant de savoir
        s'il peut continuer une action qui dépendait de cette sauvegarde."""
        if not self.last_result:
            return False
        self._sync_edits_to_last_result()

        if self._last_report_source_path is not None:
            default_name = self._last_report_source_path.name
        else:
            default_name = self.last_result.suggested_filename(self._last_result_source_stem)
        default_path = str(self._default_save_dir() / default_name)
        # DontConfirmOverwrite : réenregistrer la fiche qu'on vient d'ouvrir ne
        # doit pas demander « le fichier existe déjà, remplacer ? ». La
        # confirmation est reposée à la main uniquement si la cible est un
        # AUTRE fichier existant.
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("main_window.save_report_dialog_title"),
            default_path,
            tr("main_window.save_report_filter"),
            options=QFileDialog.DontConfirmOverwrite,
        )
        if not path:
            return False
        if not path.lower().endswith(REPORT_EXTENSION):
            path = path[: -len(".json")] if path.lower().endswith(".json") else path
            path += REPORT_EXTENSION

        target = Path(path)
        overwrites_source = (
            self._last_report_source_path is not None
            and target == self._last_report_source_path
        )
        if target.exists() and not overwrites_source:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle(tr("main_window.existing_file_title"))
            box.setText(tr("main_window.existing_file_message", path=path))
            yes_button = box.addButton(tr("main_window.yes"), QMessageBox.YesRole)
            no_button = box.addButton(tr("main_window.no"), QMessageBox.NoRole)
            box.setDefaultButton(no_button)
            box.exec_()
            if box.clickedButton() is not yes_button:
                return False

        try:
            self.last_result.save(target)
            self._report_dirty = False
            self._last_report_source_path = target
            self._last_result_source_stem = target.stem.removesuffix(".distillat")
            config.save_last_report_dir(target.parent)
            if overwrites_source:
                QMessageBox.information(
                    self, tr("main_window.save_success_title"), tr("main_window.report_overwritten_message")
                )
            else:
                QMessageBox.information(
                    self, tr("main_window.save_success_title"), tr("main_window.report_saved_message", path=path)
                )
            return True
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("main_window.save_error_title"), str(exc))
            return False

    def _on_load_report_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        default_dir = config.load_last_report_dir() or config.get_reports_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, tr("main_window.load_report_dialog_title"), str(default_dir), tr("main_window.load_report_filter")
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
        # TypeError/AttributeError couvrent un JSON syntaxiquement valide mais
        # de forme inattendue (racine qui n'est pas un objet, entrée de
        # characters qui n'est pas un dict...) : sans cela, une fiche
        # corrompue ou modifiée à la main plantait l'application au lieu
        # d'afficher ce message d'erreur.
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            QMessageBox.critical(
                self, tr("main_window.load_error_title"), tr("main_window.load_error_message", error=exc)
            )
            return

        self.last_result = result
        self._last_result_source_stem = Path(path).stem.removesuffix(".distillat")
        self._last_report_source_path = Path(path)
        self._report_dirty = False
        config.save_last_report_dir(self._last_report_source_path.parent)
        self._display_book_report(result)
        self.status_label.setText(tr("main_window.report_loaded_status", filename=os.path.basename(path)))
        self.extra_text_label.hide()
        self.save_button.setEnabled(True)
        self.save_report_button.setEnabled(True)
        self.close_report_button.setEnabled(True)
