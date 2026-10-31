"""Export du post video 9:16 d'une bande-annonce, sans interface.

Le corps du bouton « Exporter » de « Créer un visuel » (modele video) :
telechargement (yt-dlp, comme le reste de l'appli), sous-titres francais si
la video est en VO (news_story/vost.py, Claude), puis montage
(news_story/video_composer.compose_video). Partage par la fenetre
(gui/radar/story_dialog.py) et la bande-annonce automatique
(news_story/auto_trailer.py) : une seule facon de faire la video.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


class VostUnavailable(Exception):
    """Les sous-titres francais ont ete demandes mais n'ont pas pu etre faits."""


class LowQualitySource(Exception):
    """La video telechargee est trop petite pour un post 1080x1920 net."""


def export_video(video_url: str, out_path: str, *, title: str, label: str, logo_path,
                 text_opacity: float, title_scale: float, subtitle: str = "", cta: str = "",
                 french_subtitles: bool = False, intro: bool = False,
                 on_status=lambda message: None, cancel_token=None,
                 min_height: int = 0) -> str:
    """Ecrit la video dans `out_path` et renvoie ce chemin. Leve
    VostUnavailable si les sous-titres demandes echouent (la fenetre propose
    alors de decocher l'option), CancelledError si annule. `min_height` :
    hauteur minimale de la source (0 = aucune) -- LowQualitySource sinon ; la
    bande-annonce automatique refuse une source floue plutot que de la
    poster."""
    from news_story.video_composer import compose_video
    from youtube.downloader import download_video

    with tempfile.TemporaryDirectory(prefix="clipfarming_article_video_") as workdir:
        on_status("Téléchargement de la vidéo…")

        def progress(fraction, done_mb, total_mb):
            if fraction is not None:
                on_status(f"Téléchargement de la vidéo… {int(fraction * 100)} %")

        source = download_video(video_url, workdir, consent_confirmed=True, max_height=1080,
                                on_progress=progress, cancel_token=cancel_token)
        if min_height:
            from video.ffmpeg_utils import video_resolution

            _width, height = video_resolution(source)
            if height < min_height:
                raise LowQualitySource(f"vidéo disponible seulement en {height}p "
                                       f"(minimum {min_height}p)")
        cues = None
        if french_subtitles:
            from news_story.vost import VostError, french_cues

            try:
                cues = french_cues(source, on_status=on_status, cancel_token=cancel_token)
            except VostError as error:
                raise VostUnavailable(str(error)) from error
        on_status("Montage de la vidéo 9:16… (cela peut prendre une minute)")
        compose_video(source, out_path, title=title, label=label,
                      logo_path=Path(logo_path) if logo_path else None,
                      text_opacity=text_opacity, title_scale=title_scale,
                      subtitle=subtitle, cta=cta, cancel_token=cancel_token,
                      french_subtitles=cues, intro=intro)
    return str(out_path)
