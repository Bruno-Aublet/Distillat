"""Extraction du texte d'un fichier PDF."""
import io
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader

from app.cover_image import shrink_cover_image
from app.epub_parser import Chapter, BookContent

PAGES_PER_CHAPTER = 20

# Largeur de rendu de la couverture : un peu au-dessus du MAX_WIDTH_PX de
# shrink_cover_image, qui la ramènera à sa taille de stockage définitive.
_COVER_RENDER_WIDTH_PX = 900


def extract_pdf_cover(file_path: str) -> bytes | None:
    """Rend la première page du PDF en image (via pypdfium2) pour servir de
    couverture, telle qu'elle s'afficherait à l'écran : fonctionne quel que
    soit son contenu (image pleine page, composition de plusieurs images,
    page de titre en texte...)."""
    document = None
    try:
        document = pdfium.PdfDocument(file_path)
        page = document[0]
        page_width, _ = page.get_size()
        scale = _COVER_RENDER_WIDTH_PX / page_width
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        output = io.BytesIO()
        pil_image.convert("RGB").save(output, format="JPEG", quality=90)
        return shrink_cover_image(output.getvalue())
    except Exception:  # noqa: BLE001 - pas de couverture ne doit pas bloquer le parsing
        return None
    finally:
        # Sans fermeture explicite, pypdfium2 garde un verrou sur le fichier
        # (l'utilisateur ne pourrait plus le déplacer/supprimer tant que
        # l'application est ouverte).
        if document is not None:
            try:
                document.close()
            except Exception:  # noqa: BLE001
                pass


def parse_pdf(file_path: str) -> BookContent:
    """Extrait le texte d'un PDF page par page. Les PDF n'ayant pas de structure
    de chapitres fiable, le contenu est découpé en blocs de PAGES_PER_CHAPTER
    pages pour permettre le même traitement par lots que pour un EPUB volumineux."""
    reader = PdfReader(file_path)

    pages_text = [page.extract_text() or "" for page in reader.pages]
    pages_text = [t for t in pages_text if t.strip()]

    if not pages_text:
        raise ValueError(
            "Aucun texte n'a pu être extrait de ce PDF (il s'agit peut-être d'un scan sans OCR)."
        )

    chapters: list[Chapter] = []
    for start in range(0, len(pages_text), PAGES_PER_CHAPTER):
        block = pages_text[start : start + PAGES_PER_CHAPTER]
        first_page = start + 1
        last_page = start + len(block)
        title = f"Pages {first_page}-{last_page}" if len(block) > 1 else f"Page {first_page}"
        chapters.append(Chapter(title=title, text="\n\n".join(block)))

    full_text = "\n\n".join(f"## {chapter.title}\n\n{chapter.text}" for chapter in chapters)

    metadata = reader.metadata
    book_title = (metadata.title if metadata and metadata.title else None) or Path(file_path).stem
    author = (metadata.author if metadata and metadata.author else None) or "Auteur inconnu"

    return BookContent(
        book_title=book_title,
        author=author,
        full_text=full_text,
        chapters=chapters,
        cover_image=extract_pdf_cover(file_path),
    )
