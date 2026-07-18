"""Extraction du texte d'un fichier PDF."""
from pathlib import Path

from pypdf import PdfReader

from app.epub_parser import Chapter, BookContent

PAGES_PER_CHAPTER = 20


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
    )
