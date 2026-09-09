"""Telechargement d'un clip Twitch, par le moyen que Twitch propose lui-meme.

CORRECTION D'UNE ERREUR DE CONCEPTION. Les premieres versions du Radar
affirmaient que "Twitch ne fournit aucun moyen officiel de telecharger un
clip". C'etait faux : le menu Partager d'un clip contient "Telecharger la
version paysage" et "Telecharger la version portrait". Recuperer le fichier
d'un clip est donc une action que la plateforme offre, et le refuser au nom de
ses conditions d'utilisation etait une prudence mal placee -- elle interdisait
ce que Twitch autorise.

CE QUE CELA NE CHANGE PAS. Pouvoir telecharger un clip ne donne aucun droit de
le republier ni de le monetiser. Le bouton de Twitch fournit un fichier, pas
une licence : les droits restent au streamer et, le cas echeant, aux tiers
visibles ou audibles dans le clip. La verification des droits reste donc
affichee avant toute republication -- ce n'est plus une condition pour
telecharger, c'est un rappel avant de publier.

Une VOD ou un direct, eux, n'ont toujours aucun bouton de telechargement chez
Twitch : ce module ne traite que les clips, et c'est pour cela qu'il porte ce
nom.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.logging_setup import get_logger
from utils.errors import CancelledError, MediaNotAvailableError
from video.ffmpeg_utils import FFMPEG_BIN
from video.ytdlp_utils import build_format, find_downloaded_file

logger = get_logger()

# Un slug de clip Twitch : des mots colles en CamelCase, parfois avec des
# tirets. On ne valide pas plus finement -- c'est yt-dlp qui sait vraiment ce
# qu'est une URL de clip, et une regle trop stricte ici refuserait des clips
# valides pour rien.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{4,120}$")

RIGHTS_NOTICE = (
    "Twitch permet de télécharger un clip, mais fournir un fichier n'est pas "
    "céder des droits : le clip reste la propriété de son créateur, et peut "
    "contenir des tiers, de la musique ou du jeu soumis à leurs propres règles.\n\n"
    "Assurez-vous d'avoir l'autorisation nécessaire avant de republier ou de "
    "monétiser ce contenu."
)


def clip_url(value: str) -> str:
    """URL de clip a partir d'une URL complete ou d'un simple identifiant."""
    value = (value or "").strip()
    if not value:
        raise MediaNotAvailableError("Aucune adresse de clip fournie.")
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if _SLUG_RE.match(value):
        return f"https://clips.twitch.tv/{value}"
    raise MediaNotAvailableError(f"Adresse de clip Twitch non reconnue : {value}")


def _explain(raw_message: str) -> str:
    lowered = (raw_message or "").lower()
    if "does not exist" in lowered or "404" in lowered or "not found" in lowered:
        return ("Ce clip n'existe plus sur Twitch : il a été supprimé, ou la chaîne "
                "n'est plus accessible.")
    if "unavailable" in lowered or "private" in lowered:
        return "Ce clip n'est pas accessible publiquement sur Twitch."
    if "unsupported url" in lowered:
        return ("Cette adresse n'est pas reconnue comme un clip Twitch. Seuls les "
                "clips peuvent être téléchargés : Twitch ne propose aucun "
                "téléchargement pour une VOD ou un direct.")
    if "timed out" in lowered or "connection" in lowered or "network" in lowered:
        return ("Twitch est injoignable pour l'instant. Vérifiez votre connexion, "
                "puis relancez.")
    return f"Le téléchargement du clip a échoué. Détail : {raw_message.strip()[:300]}"


def download_clip(url_or_slug: str, out_dir: str, *, max_height: int = 0,
                  on_progress=None, cancel_token=None) -> str:
    """Telecharge un clip et renvoie le chemin du fichier.

    `on_progress` recoit une fraction entre 0 et 1 quand yt-dlp connait la
    taille totale, et None sinon -- une barre qui avance au hasard vaut moins
    qu'une barre qui assume ne pas savoir.
    """
    try:
        import yt_dlp
    except ImportError as error:
        raise MediaNotAvailableError(
            "yt-dlp n'est pas installé. Lance : pip install -r requirements.txt"
        ) from error

    url = clip_url(url_or_slug)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    def hook(status: dict) -> None:
        # L'annulation est verifiee ICI parce que c'est le seul endroit ou le
        # code reprend la main pendant un telechargement : sans ce crochet, le
        # bouton Annuler n'aurait aucun effet avant la fin du transfert.
        if cancel_token is not None:
            cancel_token.check()
        if on_progress is None or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes") or 0
        on_progress(min(1.0, done / total) if total else None)

    options = {
        "format": build_format(max_height),
        "outtmpl": str(Path(out_dir) / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "progress_hooks": [hook],
    }
    if Path(FFMPEG_BIN).exists():
        options["ffmpeg_location"] = str(Path(FFMPEG_BIN).resolve().parent)

    logger.info(f"Téléchargement du clip Twitch {url}...")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except CancelledError:
        raise
    except Exception as error:      # yt_dlp.utils.DownloadError et consorts
        raise MediaNotAvailableError(_explain(str(error))) from error

    path = find_downloaded_file(out_dir, info.get("id", ""))
    if path is None:
        raise MediaNotAvailableError(
            "Le téléchargement s'est terminé sans erreur mais aucun fichier n'a été "
            "trouvé. Réessayez, ou indiquez le fichier du clip manuellement.")
    height = info.get("height")
    logger.info(f"Clip téléchargé : {path.name}" + (f" ({height}p)" if height else ""))
    return str(path)
