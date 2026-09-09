"""Source YouTube : reconnaitre une URL, puis lire les metadonnees publiques.

Deux responsabilites separees a dessein : reconnaitre une URL est PUR (aucun
reseau, donc testable partout), lire les metadonnees demande yt-dlp et une
connexion. L'interface valide en local avant de tenter quoi que ce soit -- une
faute de frappe ne doit pas partir sur le reseau pour revenir en erreur.

Ce module ne contourne aucune protection : il lit la page publique d'une video
par yt-dlp, la meme bibliotheque que le reste du logiciel utilise deja.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

from core.logging_setup import get_logger
from utils.errors import YouTubeDownloadError

logger = get_logger()

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
}


class InvalidYouTubeUrl(ValueError):
    """URL vide, mal formee, ou qui ne designe pas une video YouTube."""


def parse_video_id(value: str) -> str:
    """Identifiant de la video, ou InvalidYouTubeUrl avec un message lisible.

    Formats acceptes : youtube.com/watch?v=, youtu.be/, /shorts/, /embed/,
    /live/, et un identifiant seul. Le message d'erreur dit ce qui ne va pas
    plutot que "URL invalide" : le probleme est presque toujours un copier-
    coller depuis une autre plateforme.
    """
    raw = (value or "").strip()
    if not raw:
        raise InvalidYouTubeUrl("Colle d'abord l'adresse d'une vidéo YouTube.")

    if VIDEO_ID_RE.match(raw):
        return raw

    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except ValueError as error:                     # pragma: no cover - urlparse est tolerant
        raise InvalidYouTubeUrl("Cette adresse n'est pas lisible.") from error

    host = (parsed.netloc or "").lower().split(":")[0]
    if host not in _HOSTS:
        raise InvalidYouTubeUrl(
            "Cette adresse n'est pas une vidéo YouTube. "
            "Voice Studio ne traite que YouTube pour l'instant."
        )

    path = parsed.path or ""
    if host.endswith("youtu.be"):
        candidate_id = path.strip("/").split("/")[0]
    elif path.startswith("/watch"):
        candidate_id = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        parts = [p for p in path.split("/") if p]
        candidate_id = parts[1] if len(parts) >= 2 and parts[0] in {
            "shorts", "embed", "live", "v"} else ""

    if not VIDEO_ID_RE.match(candidate_id or ""):
        raise InvalidYouTubeUrl(
            "Adresse YouTube reconnue, mais sans identifiant de vidéo. "
            "Utilise le lien d'une vidéo, pas celui d'une chaîne ou d'une playlist."
        )
    return candidate_id


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True)
class VideoInfo:
    """Ce que YouTube dit de la video. Un champ absent reste vide : rien n'est
    invente ici, l'interface n'affiche que ce qui existe."""

    video_id: str
    title: str = ""
    channel: str = ""
    duration_s: Optional[float] = None
    thumbnail_url: str = ""
    subtitle_languages: tuple = ()
    auto_caption_languages: tuple = ()
    is_live: bool = False

    @property
    def has_any_captions(self) -> bool:
        return bool(self.subtitle_languages or self.auto_caption_languages)


def _ydl_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }


def fetch_info(video_id: str, ydl_factory=None) -> VideoInfo:
    """Metadonnees publiques de la video.

    `ydl_factory` existe pour les tests : il remplace yt_dlp.YoutubeDL par un
    double, ce qui permet de verifier tout le decodage sans reseau.
    """
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError as error:
            raise YouTubeDownloadError(
                "yt-dlp n'est pas installé. Lance : pip install -r requirements.txt"
            ) from error
        ydl_factory = yt_dlp.YoutubeDL

    try:
        with ydl_factory(_ydl_options()) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False) or {}
    except Exception as error:
        raise YouTubeDownloadError(explain_error(str(error))) from error

    return VideoInfo(
        video_id=info.get("id") or video_id,
        title=info.get("title") or "",
        channel=info.get("uploader") or info.get("channel") or "",
        duration_s=float(info["duration"]) if info.get("duration") else None,
        thumbnail_url=info.get("thumbnail") or "",
        subtitle_languages=tuple(sorted((info.get("subtitles") or {}).keys())),
        auto_caption_languages=tuple(sorted((info.get("automatic_captions") or {}).keys())),
        is_live=bool(info.get("is_live")),
    )


def explain_error(raw_message: str) -> str:
    """Message technique de yt-dlp -> phrase utilisable.

    Meme role que youtube/downloader._explain_download_error, mais cette
    fonction sert la LECTURE des metadonnees et des sous-titres : les cas
    frequents n'y sont pas les memes (video privee, region, reseau coupe), et
    l'appelant n'est pas un telechargement.
    """
    lowered = (raw_message or "").lower()
    if "private video" in lowered:
        return "Cette vidéo est privée : son contenu n'est pas accessible."
    if "video unavailable" in lowered or "has been removed" in lowered:
        return "Cette vidéo n'existe plus (supprimée ou retirée par son auteur)."
    if "age-restricted" in lowered or "sign in to confirm your age" in lowered:
        return "Cette vidéo est soumise à une vérification d'âge : Voice Studio ne peut pas y accéder."
    if "is a live" in lowered or "live event" in lowered:
        return ("Cette vidéo est un direct en cours. Attends sa fin : elle deviendra "
                "une vidéo normale, transcriptible.")
    if "not available in your country" in lowered or "geo" in lowered and "restrict" in lowered:
        return "Cette vidéo est bloquée dans ta région."
    if "unable to connect" in lowered or "proxy" in lowered or "timed out" in lowered \
            or "connection" in lowered or "network" in lowered:
        return ("Impossible de joindre YouTube. Vérifie ta connexion internet, "
                "puis réessaie.")
    return f"YouTube n'a pas pu être interrogé. Détail : {(raw_message or '')[:300]}"
