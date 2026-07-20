"""Extraction du texte et de la table des matières d'un fichier EPUB."""
from dataclasses import dataclass, field

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from app.cover_image import shrink_cover_image
from app.i18n import tr


@dataclass
class Chapter:
    title: str
    text: str = field(default="", repr=False)


@dataclass
class BookContent:
    book_title: str
    author: str
    full_text: str
    chapters: list[Chapter]
    cover_image: bytes | None = None


def _html_to_text(html_content: bytes | str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _get_title(book: epub.EpubBook) -> str:
    title = book.get_metadata("DC", "title")
    if title:
        return title[0][0]
    return tr("book_parsers.unknown_title")


def _get_author(book: epub.EpubBook) -> str:
    author = book.get_metadata("DC", "creator")
    if author:
        return author[0][0]
    return tr("book_parsers.unknown_author")


def _find_cover_image_bytes(book: epub.EpubBook) -> bytes | None:
    """Cherche l'image de couverture : d'abord via le type ITEM_COVER, puis via
    la métadonnée <meta name="cover">, puis par convention de nom (fallbacks
    nécessaires car de nombreux EPUB ne taguent pas proprement leur couverture)."""
    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        return item.get_content()

    for name, value in book.get_metadata("OPF", "cover"):
        cover_id = value.get("content") if isinstance(value, dict) else None
        if cover_id:
            item = book.get_item_with_id(cover_id)
            if item is not None:
                return item.get_content()

    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if "cover" in item.get_name().lower() or "cover" in (item.get_id() or "").lower():
            return item.get_content()

    return None


def _get_cover_image(book: epub.EpubBook) -> bytes | None:
    raw_bytes = _find_cover_image_bytes(book)
    return shrink_cover_image(raw_bytes) if raw_bytes else None


def _build_toc_map(book: epub.EpubBook) -> dict[str, str]:
    """Associe le nom de fichier (href) au titre donné par la table des matières."""
    toc_map: dict[str, str] = {}

    def walk(items):
        for item in items:
            if isinstance(item, tuple):
                # (Section, [children]) ou (Link, [children])
                link_or_section, children = item
                if hasattr(link_or_section, "href"):
                    href = link_or_section.href.split("#")[0]
                    toc_map.setdefault(href, link_or_section.title)
                walk(children)
            elif isinstance(item, epub.Link):
                href = item.href.split("#")[0]
                toc_map.setdefault(href, item.title)

    walk(book.toc)
    return toc_map


def parse_epub(file_path: str) -> BookContent:
    """Parcourt l'EPUB dans l'ordre de lecture (spine) et découpe par chapitre
    en utilisant la table des matières quand elle est disponible."""
    book = epub.read_epub(file_path, options={"ignore_ncx": False})

    toc_map = _build_toc_map(book)

    doc_items = {item.get_name(): item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}

    chapters: list[Chapter] = []
    full_text_parts: list[str] = []
    fallback_index = 1

    for spine_id, _ in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if isinstance(item, epub.EpubNav) or "nav" in (item.properties or []):
            continue

        text = _html_to_text(item.get_content())
        if not text.strip():
            continue

        name = item.get_name()
        title = toc_map.get(name)
        if not title:
            title = tr("epub_parser.fallback_chapter_title", index=fallback_index)
            fallback_index += 1

        chapters.append(Chapter(title=title, text=text))
        full_text_parts.append(f"## {title}\n\n{text}")

    if not chapters:
        raise ValueError(tr("epub_parser.no_text_extracted"))

    return BookContent(
        book_title=_get_title(book),
        author=_get_author(book),
        full_text="\n\n".join(full_text_parts),
        chapters=chapters,
        cover_image=_get_cover_image(book),
    )
