"""Extraction de miniatures pour la grille de resultats/projets -- une frame
par clip via ffmpeg (reutilise video.ffmpeg_utils, meme point d'appel unique
que le reste du projet). Fait dans un QThread : generer N miniatures en
sequence sur le thread GUI figerait l'affichage de la page Resultats le temps
de tous les extraire."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from utils.errors import FfmpegError
from video.ffmpeg_utils import run_ffmpeg

THUMB_WIDTH = 400


def thumbnail_path_for(clip_mp4_path: str) -> str:
    clip = Path(clip_mp4_path)
    return str(clip.parent / ".thumbs" / f"{clip.stem}.jpg")


def ensure_thumbnail(clip_mp4_path: str) -> str:
    thumb_path = thumbnail_path_for(clip_mp4_path)
    if Path(thumb_path).exists():
        return thumb_path
    Path(thumb_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        run_ffmpeg(
            ["-i", clip_mp4_path, "-vf", f"scale={THUMB_WIDTH}:-1", "-frames:v", "1", thumb_path],
            description="extraction de miniature",
        )
    except FfmpegError:
        return ""  # pas de miniature -- la carte affichera son etat "sans apercu", jamais un plantage
    return thumb_path


class ThumbnailThread(QThread):
    thumbnail_ready = Signal(str, str)  # (clip_mp4_path, thumb_path)

    def __init__(self, clip_paths: list[str], parent=None):
        super().__init__(parent)
        self.clip_paths = clip_paths

    def run(self) -> None:
        for path in self.clip_paths:
            thumb = ensure_thumbnail(path)
            if thumb:
                self.thumbnail_ready.emit(path, thumb)


class RemoteThumbnailThread(QThread):
    """Telecharge les miniatures YouTube (URLs distantes fournies par l'API,
    section 9) hors du thread GUI -- une recherche a 50 resultats ne doit pas
    figer l'affichage le temps de charger 50 images."""

    thumbnail_ready = Signal(str, bytes)  # (video_id, contenu JPEG)

    def __init__(self, url_by_video_id: dict[str, str], parent=None):
        super().__init__(parent)
        self.url_by_video_id = url_by_video_id

    def run(self) -> None:
        import requests

        for video_id, url in self.url_by_video_id.items():
            if not url:
                continue
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    self.thumbnail_ready.emit(video_id, response.content)
            except requests.exceptions.RequestException:
                continue  # une miniature manquante n'est jamais bloquante
