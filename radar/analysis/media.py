"""Obtention du media a analyser (section 4, etape 1).

CE MODULE A ETE CORRIGE APRES UNE ERREUR DE FAIT. Il refusait de telecharger un
clip Twitch, au motif que la plateforme n'offrirait aucun moyen officiel de le
faire. C'est faux : le menu Partager d'un clip propose "Telecharger la version
paysage" et "Telecharger la version portrait". Refuser interdisait donc ce que
Twitch autorise, et obligeait a fournir un fichier a la main pour une operation
que l'application peut faire elle-meme. Un clip est desormais telecharge
automatiquement (radar/clip_download.py).

Ce qui reste vrai, et qui n'est pas la meme question : disposer du fichier ne
donne aucun droit de republication. Twitch fournit un fichier, pas une licence.
L'avertissement sur les droits n'est donc plus une condition pour telecharger,
c'est un rappel avant de publier.

Ce qui reste refuse, faute de tout mecanisme officiel : les VOD et les directs.
Twitch n'offre aucun bouton de telechargement pour eux, et il n'est pas question
d'aller le chercher autrement. Pour ceux-la, un fichier local reste attendu.

Pour YouTube, rien ne change : telecharger depuis YouTube contrevient a SES
conditions quelle que soit la licence affichee, donc youtube/downloader.py et sa
confirmation de droits obligatoire restent le seul chemin.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import user_data_dir
from radar.models import KIND_CLIP, PLATFORM_TWITCH, PLATFORM_YOUTUBE
from utils.errors import MediaNotAvailableError

logger = get_logger()

# Taille lue pour l'empreinte. Un clip fait quelques dizaines de Mo : hacher le
# fichier entier a chaque ouverture de la fenetre serait du temps perdu, alors
# que le premier mega-octet plus la taille exacte suffisent largement a
# distinguer deux fichiers differents.
FINGERPRINT_BYTES = 1_048_576

TWITCH_EXPLANATION = (
    "Le clip est téléchargé automatiquement depuis Twitch, qui propose ce "
    "téléchargement.\n\n"
    "Disposer du fichier ne donne pas pour autant le droit de le republier : le "
    "clip appartient à son créateur et peut contenir des tiers, de la musique ou "
    "du jeu soumis à leurs propres règles."
)

TWITCH_NO_DOWNLOAD_EXPLANATION = (
    "Twitch ne propose de téléchargement que pour les clips, pas pour les VOD ni "
    "les directs, et ClipFarming ne contourne pas cette limite.\n\n"
    "Pour analyser ce contenu, indiquez un fichier vidéo ou audio dont vous "
    "disposez légalement. Le fichier choisi sera mémorisé."
)

MISSING_FILE = "Le fichier indiqué est introuvable : {path}"
EMPTY_FILE = "Le fichier indiqué est vide : {path}"


@dataclass(frozen=True)
class MediaSource:
    """Un media pret a analyser."""

    path: str
    fingerprint: str
    origin: str = "local"       # "local" | "youtube"
    temporary: bool = False     # a supprimer apres analyse

    @property
    def name(self) -> str:
        return Path(self.path).name


def fingerprint(path: str | Path) -> str:
    """Empreinte stable d'un fichier : taille + debut du contenu.

    Sert de cle de cache (section 17). Deux exports differents du meme clip
    n'ont aucune raison de donner la meme empreinte, et c'est voulu : ce sont
    deux medias differents, l'analyse doit etre refaite.
    """
    file_path = Path(path)
    size = file_path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("utf-8"))
    with open(file_path, "rb") as handle:
        digest.update(handle.read(FINGERPRINT_BYTES))
    return digest.hexdigest()[:32]


def from_local_file(path: str | Path, origin: str = "local", temporary: bool = False) -> MediaSource:
    file_path = Path(path)
    if not file_path.is_file():
        raise MediaNotAvailableError(MISSING_FILE.format(path=file_path))
    if file_path.stat().st_size == 0:
        raise MediaNotAvailableError(EMPTY_FILE.format(path=file_path))
    return MediaSource(path=str(file_path), fingerprint=fingerprint(file_path),
                       origin=origin, temporary=temporary)


def can_analyze(opportunity) -> bool:
    """Un contenu est analysable s'il a une duree connue : un direct en cours
    n'a pas de media fige, il n'y a rien a transcrire de facon stable."""
    return not getattr(opportunity, "is_live", False)


def can_download(opportunity) -> bool:
    """L'application peut-elle recuperer ce media elle-meme ?

    Vrai pour un clip Twitch, que la plateforme propose au telechargement. Faux
    pour une VOD, un direct, et pour YouTube, dont le telechargement passe par
    le chemin dedie avec confirmation des droits.
    """
    return (getattr(opportunity, "platform", "") == PLATFORM_TWITCH
            and getattr(opportunity, "kind", "") == KIND_CLIP
            and not getattr(opportunity, "is_live", False))


def clips_dir() -> Path:
    """Ou sont gardes les clips telecharges.

    Dans un dossier durable et non temporaire : un clip garde est un clip qu'on
    ne retelecharge pas a chaque reanalyse, ni pour l'envoyer ensuite au Content
    Factory. L'emplacement est modifiable dans les Parametres -- des clips
    s'accumulent, et le disque systeme n'est pas toujours le bon endroit.

    L'import de gui.settings_store est fait ICI et non en tete de fichier : ce
    module est un simple fichier de preferences en JSON, sans aucune dependance
    a Qt, mais il vit sous gui/. L'importer paresseusement et avec un repli
    garantit qu'un usage en ligne de commande ne depende jamais de lui.
    """
    try:
        from gui import settings_store

        return settings_store.clips_dir()
    except Exception:
        return user_data_dir() / "clips"


def explanation_for(opportunity) -> str:
    platform = getattr(opportunity, "platform", "")
    if platform == PLATFORM_TWITCH:
        return TWITCH_EXPLANATION if can_download(opportunity) else TWITCH_NO_DOWNLOAD_EXPLANATION
    if platform == PLATFORM_YOUTUBE:
        return YOUTUBE_EXPLANATION
    return TWITCH_NO_DOWNLOAD_EXPLANATION


def cached_clip(opportunity) -> Path | None:
    """Clip deja telecharge pour cette opportunite, s'il est toujours la."""
    from video.ytdlp_utils import find_downloaded_file

    content_id = getattr(opportunity, "content_id", "")
    if not content_id or not clips_dir().is_dir():
        return None
    return find_downloaded_file(str(clips_dir()), content_id)


def resolve(opportunity, *, local_path: str | None = None, download_dir: str | None = None,
            rights_confirmed: bool = False, on_progress=None, cancel_token=None) -> MediaSource:
    """Media a analyser pour cette opportunite.

    Ordre volontaire : un fichier local designe par l'utilisateur gagne toujours
    -- c'est le seul cas ou il a explicitement choisi quelque chose.
    """
    if local_path:
        return from_local_file(local_path)

    if not can_analyze(opportunity):
        raise MediaNotAvailableError(
            "Un direct en cours n'a pas de média figé : il n'y a rien à analyser "
            "tant que le stream n'est pas terminé.")

    if can_download(opportunity):
        existing = cached_clip(opportunity)
        if existing is not None:
            logger.info(f"Clip déjà téléchargé, réutilisé : {existing.name}")
            return from_local_file(existing, origin="twitch")

        from radar.clip_download import download_clip

        target = Path(download_dir) if download_dir else clips_dir()
        downloaded = download_clip(
            getattr(opportunity, "url", "") or getattr(opportunity, "content_id", ""),
            str(target), on_progress=on_progress, cancel_token=cancel_token)
        return from_local_file(downloaded, origin="twitch")

    platform = getattr(opportunity, "platform", "")
    if platform == PLATFORM_YOUTUBE and rights_confirmed and download_dir:
        from youtube.downloader import download_video
        url = getattr(opportunity, "url", "") or getattr(opportunity, "content_id", "")
        logger.info(f"Téléchargement YouTube pour analyse : {url}")
        downloaded = download_video(url, download_dir, consent_confirmed=True)
        return from_local_file(downloaded, origin="youtube", temporary=True)

    raise MediaNotAvailableError(explanation_for(opportunity))
