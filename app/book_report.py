"""Structure unifiée d'une fiche de livre (résumé, personnages, analyse) et
sa sérialisation dans un fichier JSON unique, autonome et rechargeable."""
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.cover_image import shrink_cover_image

FILE_FORMAT_VERSION = 2

# Caractères, en plus des lettres/chiffres, autorisés dans un nom de fichier
# généré automatiquement. Exclut uniquement les caractères réellement
# interdits par Windows (< > : " / \ | ? *), pas la ponctuation courante des
# titres de livres (parenthèses, virgule, point, apostrophe droite ' ou
# typographique '...).
SAFE_FILENAME_EXTRA_CHARS = " -_(),.'’!"

# Noms de fichier réservés par Windows, quelle que soit leur extension.
_RESERVED_WINDOWS_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_filename(base: str, fallback: str = "livre") -> str:
    """Retire d'une chaîne les caractères interdits dans un nom de fichier
    Windows, en conservant la ponctuation courante des titres de livres."""
    safe_base = "".join(c for c in base if c.isalnum() or c in SAFE_FILENAME_EXTRA_CHARS).strip()
    # Windows tronque silencieusement les points/espaces finaux d'un nom de
    # fichier, et interdit certains noms (CON, NUL, COM1...) quelle que soit
    # leur extension.
    safe_base = safe_base.rstrip(" .")
    if not safe_base or safe_base.upper() in _RESERVED_WINDOWS_NAMES:
        return fallback
    return safe_base


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
    # Texte que Gemini a produit en trop après le premier objet JSON exploité
    # (rare, cas d'une réponse mal formée) : conservé en mémoire pour que
    # l'utilisateur puisse le consulter et le récupérer à la main si c'est du
    # contenu légitime, mais jamais persisté (ni JSON, ni export PDF) car il
    # ne fait pas partie du contenu validé de la fiche.
    extra_generated_text: str = ""

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
        data = json.loads(raw)
        cover_b64 = data.get("cover_image_base64")
        cover_image = base64.b64decode(cover_b64) if cover_b64 else None
        if cover_image:
            # Remet aux normes actuelles une couverture provenant d'une fiche
            # plus ancienne ou générée avant l'introduction de la réduction
            # automatique (pas d'effet si elle est déjà assez légère). Reste
            # en mémoire uniquement : ne réécrit pas le fichier source, une
            # simple lecture ne doit pas modifier le fichier sur disque.
            cover_image = shrink_cover_image(cover_image)
        return BookReport(
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

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "BookReport":
        return BookReport.from_json(path.read_text(encoding="utf-8"))

    def suggested_filename(self, source_stem: str | None = None) -> str:
        """Nom de fichier suggéré pour la sauvegarde JSON. Basé sur le nom du
        fichier source (EPUB/PDF, sans extension) quand il est connu, sinon
        replié sur le titre du livre (ex: fiche rechargée depuis un JSON)."""
        base = source_stem or self.book_title
        return f"{sanitize_filename(base)}.distillat.json"
