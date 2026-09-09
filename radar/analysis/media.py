"""Obtention du media a analyser, et rien de plus (section 4, etape 1).

CE MODULE NE CONTOURNE RIEN, et c'est sa raison d'etre.

Twitch ne publie aucun moyen officiel de recuperer le fichier d'un clip, d'une
VOD ou d'un direct. Les methodes qui existent -- deviner l'URL du mp4 a partir
de celle de la miniature, passer par l'API interne du site, se faire passer
pour un navigateur -- fonctionnent, et sont exactement ce que les conditions
d'utilisation de Twitch interdisent. Le Radar les refuse depuis sa premiere
version (radar/bridge.py) ; l'analyse de contenu s'aligne dessus au lieu
d'ouvrir une seconde porte qui rendrait la premiere inutile.

Consequence assumee, et c'est la seule limite reelle de la fonctionnalite :
pour analyser un clip Twitch, il faut designer un fichier dont on dispose
legalement. L'association est ensuite MEMORISEE, donc le geste ne se refait pas
a chaque analyse du meme clip.

Pour YouTube, rien de nouveau non plus : youtube/downloader.py existe deja,
avec sa confirmation de droits obligatoire. Ce module l'appelle, il ne le
reecrit pas.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from radar.models import PLATFORM_TWITCH, PLATFORM_YOUTUBE
from utils.errors import MediaNotAvailableError

logger = get_logger()

# Taille lue pour l'empreinte. Un clip fait quelques dizaines de Mo : hacher le
# fichier entier a chaque ouverture de la fenetre serait du temps perdu, alors
# que le premier mega-octet plus la taille exacte suffisent largement a
# distinguer deux fichiers differents.
FINGERPRINT_BYTES = 1_048_576

TWITCH_EXPLANATION = (
    "Twitch ne fournit aucun moyen officiel de télécharger un clip, une VOD ou un "
    "direct. ClipFarming ne contourne pas cette limite.\n\n"
    "Pour analyser ce clip, indiquez un fichier vidéo ou audio dont vous disposez "
    "légalement (votre propre contenu, ou celui d'un créateur qui vous a donné son "
    "accord). Le fichier choisi sera mémorisé pour ce clip."
)

YOUTUBE_EXPLANATION = (
    "Le téléchargement YouTube demande une confirmation explicite de vos droits sur "
    "le contenu. Sans cette confirmation, indiquez un fichier local dont vous "
    "disposez légalement."
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


def explanation_for(opportunity) -> str:
    platform = getattr(opportunity, "platform", "")
    if platform == PLATFORM_TWITCH:
        return TWITCH_EXPLANATION
    if platform == PLATFORM_YOUTUBE:
        return YOUTUBE_EXPLANATION
    return TWITCH_EXPLANATION


def resolve(opportunity, *, local_path: str | None = None, download_dir: str | None = None,
            rights_confirmed: bool = False) -> MediaSource:
    """Media a analyser pour cette opportunite.

    Ordre volontaire : un fichier local designe par l'utilisateur gagne toujours,
    y compris sur YouTube. C'est la voie la plus sure -- elle ne telecharge rien
    -- et la seule disponible pour Twitch.
    """
    if local_path:
        return from_local_file(local_path)

    if not can_analyze(opportunity):
        raise MediaNotAvailableError(
            "Un direct en cours n'a pas de média figé : il n'y a rien à analyser "
            "tant que le stream n'est pas terminé.")

    platform = getattr(opportunity, "platform", "")
    if platform == PLATFORM_YOUTUBE and rights_confirmed and download_dir:
        from youtube.downloader import download_video
        url = getattr(opportunity, "url", "") or getattr(opportunity, "content_id", "")
        logger.info(f"Téléchargement YouTube pour analyse : {url}")
        downloaded = download_video(url, download_dir, consent_confirmed=True)
        return from_local_file(downloaded, origin="youtube", temporary=True)

    raise MediaNotAvailableError(explanation_for(opportunity))
