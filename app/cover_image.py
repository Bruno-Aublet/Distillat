"""Réduction des images de couverture avant stockage (fiche JSON, export
Word) : les couvertures EPUB peuvent être en haute résolution (plusieurs Mo),
inutilement lourdes pour un affichage à l'écran ou en petit format imprimé."""
import io

from PIL import Image, UnidentifiedImageError

MAX_WIDTH_PX = 600
JPEG_QUALITY = 85


def shrink_cover_image(raw_bytes: bytes) -> bytes:
    """Redimensionne (si nécessaire) et recompresse en JPEG. Retourne les
    bytes d'origine si l'image est illisible, trop grande pour être ouverte
    en sécurité, ou déjà assez petite/légère."""
    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
    # UnidentifiedImageError : format non reconnu. OSError : fichier tronqué/
    # corrompu (levée par load()). DecompressionBombError : image aux
    # dimensions déclarées excessives (protection PIL contre les "bombes de
    # décompression"). Dans les trois cas la couverture est accessoire : on
    # renvoie les bytes d'origine plutôt que de faire échouer tout l'appelant
    # (le parsing complet du livre, avant ce correctif).
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return raw_bytes

    if image.width > MAX_WIDTH_PX:
        new_height = round(image.height * MAX_WIDTH_PX / image.width)
        image = image.resize((MAX_WIDTH_PX, new_height), Image.LANCZOS)

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    shrunk_bytes = output.getvalue()

    return shrunk_bytes if len(shrunk_bytes) < len(raw_bytes) else raw_bytes
