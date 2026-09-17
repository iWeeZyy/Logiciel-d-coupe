"""Video d'introduction posee au DEBUT de chaque clip produit.

Ce n'est PAS une incrustation flottante par-dessus le clip : la video fournie
("Follow" anime, plein cadre, fond noir) n'a aucun sens posee en transparence
sur le contenu du streamer, elle doit etre VUE en entier avant que le clip ne
commence. C'est donc un second clip, concatene devant le premier -- voir
video/intro_concat.py pour la construction du graphe ffmpeg qui fait ce
raccord.

Module PUR comme watermark.py : resout le chemin de la video et rien
d'autre. Aucun appel a ffmpeg ici.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_VIDEO = "branding/intro_follow.mp4"


@dataclass(frozen=True)
class Intro:
    """Une video d'intro prete a etre concatenee."""

    video: str

    @property
    def exists(self) -> bool:
        return bool(self.video) and Path(self.video).is_file()


def asset_path(name: str) -> Path:
    """Chemin d'une video livree avec l'application.

    Meme raisonnement que watermark.asset_path() : passe par app_base_dir()
    et non par un chemin relatif, pour rester valable une fois compile.
    """
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / name


def default_video_path() -> Path:
    return asset_path(DEFAULT_VIDEO)


def from_config(config: dict | None) -> Intro | None:
    """Intro decrite par config/editing.json, ou None si desactivee ou
    introuvable.

    Une video absente du disque (mauvais chemin dans une config personnalisee,
    ou asset non livre avec une installation ancienne) ne doit pas faire
    echouer le clip : elle rend simplement None, et le clip sort sans intro --
    exactement le comportement d'un filigrane dont l'image manquerait.
    """
    config = config or {}
    if not config.get("enabled", False):
        return None

    video = str(config.get("video") or "").strip()
    path = Path(video) if video else default_video_path()
    if not path.is_file():
        return None

    return Intro(video=str(path))
