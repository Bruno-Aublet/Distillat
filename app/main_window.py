"""Fenêtre principale de l'application Distillat."""
import os
import re
import webbrowser
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
)
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
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import config, generation_resume
from app.__version__ import VERSION
from app.update_checker import check_for_updates_on_startup, releases_page_url
from app.book_report import BookReport, Character, sanitize_filename
from app.gemini_client import DEFAULT_PROMPT_TEMPLATES, MODEL_NAME
from app.pdf_export import export_book_report_to_pdf
from app.prompts_store import load_custom_prompts, save_custom_prompts
from app.quota_tracker import QuotaSnapshot, QuotaTracker, save_quota_limits
from app.worker import SummarizeWorker


class LicenseDialog(QDialog):
    """Affiche le contenu du fichier LICENSE à la racine du projet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
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
        self.setWindowTitle("Contenu supplémentaire ignoré")
        self.resize(600, 450)

        layout = QVBoxLayout(self)

        explanation = QLabel(
            "Gemini a produit ce texte en plus du contenu utilisé pour la fiche. "
            "Il peut s'agir d'une répétition sans intérêt ou de contenu légitime : "
            "vérifiez-le et copiez-collez ce qui vous semble utile."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #555;")
        layout.addWidget(explanation)

        text_view = QTextEdit()
        text_view.setPlainText(text)
        text_view.setReadOnly(True)
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)
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
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle("Clé API Gemini")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Récupérez votre clé gratuite sur "
            '<a href="https://aistudio.google.com/apikey">Google AI Studio</a>. '
            "Elle sera enregistrée de façon chiffrée via le Gestionnaire "
            "d'identification Windows - ne la partagez pas."
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


class QuotaLimitsDialog(QDialog):
    """Boîte de dialogue pour ajuster manuellement les limites RPM/TPM/RPD
    affichées dans l'application, si Google modifie le palier gratuit."""

    def __init__(self, parent=None, current_rpm: int = 0, current_tpm: int = 0, current_rpd: int = 0):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle("Limites de quota Gemini")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        model_label = QLabel(f"Modèle utilisé : <b>{MODEL_NAME}</b>")
        model_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(model_label)

        info = QLabel(
            "Google ne publie pas ces limites via l'API. Consultez les vôtres sur "
            '<a href="https://aistudio.google.com/rate-limit">aistudio.google.com/rate-limit</a> '
            "(pour ce modèle précisément) et ajustez-les ici si elles diffèrent de "
            "celles affichées dans l'application."
        )
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
        form.addRow("Requêtes par minute (RPM) :", self.rpm_input)

        self.tpm_input = QSpinBox()
        self.tpm_input.setRange(1, 100_000_000)
        self.tpm_input.setSingleStep(1000)
        self.tpm_input.setValue(current_tpm)
        self.tpm_input.setFixedWidth(100)
        form.addRow("Tokens par minute (TPM) :", self.tpm_input)

        self.rpd_input = QSpinBox()
        self.rpd_input.setRange(1, 1_000_000)
        self.rpd_input.setValue(current_rpd)
        self.rpd_input.setFixedWidth(100)
        form.addRow("Requêtes par jour (RPD) :", self.rpd_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def limits(self) -> tuple[int, int, int]:
        return self.rpm_input.value(), self.tpm_input.value(), self.rpd_input.value()


_PROMPT_TABS: tuple[tuple[str, str, str], ...] = (
    (
        "full_report",
        "Résumé + personnages + analyse",
        "Cas le plus courant : le livre tient en entier dans une seule requête. Ce prompt "
        "demande en une fois le résumé court, le résumé détaillé, les personnages et "
        "l'analyse littéraire.",
    ),
    (
        "chapter_summary",
        "1. Résumé d'un lot de chapitres",
        "Cas d'un livre trop volumineux pour tenir dans une seule requête : premier prompt "
        "du découpage. Le livre est réparti en lots de plusieurs chapitres consécutifs (le "
        "plus possible à la fois selon la taille du livre), pour limiter le nombre de "
        "requêtes envoyées à l'API (le quota gratuit journalier est très limité). Ce prompt "
        "reçoit un lot et doit résumer chaque chapitre du lot séparément.",
    ),
    (
        "consolidation",
        "2. Fusion résumé + personnages + analyse",
        "Toujours dans le cas d'un livre découpé : une fois tous les lots résumés "
        "séparément, ce prompt reçoit l'ensemble des résumés de chapitre (jamais le texte "
        "intégral) et produit en une seule fois le résumé court, le résumé détaillé, les "
        "personnages et l'analyse littéraire du livre entier.",
    ),
)


class PromptsDialog(QDialog):
    """Fenêtre permettant de consulter et modifier les prompts envoyés à
    Gemini. Chaque prompt a sa propre zone de saisie et son propre bouton de
    réinitialisation (n'affecte que cette zone)."""

    def __init__(self, parent=None, current_prompts: dict[str, str] | None = None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle("Prompts Gemini")
        self.resize(750, 600)

        current_prompts = current_prompts or {}

        layout = QVBoxLayout(self)

        warning = QLabel(
            "⚠️ Ces prompts pilotent directement la génération des fiches et fonctionnent tels "
            "quels. Les modifier peut faire échouer la génération ou produire des résultats "
            "incohérents (résultat non exploitable, réponse non reconnue par l'application, etc.) : "
            "modifiez-les à vos risques et périls. Les portions entre accolades, par exemple "
            "{book_title} ou {full_text}, sont remplacées automatiquement par l'application : "
            "ne les supprimez pas et ne changez pas leur orthographe, sous peine d'erreur."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b02a2a; font-weight: bold;")
        layout.addWidget(warning)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        self._text_edits: dict[str, QTextEdit] = {}
        for key, tab_title, tab_explanation in _PROMPT_TABS:
            self._text_edits[key] = self._build_tab(key, tab_title, tab_explanation, current_prompts.get(key, ""))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_tab(self, key: str, tab_title: str, tab_explanation: str, initial_text: str) -> QTextEdit:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)

        explanation = QLabel(tab_explanation)
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #555; font-style: italic;")
        tab_layout.addWidget(explanation)

        text_edit = QTextEdit()
        text_edit.setPlainText(initial_text or DEFAULT_PROMPT_TEMPLATES[key])
        text_edit.setFontFamily("Courier New")
        tab_layout.addWidget(text_edit)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_button = QPushButton("Réinitialiser ce prompt")
        reset_button.clicked.connect(lambda: text_edit.setPlainText(DEFAULT_PROMPT_TEMPLATES[key]))
        reset_row.addWidget(reset_button)
        tab_layout.addLayout(reset_row)

        self.tabs.addTab(tab, tab_title)
        return text_edit

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

    def __init__(self, on_file_dropped, on_report_dropped, parent=None):
        super().__init__(parent)
        self.on_file_dropped = on_file_dropped
        self.on_report_dropped = on_report_dropped
        self.busy = False
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(180)
        self.setText("📚\n\nGlissez-déposez un fichier EPUB, PDF ou une fiche .distillat.json ici\nou cliquez pour parcourir")
        self._set_style(active=False)

    def set_busy(self, busy: bool) -> None:
        """Désactive le dépôt et le clic (choix de fichier) tant qu'un résumé
        est en cours de génération, pour éviter de remplacer le fichier
        sélectionné ou d'écraser la fiche affichée pendant le traitement."""
        self.busy = busy
        if busy:
            self.setText(
                "📚\n\nRésumé en cours...\nGlissez-déposez un nouveau fichier une fois terminé"
            )
        else:
            self.setText(
                "📚\n\nGlissez-déposez un fichier EPUB, PDF ou une fiche .distillat.json ici\n"
                "ou cliquez pour parcourir"
            )

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Distillat v{VERSION} - Résumé de livres avec Gemini")
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
        self.quota_tracker = QuotaTracker(
            daily_state_path=config.get_settings_dir() / ".quota_state.json",
            settings_dir=config.get_settings_dir(),
        )

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
        self._ensure_api_key(prompt_if_missing=False)
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
        self.prompts_button = QPushButton("Prompts")
        self.prompts_button.clicked.connect(self._on_edit_prompts)
        header.addWidget(self.prompts_button)
        self.quota_limits_button = QPushButton("Limites de quota")
        self.quota_limits_button.clicked.connect(self._on_edit_quota_limits)
        header.addWidget(self.quota_limits_button)
        self.api_key_button = QPushButton("Clé API")
        self.api_key_button.clicked.connect(self._on_edit_api_key)
        header.addWidget(self.api_key_button)
        layout.addLayout(header)

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
        layout.addLayout(action_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #555;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Discret et masqué par défaut : n'apparaît que si Gemini a produit du
        # texte en trop après le premier objet JSON exploité (cas rare d'une
        # réponse mal formée). Ce texte peut être légitime, ce n'est pas à
        # l'application de décider silencieusement qu'il ne sert à rien.
        self.extra_text_label = QLabel(
            '<a href="#">ℹ️ Du contenu supplémentaire généré par Gemini a été ignoré, cliquez pour le consulter</a>'
        )
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
        result_row.addStretch()

        self.load_report_button = QPushButton("Charger une fiche")
        self.load_report_button.clicked.connect(self._on_load_report_clicked)
        result_row.addWidget(self.load_report_button)

        self.save_report_button = QPushButton("Sauvegarder la fiche")
        self.save_report_button.setEnabled(False)
        self.save_report_button.clicked.connect(self._on_save_report_clicked)
        result_row.addWidget(self.save_report_button)

        self.close_report_button = QPushButton("Fermer la fiche")
        self.close_report_button.setEnabled(False)
        self.close_report_button.clicked.connect(self._on_close_report_clicked)
        result_row.addWidget(self.close_report_button)

        self.save_button = QPushButton("Exporter en .pdf")
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
        self.result_tabs.addTab(tab, "Couverture")

    def _build_summary_tab(self) -> None:
        self.summary_view = QTextEdit()
        self.summary_view.setStyleSheet("font-size: 15px;")
        self.summary_view.setPlaceholderText("Le résumé court en français apparaîtra ici après traitement.")
        self.summary_view.textChanged.connect(self._on_result_edited)
        self.result_tabs.addTab(self.summary_view, "Résumé court")

    def _build_detailed_summary_tab(self) -> None:
        self.detailed_summary_view = QTextEdit()
        self.detailed_summary_view.setStyleSheet("font-size: 15px;")
        self.detailed_summary_view.setPlaceholderText(
            "Le résumé détaillé en français apparaîtra ici après traitement."
        )
        self.detailed_summary_view.textChanged.connect(self._on_result_edited)
        self.result_tabs.addTab(self.detailed_summary_view, "Résumé détaillé")

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

        self.characters_placeholder = QLabel(
            "Les fiches des personnages principaux apparaîtront ici après traitement."
        )
        self.characters_placeholder.setStyleSheet("color: #888; font-size: 15px;")
        self.characters_placeholder.setWordWrap(True)
        self.characters_layout.insertWidget(0, self.characters_placeholder)

        self.result_tabs.addTab(scroll, "Personnages")

    def _build_analysis_tab(self) -> None:
        self.analysis_view = QTextEdit()
        self.analysis_view.setStyleSheet("font-size: 15px;")
        self.analysis_view.setPlaceholderText("L'analyse littéraire apparaîtra ici après traitement.")
        self.analysis_view.textChanged.connect(self._on_result_edited)
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
        self.character_name_inputs = []
        self.character_description_inputs = []
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
            if not config.save_api_key(api_key):
                QMessageBox.critical(
                    self,
                    "Erreur d'enregistrement",
                    "Impossible d'enregistrer la clé API : le Gestionnaire d'identification "
                    "Windows est indisponible.",
                )
                return None
            return api_key
        return None

    def _on_edit_api_key(self) -> None:
        self._prompt_for_api_key()

    def _on_edit_prompts(self) -> None:
        current_prompts = load_custom_prompts(config.get_settings_dir())
        dialog = PromptsDialog(self, current_prompts=current_prompts)
        if dialog.exec_() == QDialog.Accepted:
            try:
                save_custom_prompts(config.get_settings_dir(), dialog.prompts())
            except OSError as exc:
                QMessageBox.critical(self, "Erreur de sauvegarde", f"Impossible d'enregistrer les prompts : {exc}")

    def _on_edit_quota_limits(self) -> None:
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
                save_quota_limits(config.get_settings_dir(), rpm_limit, tpm_limit, rpd_limit)
            except OSError as exc:
                QMessageBox.critical(
                    self, "Erreur de sauvegarde", f"Impossible d'enregistrer les limites de quota : {exc}"
                )
                return
            self.quota_tracker.reload_limits()
            self._update_quota_display(self.quota_tracker.snapshot())

    def _on_show_license(self) -> None:
        LicenseDialog(self).exec_()

    def show_update_banner(self, latest_version: str) -> None:
        self.update_banner_label.setText(
            f'<a href="#">🆕 Une nouvelle version de Distillat est disponible '
            f"(v{latest_version}), cliquez pour la télécharger</a>"
        )
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
        self.file_label.setText(f"Fichier sélectionné : {os.path.basename(path)}")
        self.remove_file_button.show()
        self._set_summarize_button_enabled(True)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText("")
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
        self.file_label.setText("Aucun fichier sélectionné")
        self.remove_file_button.hide()
        self._set_summarize_button_enabled(False)
        self.save_button.setEnabled(False)
        self.save_report_button.setEnabled(False)
        self.close_report_button.setEnabled(False)
        self.status_label.setText("")
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
        self.status_label.setText("")
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
        box.setWindowTitle("Fiche non sauvegardée")
        box.setText(
            "La fiche actuelle n'a pas été sauvegardée. Voulez-vous continuer sans sauvegarder ?"
        )

        discard_button = box.addButton("Oui : perdre les modifications", QMessageBox.YesRole)
        discard_button.setStyleSheet(
            "background-color: #d9362e; color: white; font-weight: bold;"
        )
        cancel_button = box.addButton("Non : retour à la fiche", QMessageBox.RejectRole)
        cancel_button.setStyleSheet("background-color: #9aa5b1; color: white;")
        save_button = box.addButton("Non : sauvegarder et fermer la fiche", QMessageBox.ActionRole)
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
        box.setWindowTitle("Génération en cours")
        box.setText(
            "Une génération est en cours. Voulez-vous vraiment quitter ?\n"
            "La fiche en cours de génération sera perdue (le quota Gemini déjà "
            "consommé ne sera pas restitué)."
        )
        quit_button = box.addButton("Oui : quitter", QMessageBox.YesRole)
        quit_button.setStyleSheet(
            "background-color: #d9362e; color: white; font-weight: bold;"
        )
        stay_button = box.addButton("Non : laisser la génération se terminer", QMessageBox.RejectRole)
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

        resume_state = generation_resume.load_resume_state(config.get_settings_dir())
        if resume_state is not None and resume_state.book_path == self.selected_book_path:
            resume_box = QMessageBox(self)
            resume_box.setIcon(QMessageBox.Question)
            resume_box.setWindowTitle("Reprendre la génération interrompue ?")
            resume_box.setText(
                f"Une génération précédente pour ce livre s'était arrêtée après "
                f"{resume_state.batches_done}/{resume_state.batches_total} lot(s) de chapitres "
                f"résumé(s) avec succès.\n\nReprendre à partir de là (recommandé), ou repartir de zéro ?"
            )
            resume_button = resume_box.addButton("Reprendre", QMessageBox.AcceptRole)
            restart_button = resume_box.addButton("Repartir de zéro", QMessageBox.RejectRole)
            resume_box.setDefaultButton(resume_button)
            resume_box.exec_()
            if resume_box.clickedButton() is restart_button:
                generation_resume.clear_resume_state(config.get_settings_dir())
                resume_state = None
        else:
            resume_state = None

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

        self._last_progress_message = "Démarrage du traitement…"
        self._elapsed_seconds = 0
        self.status_label.setStyleSheet("color: #555;")
        self._render_status_with_elapsed()
        self._elapsed_timer.start()

        self.quota_warning_label.hide()
        self.worker = SummarizeWorker(self.selected_book_path, api_key, self.quota_tracker, resume_state)
        self.worker.progress.connect(self._on_progress)
        self.worker.quota_updated.connect(self._update_quota_display)
        self.worker.finished_ok.connect(self._on_finished_ok)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        minutes, seconds = divmod(max(total_seconds, 0), 60)
        if minutes:
            return f"{minutes} min {seconds:02d} s"
        return f"{seconds} s"

    def _render_status_with_elapsed(self) -> None:
        elapsed = self._format_duration(self._elapsed_seconds)
        self.status_label.setText(f"{self._last_progress_message} (écoulé : {elapsed})")

    def _on_elapsed_tick(self) -> None:
        self._elapsed_seconds += 1
        self._render_status_with_elapsed()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._last_progress_message = message
        self._render_status_with_elapsed()

    QUOTA_WARNING_THRESHOLD = 0.8

    def _update_quota_display(self, snapshot: QuotaSnapshot) -> None:
        self.quota_label.setText(
            f"Tokens - entrée : {snapshot.input_tokens_total:,} · "
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
        self.book_title_input.setText("")
        self.book_author_input.setText("")
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
                result.detailed_summary_text or "Aucun résumé détaillé disponible pour cette fiche."
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
                self.cover_label.setText("Pas de couverture")
        else:
            self.cover_label.setText("Pas de couverture")

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
        mode = (
            f"Résumé consolidé à partir de {result.chapter_count} chapitres."
            if result.was_split
            else "Résumé produit en une seule requête."
        )
        duration = self._format_duration(self._elapsed_seconds)
        self.status_label.setStyleSheet("color: #2ea04f; font-weight: bold;")
        self.status_label.setText(f"Terminé en {duration}. {mode}")
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

    def _on_failed(self, error_message: str) -> None:
        self._elapsed_timer.stop()
        duration = self._format_duration(self._elapsed_seconds)
        self.status_label.setStyleSheet("color: #b02a2a; font-weight: bold;")
        self.status_label.setText(f"Échec après {duration}.")
        self._set_summarize_button_enabled(True)
        self.load_report_button.setEnabled(True)
        self.prompts_button.setEnabled(True)
        self.drop_zone.set_busy(False)
        if "quota" in error_message.lower():
            self.quota_warning_label.setText(
                "🚫 Quota Gemini dépassé, l'appli ne peut plus faire de requêtes pour l'instant. "
                "Réessayez plus tard (le quota par minute se réinitialise en 60 s, "
                "le quota journalier à minuit)."
            )
            self.quota_warning_label.show()
        resume_state = generation_resume.load_resume_state(config.get_settings_dir())
        if resume_state is not None and resume_state.book_path == self.selected_book_path:
            if "quota journalier" in error_message.lower():
                wait_hint = (
                    "C'est le quota JOURNALIER (nombre de requêtes/jour) qui est épuisé : il ne se "
                    "réinitialise qu'à minuit, il faudra donc attendre demain avant de pouvoir reprendre. "
                    "Une fois ce délai passé, "
                )
            elif "quota" in error_message.lower():
                wait_hint = (
                    "C'est le quota PAR MINUTE (requêtes ou tokens/minute) qui est temporairement dépassé : "
                    "il se libère de lui-même en général en moins de 2 minutes. Une fois ce délai passé, "
                )
            else:
                # Pas un problème de quota (ex : réponse mal formée renvoyée par Gemini,
                # aléa ponctuel) : aucun délai à attendre, un nouvel essai peut suffire
                # immédiatement, contrairement aux cas de quota traités ci-dessus.
                wait_hint = (
                    "Ce n'est pas un problème de quota : rien à attendre, il s'agit probablement d'un "
                    "aléa ponctuel de génération. Vous pouvez réessayer tout de suite, "
                )
            error_message += (
                f"\n\nRien n'est perdu : les {resume_state.batches_done}/{resume_state.batches_total} "
                f"lot(s) de chapitres déjà résumés avec succès ont été sauvegardés. {wait_hint}"
                "glissez-déposez à nouveau ce même fichier EPUB/PDF et cliquez sur \"Résumer\" : "
                "Distillat reconnaîtra automatiquement où le traitement s'était arrêté et proposera de "
                "reprendre exactement à partir de là, sans refaire le travail déjà fait."
            )
        QMessageBox.critical(self, "Erreur", error_message)

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
            self, "Enregistrer le résumé", default_path, "Documents PDF (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            export_book_report_to_pdf(self.last_result, path)
            config.save_last_pdf_dir(Path(path).parent)
            QMessageBox.information(self, "Sauvegarde réussie", f"Document enregistré :\n{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erreur de sauvegarde", str(exc))

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
            "Enregistrer la fiche",
            default_path,
            "Fiches Distillat (*.json)",
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
            box.setWindowTitle("Fichier existant")
            box.setText(f"Le fichier existe déjà :\n{path}\n\nVoulez-vous le remplacer ?")
            yes_button = box.addButton("Oui", QMessageBox.YesRole)
            no_button = box.addButton("Non", QMessageBox.NoRole)
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
                    self, "Sauvegarde réussie", "La fiche a été correctement modifiée."
                )
            else:
                QMessageBox.information(self, "Sauvegarde réussie", f"Fiche enregistrée :\n{path}")
            return True
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erreur de sauvegarde", str(exc))
            return False

    def _on_load_report_clicked(self) -> None:
        if not self._confirm_discard_unsaved_report():
            return
        default_dir = config.load_last_report_dir() or config.get_reports_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger une fiche", str(default_dir), "Fiches Distillat (*.json)"
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
            QMessageBox.critical(self, "Erreur de chargement", f"Fichier illisible : {exc}")
            return

        self.last_result = result
        self._last_result_source_stem = Path(path).stem.removesuffix(".distillat")
        self._last_report_source_path = Path(path)
        self._report_dirty = False
        config.save_last_report_dir(self._last_report_source_path.parent)
        self._display_book_report(result)
        self.status_label.setText(f"Fiche chargée depuis {os.path.basename(path)}.")
        self.extra_text_label.hide()
        self.save_button.setEnabled(True)
        self.save_report_button.setEnabled(True)
        self.close_report_button.setEnabled(True)
