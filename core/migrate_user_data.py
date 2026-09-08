"""Deplacement unique des donnees ecrites dans le dossier d'installation.

Les versions precedentes ecrivaient les projets, les caches, les reglages et la
cle YouTube a cote de ClipFarming.exe, donc dans Program Files. Ce module les
recupere au premier lancement de la version qui a corrige cela, pour que
personne ne retrouve son travail "disparu" apres une mise a jour.

Regles, dans cet ordre de priorite :
- ne JAMAIS ecraser : si la destination existe deja, l'ancienne copie est
  laissee en place et rien n'est touche ;
- ne jamais faire echouer le demarrage : toute erreur est journalisee et
  l'application continue avec les nouveaux emplacements, vides ;
- ne rien faire du tout hors mode fige : en developpement, ancien et nouvel
  emplacement sont le meme dossier.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import app_base_dir, default_projects_dir, is_frozen, user_data_dir

logger = get_logger()


def _move(source: Path, destination: Path) -> bool:
    if not source.exists() or destination.exists():
        return False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except OSError as error:
        # Program Files est protege en ecriture : la suppression de la source
        # peut echouer la ou la copie, elle, aurait reussi. On recopie donc
        # plutot que de laisser l'utilisateur sans ses donnees.
        try:
            if source.is_dir():
                shutil.copytree(str(source), str(destination), dirs_exist_ok=False)
            else:
                shutil.copy2(str(source), str(destination))
        except OSError as copy_error:
            logger.warning(f"Recuperation impossible de {source} vers {destination} : {copy_error}")
            return False
        logger.info(f"{source} recopie vers {destination} (l'original n'a pas pu etre supprime : {error}).")
        return True
    logger.info(f"{source} deplace vers {destination}.")
    return True


def migrate_legacy_user_data() -> list[tuple[Path, Path]]:
    """Recupere les donnees des anciennes versions. Renvoie ce qui a bouge."""
    if not is_frozen():
        return []

    legacy_root = app_base_dir()
    moves = [
        (legacy_root / "user_projects", default_projects_dir()),
        (legacy_root / ".cache", user_data_dir() / ".cache"),
        (legacy_root / "youtube_api_key.txt", user_data_dir() / "youtube_api_key.txt"),
        (legacy_root / "config" / "gui_settings.json", user_data_dir() / "gui_settings.json"),
    ]
    return [(src, dst) for src, dst in moves if _move(src, dst)]
