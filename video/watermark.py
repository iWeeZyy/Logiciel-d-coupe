"""Filigrane : poser un logo semi-transparent sur le clip.

Module PUR : il ne fait que construire les morceaux de filtre ffmpeg et
resoudre le chemin de l'image. Aucun appel a ffmpeg ici, donc tout est
verifiable sans encoder quoi que ce soit.

Deux choix de valeurs par defaut, expliques parce qu'ils ne sont pas evidents :

- EN HAUT ET NON EN BAS. Le bas du cadre est deja occupe : les sous-titres y
  sont incrustes, et les deux plateformes visees y posent leur propre interface
  (legende, boutons). Un logo en bas se retrouve derriere du texte.
- LA TAILLE EST UN POURCENTAGE de la largeur de sortie, jamais un nombre de
  pixels. Le meme logo doit peser pareil a l'oeil en 1080x1920 et en 1920x1080 ;
  une taille fixe serait deux fois trop grosse sur l'un des deux.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "branding/watermark.png"

POSITIONS = ("haut-gauche", "haut-droite", "bas-gauche", "bas-droite")
DEFAULT_POSITION = "haut-droite"
DEFAULT_SIZE_PERCENT = 14.0
DEFAULT_OPACITY = 0.70
DEFAULT_MARGIN_PERCENT = 4.0

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


def default_image_path() -> Path:
    """Le logo livre avec l'application.

    Passe par app_base_dir() et non par un chemin relatif : une fois compile,
    le dossier courant n'est pas celui de l'executable, et un chemin relatif
    marcherait depuis le depot puis echouerait silencieusement dans l'exe.
    """
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / DEFAULT_IMAGE


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


def overlay_position(watermark: Watermark, out_w: int) -> str:
    """Coordonnees de l'incrustation, en expressions ffmpeg.

    W et H sont la taille du fond, w et h celle du logo : la marge reste juste
    quelle que soit la definition de sortie.
    """
    margin = max(0, int(round(out_w * watermark.margin_percent / 100.0)))
    if watermark.position == "haut-gauche":
        return f"{margin}:{margin}"
    if watermark.position == "bas-gauche":
        return f"{margin}:H-h-{margin}"
    if watermark.position == "bas-droite":
        return f"W-w-{margin}:H-h-{margin}"
    return f"W-w-{margin}:{margin}"      # haut-droite, le defaut
