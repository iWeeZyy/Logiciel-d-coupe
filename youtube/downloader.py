"""Telechargement d'une video YouTube pour analyse locale, via yt-dlp.

Point legal a ne jamais oublier (voir README, section recherche YouTube) :
telecharger une video YouTube par ce moyen -- ou n'importe quel moyen autre
que le bouton de telechargement officiel de YouTube -- est une violation des
conditions d'utilisation de YouTube, INDEPENDAMMENT de la licence affichee
sur le contenu (Creative Commons ou non). Ce module ne pretend donc jamais
que l'operation est "legale" : il refuse d'agir sans confirmation explicite
et documentee de l'appelant (consent_confirmed=True), qui doit venir d'une
action explicite de l'utilisateur en CLI (--confirm-rights), jamais d'une
valeur par defaut.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.config_loader import load_youtube_config
from core.logging_setup import get_logger
from utils.errors import (
    CancelledError,
    RightsNotConfirmedError,
    YouTubeDownloadError,
)
from video.ffmpeg_utils import FFMPEG_BIN
# Helpers yt-dlp communs a YouTube et Twitch, ranges hors de ce paquet depuis
# que le telechargement des clips Twitch les utilise aussi. Reexportes ici :
# ils faisaient partie de l'interface de ce module.
from video.ytdlp_utils import build_format, find_downloaded_file  # noqa: F401

logger = get_logger()

_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

RIGHTS_WARNING = (
    "Telecharger une video YouTube par ce moyen viole les conditions "
    "d'utilisation de YouTube, quelle que soit sa licence affichee "
    "(Creative Commons inclus -- une licence sur le contenu ne donne aucun "
    "droit de telechargement depuis la plateforme). Verifie que tu disposes "
    "reellement des droits necessaires (autorisation du createur, licence "
    "explicite hors YouTube, contenu dont tu es l'auteur...) avant de "
    "continuer, republier ou monetiser un extrait."
)


def configured_max_height() -> int:
    """Plafond de definition lu dans config/youtube.json, 0 par defaut."""
    try:
        download = load_youtube_config().get("download") or {}
        value = download.get("max_height", 0)
        return int(value) if isinstance(value, (int, float)) and value > 0 else 0
    except Exception:      # pragma: no cover - config illisible
        return 0


def resolve_watch_url(video_id_or_url: str) -> str:
    value = video_id_or_url.strip()
    if _BARE_ID_RE.match(value):
        return f"https://www.youtube.com/watch?v={value}"
    return value  # deja une URL -- yt-dlp gere youtube.com/watch, youtu.be, shorts, etc.


# Hotes YouTube reconnus. La liste est EXPLICITE plutot qu'une recherche de
# « youtube » dans la chaine : « youtube.evil.example.com » contient « youtube »
# sans etre YouTube, et l'accueil ne doit pas envoyer n'importe quelle adresse a
# yt-dlp sous pretexte qu'elle en a l'air.
_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def looks_like_youtube(value: str) -> bool:
    """Cette chaine designe-t-elle une video YouTube ?

    FONCTION PURE, sans reseau : elle sert a activer ou griser un bouton, pas a
    garantir que la video existe. Un identifiant nu (11 caracteres) compte,
    puisque resolve_watch_url() sait deja le completer.

    Ce qu'elle REFUSE volontairement : un autre site, une adresse sans hote, et
    une chaine vide. Mieux vaut dire « ce n'est pas un lien YouTube » que de
    laisser yt-dlp echouer trente secondes plus tard sur un message technique.
    """
    value = (value or "").strip()
    if not value:
        return False
    if _BARE_ID_RE.match(value):
        return True

    from urllib.parse import urlparse

    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _YOUTUBE_HOSTS


def download_video(video_id_or_url: str, out_dir: str, consent_confirmed: bool, *,
                   max_height: int | None = None, on_progress=None,
                   cancel_token=None) -> str:
    """Telecharge la video et renvoie le chemin du fichier produit.

    Les trois arguments nommes sont ARRIVES APRES : le telechargement ne servait
    qu'a une analyse en ligne de commande, ou personne ne regarde une barre de
    progression et ou il n'y a pas de bouton Annuler. Ils sont donc optionnels,
    et sans eux le comportement est exactement celui d'avant.

    `max_height` a None laisse le plafond de config/youtube.json decider ; une
    valeur explicite (0 = aucune limite) le remplace pour cet appel seulement.
    """
    if not consent_confirmed:
        raise RightsNotConfirmedError(
            "Telechargement refuse : les droits necessaires n'ont pas ete "
            "confirmes.\n\n" + RIGHTS_WARNING + "\n\nRelance avec --confirm-rights "
            "une fois cette verification faite."
        )

    try:
        import yt_dlp
    except ImportError as e:
        raise YouTubeDownloadError(
            "yt-dlp n'est pas installe. Lance : pip install -r requirements.txt"
        ) from e

    url = resolve_watch_url(video_id_or_url)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    def _hook(status: dict) -> None:
        # Seul moment ou le code reprend la main pendant un transfert : c'est
        # donc ici que l'annulation est vue, et nulle part ailleurs. Meme
        # crochet que radar/clip_download.py, pour la meme raison.
        if cancel_token is not None:
            cancel_token.check()
        if on_progress is None or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes") or 0
        # Une fraction quand la taille totale est connue, None sinon : une barre
        # qui avance au hasard vaut moins qu'une barre qui assume ne pas savoir.
        on_progress(min(1.0, done / total) if total else None,
                    done / 1_000_000, (total / 1_000_000) if total else None)

    ceiling = configured_max_height() if max_height is None else int(max_height)
    ydl_opts = {
        "format": build_format(ceiling),
        "outtmpl": str(Path(out_dir) / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "progress_hooks": [_hook],
    }
    if Path(FFMPEG_BIN).exists():
        ydl_opts["ffmpeg_location"] = str(Path(FFMPEG_BIN).resolve().parent)

    logger.info(f"Telechargement de {url} (yt-dlp)...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except CancelledError:
        # Une annulation demandee par l'utilisateur n'est pas un echec de
        # telechargement : la presenter comme tel afficherait un message
        # d'erreur reseau a quelqu'un qui vient d'appuyer sur Annuler.
        raise
    except yt_dlp.utils.DownloadError as e:
        raise YouTubeDownloadError(_explain_download_error(str(e))) from e

    video_id = info.get("id")
    out_path = find_downloaded_file(out_dir, video_id)
    if out_path is None:
        raise YouTubeDownloadError(
            f"yt-dlp a termine sans erreur mais aucun fichier nomme '{video_id}' "
            f"n'a ete trouve dans {out_dir} -- format de sortie inattendu."
        )
    height = info.get("height")
    logger.info(f"Telechargement termine : {out_path.name}"
                + (f" ({height}p)" if height else ""))
    return str(out_path)


def _explain_download_error(raw_message: str) -> str:
    lowered = raw_message.lower()
    if "private video" in lowered:
        return "Cette video est privee -- impossible de la telecharger."
    if "video unavailable" in lowered or "has been removed" in lowered:
        return "Cette video n'est plus disponible (supprimee ou retiree)."
    if "sign in to confirm your age" in lowered or "age-restricted" in lowered:
        return "Cette video est soumise a une verification d'age -- non geree par ce module."
    if "live event" in lowered or "is a live stream" in lowered:
        return "Cette video est un live en cours -- non supporte (attends sa fin, elle deviendra une VOD normale)."
    if "not available in your country" in lowered or "geo" in lowered and "restrict" in lowered:
        return "Cette video est bloquee dans ta region."
    return f"Echec du telechargement : {raw_message[:400]}"
