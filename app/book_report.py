"""Structure unifiée d'une fiche de livre (résumé, personnages, analyse) et
sa sérialisation dans un fichier JSON unique, autonome et rechargeable."""
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.cover_image import shrink_cover_image

FILE_FORMAT_VERSION = 2


@dataclass
class Character:
    name: str
    description: str


@dataclass
class BookReport:
    book_title: str
    author: str
    summary_text: str
    detailed_summary_text: str = ""
    characters: list[Character] = field(default_factory=list)
    analysis_text: str = ""
    cover_image: bytes | None = None
    was_split: bool = False
    chapter_count: int = 1

    def to_json(self) -> str:
        data = {
            "format_version": FILE_FORMAT_VERSION,
            "book_title": self.book_title,
            "author": self.author,
            "summary_text": self.summary_text,
            "detailed_summary_text": self.detailed_summary_text,
            "characters": [{"name": c.name, "description": c.description} for c in self.characters],
            "analysis_text": self.analysis_text,
            "cover_image_base64": (
                base64.b64encode(self.cover_image).decode("ascii") if self.cover_image else None
            ),
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(raw: str) -> "BookReport":
        report, _ = BookReport._from_json_with_shrink_flag(raw)
        return report

    @staticmethod
    def _from_json_with_shrink_flag(raw: str) -> tuple["BookReport", bool]:
        data = json.loads(raw)
        cover_b64 = data.get("cover_image_base64")
        cover_image = base64.b64decode(cover_b64) if cover_b64 else None
        cover_was_shrunk = False
        if cover_image:
            # Remet aux normes actuelles une couverture provenant d'une fiche
            # plus ancienne ou générée avant l'introduction de la réduction
            # automatique (pas d'effet si elle est déjà assez légère).
            shrunk = shrink_cover_image(cover_image)
            cover_was_shrunk = shrunk != cover_image
            cover_image = shrunk
        report = BookReport(
            book_title=data["book_title"],
            author=data["author"],
            summary_text=data["summary_text"],
            # absent des fiches sauvegardées avant l'ajout du résumé détaillé (format v1)
            detailed_summary_text=data.get("detailed_summary_text", ""),
            characters=[
                Character(name=c["name"], description=c["description"])
                for c in data.get("characters", [])
            ],
            analysis_text=data.get("analysis_text", ""),
            cover_image=cover_image,
        )
        return report, cover_was_shrunk

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "BookReport":
        report, cover_was_shrunk = BookReport._from_json_with_shrink_flag(path.read_text(encoding="utf-8"))
        if cover_was_shrunk:
            # La fiche sur disque contenait une couverture surdimensionnée :
            # on la réécrit immédiatement avec la version allégée, pour que
            # le gain de place profite aussi au fichier, pas seulement à la
            # session en mémoire.
            try:
                report.save(path)
            except OSError:
                pass
        return report

    def suggested_filename(self, source_stem: str | None = None) -> str:
        """Nom de fichier suggéré pour la sauvegarde JSON. Basé sur le nom du
        fichier source (EPUB/PDF, sans extension) quand il est connu, sinon
        replié sur le titre du livre (ex: fiche rechargée depuis un JSON)."""
        base = source_stem or self.book_title
        safe_base = "".join(c for c in base if c.isalnum() or c in " -_").strip()
        return f"{safe_base or 'livre'}.distillat.json"
