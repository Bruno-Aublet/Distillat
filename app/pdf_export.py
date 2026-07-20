"""Export de la fiche de livre en PDF via ReportLab (pur pip, aucune
dépendance système - contrairement à WeasyPrint qui exige Pango/GTK).

Dépendance : pip install reportlab
"""
import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from xml.sax.saxutils import escape

from app.book_report import BookReport
from app.i18n import tr

INK = colors.HexColor("#201E1C")
INK_MUTED = colors.HexColor("#6E6A66")
NAVY = colors.HexColor("#16283F")
GOLD = colors.HexColor("#B8863B")
CREAM = colors.HexColor("#F4EFE6")
HAIRLINE = colors.HexColor("#E1DAD0")

MARGIN_LEFT = 3 * cm
MARGIN_RIGHT = 3 * cm
MARGIN_TOP = 2.3 * cm
MARGIN_BOTTOM = 2.6 * cm
CONTENT_WIDTH = A4[0] - MARGIN_LEFT - MARGIN_RIGHT
# Largeur réelle du texte : le Frame de SimpleDocTemplate ajoute 6pt de
# padding interne de chaque côté.
TEXT_WIDTH = CONTENT_WIDTH - 12


def _register_fonts() -> tuple[str, str, str, str]:
    """Tente d'enregistrer Georgia depuis les emplacements habituels de l'OS ;
    retombe sur Times (embarqué dans ReportLab) sinon.
    Retourne (normal, gras, italique, gras-italique)."""
    candidates = {
        "georgia.ttf": ("Georgia", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    }
    search_dirs = [
        "C:/Windows/Fonts",
        "/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        os.path.expanduser("~/Library/Fonts"),
        "/usr/share/fonts/truetype/msttcorefonts",
    ]
    for regular, (family, bold, italic, bold_italic) in candidates.items():
        for directory in search_dirs:
            path = os.path.join(directory, regular)
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont(family, path))
                    names = [family, family, family, family]
                    for suffix, filename, index in (
                        ("-Bold", bold, 1),
                        ("-Italic", italic, 2),
                        ("-BoldItalic", bold_italic, 3),
                    ):
                        variant_path = os.path.join(directory, filename)
                        if os.path.exists(variant_path):
                            pdfmetrics.registerFont(TTFont(family + suffix, variant_path))
                            names[index] = family + suffix
                    pdfmetrics.registerFontFamily(
                        family, normal=names[0], bold=names[1],
                        italic=names[2], boldItalic=names[3],
                    )
                    return tuple(names)
                except Exception:  # noqa: BLE001
                    break
    return ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic")


FONT, FONT_BOLD, FONT_ITALIC, FONT_BOLD_ITALIC = _register_fonts()


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName=FONT, fontSize=11, leading=11 * 1.35, textColor=INK)
    return {
        "body": ParagraphStyle("body", alignment=TA_JUSTIFY, spaceAfter=8, **base),
        "body_indent": ParagraphStyle(
            "body_indent", alignment=TA_JUSTIFY, spaceAfter=8, firstLineIndent=0.5 * cm, **base
        ),
        "title": ParagraphStyle(
            "title", fontName=FONT_BOLD, fontSize=26, leading=30,
            alignment=TA_CENTER, textColor=INK, spaceAfter=6,
        ),
        "author": ParagraphStyle(
            "author", fontName=FONT_ITALIC, fontSize=13, leading=16,
            alignment=TA_CENTER, textColor=GOLD, spaceAfter=12,
        ),
        "tag": ParagraphStyle(
            "tag", fontName=FONT_BOLD, fontSize=11, leading=14,
            textColor=CREAM, alignment=TA_LEFT,
        ),
        "h3": ParagraphStyle(
            "h3", fontName=FONT_BOLD_ITALIC, fontSize=12.5, leading=15,
            textColor=NAVY, spaceBefore=12, spaceAfter=5, keepWithNext=True,
        ),
        "h4": ParagraphStyle(
            "h4", fontName=FONT_BOLD_ITALIC, fontSize=12, leading=14,
            textColor=NAVY, spaceBefore=10, spaceAfter=4, keepWithNext=True,
        ),
        "char_name": ParagraphStyle(
            "char_name", fontName=FONT_BOLD, fontSize=12.5, leading=15,
            textColor=NAVY, spaceAfter=3,
        ),
        "char_body": ParagraphStyle(
            "char_body", fontName=FONT, fontSize=10.5, leading=10.5 * 1.3,
            textColor=INK, alignment=TA_JUSTIFY,
        ),
        "muted": ParagraphStyle(
            "muted", fontName=FONT_ITALIC, fontSize=11, leading=15, textColor=INK_MUTED
        ),
        "dropcap": ParagraphStyle(
            "dropcap", fontName=FONT_BOLD, fontSize=30, leading=30, textColor=NAVY
        ),
    }


STYLES = _styles()


class _TagHeading(Flowable):
    """Bandeau bleu marine ajusté à la largeur du texte, avec l'espacement
    de lettres dessiné directement (Paragraph ne gère ni l'un ni l'autre :
    le tableau prenait toute la largeur et l'espace fine U+2009 n'existe pas
    dans toutes les polices, d'où les mots collés)."""

    CHAR_SPACE = 1.5
    PAD_X = 10
    HEIGHT = 24

    def __init__(self, text: str):
        super().__init__()
        self.text = text.upper()
        text_width = pdfmetrics.stringWidth(self.text, FONT_BOLD, 11)
        self.width = text_width + self.CHAR_SPACE * len(self.text) + 2 * self.PAD_X
        self.height = self.HEIGHT

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        self.canv.setFillColor(NAVY)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        self.canv.setFillColor(CREAM)
        self.canv.setFont(FONT_BOLD, 11)
        # Espacement de lettres dessiné caractère par caractère (setCharSpace
        # n'existe pas sur toutes les versions de ReportLab).
        x = self.PAD_X
        for char in self.text:
            self.canv.drawString(x, 7.5, char)
            x += pdfmetrics.stringWidth(char, FONT_BOLD, 11) + self.CHAR_SPACE


def _tag_heading(title: str) -> list:
    rule = HRFlowable(width="100%", thickness=2, color=GOLD, spaceBefore=5, spaceAfter=12)
    return [_TagHeading(title), rule]


def _split_for_lines(text: str, available_width: float, max_lines: int) -> tuple[str, str]:
    """Découpe `text` au mot près : la 1re partie tient dans `max_lines`
    lignes de largeur `available_width` (police du corps de texte)."""
    words = text.split()
    space_width = pdfmetrics.stringWidth(" ", FONT, 11)
    line = 0
    line_width = 0.0
    for index, word in enumerate(words):
        word_width = pdfmetrics.stringWidth(word, FONT, 11)
        needed = word_width if line_width == 0 else line_width + space_width + word_width
        if needed > available_width:
            line += 1
            if line >= max_lines:
                return " ".join(words[:index]), " ".join(words[index:])
            line_width = word_width
        else:
            line_width = needed
    return text, ""


class _DropCapBlock(Flowable):
    """Lettrine + les deux premières lignes du paragraphe, dessinées
    entièrement à la main : la justification est calculée mot à mot sur la
    largeur réelle, ce que le découpage approximatif + re-césure de Paragraph
    ne garantissait pas (mots débordant dans la marge, 2e ligne trop courte)."""

    GAP = 9  # espace entre la lettre et le texte

    def __init__(self, letter: str, line1: list, line2: list, justify_last: bool):
        super().__init__()
        self.letter = letter
        self.lines = [line1, line2]
        self.justify_last = justify_last
        self.leading = STYLES["body"].leading
        self.height = 2 * self.leading
        self.width = TEXT_WIDTH
        self.text_x = pdfmetrics.stringWidth(letter, FONT_BOLD, 33) + self.GAP

    def wrap(self, available_width, available_height):
        # Se caler sur la largeur réelle du cadre (justification exacte).
        self.width = available_width
        return self.width, self.height

    def _draw_line(self, words: list, y: float, justify: bool) -> None:
        available = self.width - self.text_x
        space_width = pdfmetrics.stringWidth(" ", FONT, 11)
        widths = [pdfmetrics.stringWidth(word, FONT, 11) for word in words]
        if justify and len(words) > 1:
            gap = (available - sum(widths)) / (len(words) - 1)
        else:
            gap = space_width
        x = self.text_x
        for word, word_width in zip(words, widths):
            self.canv.drawString(x, y, word)
            x += word_width + gap

    def draw(self):
        self.canv.setFillColor(NAVY)
        self.canv.setFont(FONT_BOLD, 33)
        self.canv.drawString(0, 4, self.letter)
        self.canv.setFillColor(INK)
        self.canv.setFont(FONT, 11)
        baseline1 = self.height - 11
        self._draw_line(self.lines[0], baseline1, justify=True)
        if self.lines[1]:
            self._draw_line(self.lines[1], baseline1 - self.leading, justify=self.justify_last)


def _dropcap_flowables(text: str) -> list:
    """Lettrine : la grande lettre et les 2 premières lignes dessinées à la
    main, le reste du paragraphe reprend pleine largeur."""
    letter = text[0]
    text_x = pdfmetrics.stringWidth(letter, FONT_BOLD, 33) + _DropCapBlock.GAP
    available = TEXT_WIDTH - text_x
    line1_text, rest = _split_for_lines(text[1:].strip(), available, 1)
    line2_text, below = _split_for_lines(rest, available, 1) if rest else ("", "")
    block = _DropCapBlock(
        letter, line1_text.split(), line2_text.split(), justify_last=bool(below)
    )
    flowables = [block, Spacer(1, 3)]
    if below:
        flowables.append(Paragraph(escape(below), STYLES["body"]))
    else:
        flowables.append(Spacer(1, 5))
    return flowables


def _body_flowables(text: str) -> list:
    flowables = []
    lead_used = False
    just_after_heading = True
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("### "):
            flowables.append(Paragraph(escape(line[4:].strip()), STYLES["h4"]))
            just_after_heading = True
        elif line.startswith("## ") or line.startswith("# "):
            content = line[3:].strip() if line.startswith("## ") else line[2:].strip()
            flowables.append(Paragraph(escape(content), STYLES["h3"]))
            just_after_heading = True
        else:
            if not lead_used and len(line) > 1:
                lead_used = True
                flowables.extend(_dropcap_flowables(line))
            else:
                flowables.append(Paragraph(escape(line), STYLES["body_indent"]))
            just_after_heading = False
    return flowables


def _character_flowables(result: BookReport) -> list:
    if not result.characters:
        return [Paragraph(tr("main_window.no_characters"), STYLES["muted"])]
    flowables = []
    for index, character in enumerate(result.characters):
        if index > 0:
            flowables.append(
                HRFlowable(width="100%", thickness=0.5, color=HAIRLINE, spaceBefore=2, spaceAfter=10)
            )
        block = Table(
            [[
                Paragraph(escape(character.name), STYLES["char_name"]),
            ], [
                Paragraph(escape(character.description), STYLES["char_body"]),
            ]],
            colWidths=[CONTENT_WIDTH],
            style=TableStyle([
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, GOLD),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 3),
                ("BOTTOMPADDING", (0, 1), (0, 1), 0),
            ]),
            hAlign="LEFT",
        )
        block.keepWithNext = False
        flowables.append(block)
        flowables.append(Spacer(1, 10))
    return flowables


def _footer_factory(book_title: str):
    def _footer(canvas, doc):
        canvas.saveState()
        y = MARGIN_BOTTOM - 0.9 * cm
        canvas.setStrokeColor(HAIRLINE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_LEFT, y + 12, A4[0] - MARGIN_RIGHT, y + 12)
        canvas.setFont(FONT_ITALIC, 8.5)
        canvas.setFillColor(INK_MUTED)
        canvas.drawString(MARGIN_LEFT, y, book_title)
        canvas.setFont(FONT, 8.5)
        canvas.drawRightString(A4[0] - MARGIN_RIGHT, y, str(canvas.getPageNumber()))
        canvas.restoreState()
    return _footer


def export_book_report_to_pdf(result: BookReport, output_path: str) -> None:
    document = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN_LEFT,
        rightMargin=MARGIN_RIGHT,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=result.book_title,
        author=result.author,
    )

    story = []

    if result.cover_image:
        try:
            image = RLImage(io.BytesIO(result.cover_image))
            ratio = image.imageHeight / image.imageWidth
            image.drawWidth = 6.2 * cm
            image.drawHeight = 6.2 * cm * ratio
            framed = Table(
                [[image]],
                style=TableStyle([
                    ("BOX", (0, 0), (0, 0), 2, GOLD),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]),
                hAlign="CENTER",
            )
            story.append(framed)
            story.append(Spacer(1, 16))
        except Exception:  # noqa: BLE001 - une couverture illisible ne bloque pas l'export
            pass

    story.append(Paragraph(escape(result.book_title), STYLES["title"]))
    story.append(Paragraph(escape(result.author), STYLES["author"]))
    story.append(
        HRFlowable(width=2.2 * cm, thickness=2, color=GOLD, spaceBefore=0, spaceAfter=14, hAlign="CENTER")
    )

    story.extend(_tag_heading(tr("pdf_export.summary_heading")))
    story.extend(_body_flowables(result.summary_text))

    if result.detailed_summary_text:
        story.append(PageBreak())
        story.extend(_tag_heading(tr("pdf_export.detailed_summary_heading")))
        story.extend(_body_flowables(result.detailed_summary_text))

    story.append(PageBreak())
    story.extend(_tag_heading(tr("pdf_export.characters_heading")))
    story.extend(_character_flowables(result))

    story.append(PageBreak())
    story.extend(_tag_heading(tr("pdf_export.analysis_heading")))
    story.extend(_body_flowables(result.analysis_text))

    footer = _footer_factory(result.book_title)
    document.build(story, onFirstPage=lambda c, d: None, onLaterPages=footer)
