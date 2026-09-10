"""Filigrane : poser un logo semi-transparent sur le clip.

Module PUR : il ne fait que construire les morceaux de filtre ffmpeg et
resoudre le chemin de l'image. Aucun appel a ffmpeg ici, donc tout est
verifiable sans encoder quoi que ce soit.

Deux choix de valeurs par defaut, expliques parce qu'ils ne sont pas evidents :

- EN BAS AU CENTRE, sous les sous-titres. C'est l'emplacement demande, et il
  tient : les sous-titres sont incrustes entre 300 et 460 pixels du bas selon
  le style, le logo occupe la bande en dessous. La colonne d'icones de TikTok
  et d'Instagram est a DROITE et la legende a GAUCHE : le centre bas est la
  seule zone basse que leur interface laisse libre. `reserved_bottom_px()`
  existe pour que les sous-titres ne puissent pas redescendre dessus.
- LA TAILLE EST UN POURCENTAGE de la largeur de sortie, jamais un nombre de
  pixels. Le meme logo doit peser pareil a l'oeil en 1080x1920 et en 1920x1080 ;
  une taille fixe serait deux fois trop grosse sur l'un des deux.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "branding/watermark.png"
# Voice Studio pose un AUTRE logo que le rendu des clips : les deux ne
# publient pas sous le meme nom. Meme forme (un disque detoure, fond
# transparent) donc mêmes reglages de taille, de marge et d'opacite -- seule
# l'image change.
VOICE_STUDIO_IMAGE = "branding/watermark-landscapesfr.png"
# Ce logo-la porte du TEXTE (un nom sur l'anneau, une baseline en bas). A 14 %
# de la largeur, comme la marque des clips, ce texte n'est plus lisible : un
# ecusson detaille a besoin de plus de place et de plus d'opacite qu'un
# pictogramme. D'ou deux valeurs propres a Voice Studio, remplacables dans
# config/editing.json.
VOICE_STUDIO_SIZE_PERCENT = 18.0
VOICE_STUDIO_OPACITY = 0.85

POSITIONS = ("haut-gauche", "haut-centre", "haut-droite",
             "bas-gauche", "bas-centre", "bas-droite")
DEFAULT_POSITION = "bas-centre"
DEFAULT_SIZE_PERCENT = 14.0
DEFAULT_OPACITY = 0.70
# La marge est une distance au bord le plus proche, en pourcentage de la
# LARGEUR de sortie. 12 % de 1080 px placent le logo a 130 px du bas, donc
# sous les sous-titres et au-dessus de la barre de l'application.
DEFAULT_MARGIN_PERCENT = 12.0

# Bornes de bon sens. Un filigrane a 100 % d'opacite couvre l'image, et a 1 %
# il n'existe pas : dans les deux cas l'utilisateur croirait a un bug.
MIN_OPACITY, MAX_OPACITY = 0.05, 1.0
MIN_SIZE_PERCENT, MAX_SIZE_PERCENT = 2.0, 40.0


@dataclass(frozen=True)
class Watermark:
    """Un filigrane pret a etre pose."""

    image: str
    size_percent: float = DEFAULT_SIZE_PERCENT
    opacity: float = DEFAULT_OPACITY
    position: str = DEFAULT_POSITION
    margin_percent: float = DEFAULT_MARGIN_PERCENT

    @property
    def exists(self) -> bool:
        return bool(self.image) and Path(self.image).is_file()


def asset_path(name: str) -> Path:
    """Chemin d'un logo livre avec l'application.

    Passe par app_base_dir() et non par un chemin relatif : une fois compile,
    le dossier courant n'est pas celui de l'executable, et un chemin relatif
    marcherait depuis le depot puis echouerait silencieusement dans l'exe.
    """
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / name


def default_image_path() -> Path:
    """Le logo des clips."""
    return asset_path(DEFAULT_IMAGE)


def voice_studio_image_path() -> Path:
    """Le logo des videos narrees de Voice Studio."""
    return asset_path(VOICE_STUDIO_IMAGE)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def from_config(config: dict | None) -> Watermark | None:
    """Filigrane decrit par config/editing.json, ou None s'il est desactive."""
    config = config or {}
    if not config.get("enabled", False):
        return None

    image = str(config.get("image") or "").strip()
    path = Path(image) if image else default_image_path()
    if not path.is_file():
        return None

    return Watermark(
        image=str(path),
        size_percent=_clamp(float(config.get("size_percent", DEFAULT_SIZE_PERCENT)),
                            MIN_SIZE_PERCENT, MAX_SIZE_PERCENT),
        opacity=_clamp(float(config.get("opacity", DEFAULT_OPACITY)),
                       MIN_OPACITY, MAX_OPACITY),
        position=(config.get("position") or DEFAULT_POSITION)
        if (config.get("position") or DEFAULT_POSITION) in POSITIONS else DEFAULT_POSITION,
        margin_percent=_clamp(float(config.get("margin_percent", DEFAULT_MARGIN_PERCENT)),
                              0.0, 20.0),
    )


def prepare_filter(watermark: Watermark, out_w: int) -> str:
    """Filtre appliquer a l'image du logo avant de la poser.

    L'opacite est appliquee en MULTIPLIANT le canal alpha existant, ce qui
    preserve le bord adouci du disque : remplacer l'alpha rendrait le contour
    net et carrerait le logo.
    """
    width = max(2, int(round(out_w * watermark.size_percent / 100.0)))
    width -= width % 2
    return (f"scale={width}:-1,format=rgba,"
            f"colorchannelmixer=aa={watermark.opacity:.3f}")


def overlay_position(watermark: Watermark, out_w: int, out_h: int | None = None) -> str:
    """Coordonnees de l'incrustation, en expressions ffmpeg.

    W et H sont la taille du fond, w et h celle du logo : la marge reste juste
    quelle que soit la definition de sortie, et le centrage aussi.

    La marge se calcule sur le PLUS PETIT des deux cotes. Calculee sur la
    largeur seule -- ce qui etait le cas -- elle vaut 12 % de 1920 en 16:9,
    soit 230 pixels : un cinquieme d'une image haute de 1080, et le logo
    flottait au milieu du cadre au lieu d'etre en bas. Calculee sur la hauteur
    seule, c'est le format 9:16 qui se retrouve avec un logo trop remonte. Le
    plus petit cote donne le meme ecart a l'oeil dans les deux formats.
    """
    reference = min(out_w, out_h or out_w)
    margin = max(0, int(round(reference * watermark.margin_percent / 100.0)))
    margin_v = margin
    horizontal = {"gauche": f"{margin}", "centre": "(W-w)/2", "droite": f"W-w-{margin}"}
    vertical = {"haut": f"{margin_v}", "bas": f"H-h-{margin_v}"}
    position = watermark.position if watermark.position in POSITIONS else DEFAULT_POSITION
    band, side = position.split("-")
    return f"{horizontal[side]}:{vertical[band]}"


def logo_height_px(watermark: Watermark, out_w: int) -> int:
    """Hauteur du logo une fois pose, en pixels.

    Le filtre le met a l'echelle sur sa largeur (`scale=w:-1`) : la hauteur
    depend donc des proportions de l'image. On les lit vraiment plutot que de
    supposer un carre -- une image large donnerait sinon une reserve trop
    grande, et une image haute une reserve trop petite, ce qui laisserait les
    sous-titres retomber dessus.
    """
    width = max(2, int(round(out_w * watermark.size_percent / 100.0)))
    ratio = 1.0
    try:
        from PIL import Image

        with Image.open(watermark.image) as image:
            if image.width:
                ratio = image.height / image.width
    except Exception:
        ratio = 1.0                      # image illisible : on suppose un carre
    return max(2, int(round(width * ratio)))


def reserved_bottom_px(watermark: Watermark | None, out_w: int, gap_px: int = 24,
                       out_h: int | None = None) -> int:
    """Hauteur de la bande basse occupee par le logo, sous-titres exclus.

    Sert a empecher les sous-titres de redescendre sur le logo : le placement
    intelligent peut les rapprocher du bas quand un visage occupe le cadre, et
    il n'a aucune raison de savoir qu'un logo est pose la. Zero si le logo
    n'est pas en bas -- il n'y a alors rien a reserver.
    """
    if watermark is None or not watermark.position.startswith("bas-"):
        return 0
    reference = min(out_w, out_h or out_w)
    margin = max(0, int(round(reference * watermark.margin_percent / 100.0)))
    return margin + logo_height_px(watermark, out_w) + max(0, gap_px)
