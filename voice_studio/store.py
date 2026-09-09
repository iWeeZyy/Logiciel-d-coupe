"""Enregistrement local des projets Voice Studio.

Un fichier JSON par video, sous %LOCALAPPDATA%\\ClipFarming (user_data_dir),
comme le reste de ce que l'application ecrit. Pas de base de donnees : un
projet est un document autonome, on le lit en entier ou pas du tout, et un
fichier lisible se recupere a la main le jour ou quelque chose tourne mal.

Le fichier EST le cache : rouvrir la meme video reaffiche sa transcription sans
rien recalculer. Rien n'est envoye nulle part.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import user_data_dir
from voice_studio.models import VoiceStudioProject, utc_now_iso

logger = get_logger()


def store_dir() -> Path:
    """Dossier des projets enregistres.

    Nomme "voice_studio_data" et non "voice_studio" : en developpement,
    user_data_dir() est la racine du depot, ou un dossier "voice_studio" existe
    deja -- c'est le paquet Python. Les projets iraient se ranger au milieu du
    code source.
    """
    return user_data_dir() / "voice_studio_data"


def project_path(video_id: str) -> Path:
    return store_dir() / f"{video_id}.json"


def load(video_id: str) -> VoiceStudioProject | None:
    path = project_path(video_id)
    if not path.is_file():
        return None
    try:
        return VoiceStudioProject.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        # Fichier abime : on l'ignore plutot que de faire echouer l'ouverture.
        # La video sera simplement retranscrite.
        logger.warning(f"Projet Voice Studio illisible ({path.name}) : {error}")
        return None


def save(project: VoiceStudioProject) -> Path:
    project.updated_at = utc_now_iso()
    path = project_path(project.youtube_video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ecriture en deux temps : une coupure de courant pendant un enregistrement
    # ne doit pas laisser un fichier a moitie ecrit a la place d'un projet
    # complet.
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)
    return path


def delete(video_id: str) -> bool:
    path = project_path(video_id)
    if path.is_file():
        path.unlink()
        return True
    return False


def list_projects() -> list[VoiceStudioProject]:
    """Projets enregistres, du plus recemment mis a jour au plus ancien."""
    directory = store_dir()
    if not directory.is_dir():
        return []
    projects = []
    for path in directory.glob("*.json"):
        project = load(path.stem)
        if project is not None:
            projects.append(project)
    return sorted(projects, key=lambda p: p.updated_at or "", reverse=True)


def audio_dir() -> Path:
    """Ou vont les voix generees. Un dossier visible et nomme : un fichier
    audio produit sans dire ou il est n'existe pas pour l'utilisateur."""
    path = store_dir() / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path
