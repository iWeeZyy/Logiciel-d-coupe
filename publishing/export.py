"""Export manuel d'un clip pret a publier (section 17).

Ce n'est pas un repli de secours honteux : c'est le chemin qui marche toujours.
La publication directe demande, chez Meta comme chez TikTok, une application
developpeur validee et un type de compte particulier. Tant que ce n'est pas
fait -- et ce peut ne jamais l'etre -- l'application doit rester utile.

Un export contient tout ce qu'il faut pour publier a la main en trois copier-
coller : la video, la legende, les hashtags, et la couverture si elle existe.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from publishing.models import PLATFORM_LABELS, PublicationDraft

logger = get_logger()

CAPTION_FILE = "legende.txt"
HASHTAGS_FILE = "hashtags.txt"
COVER_FILE = "couverture.jpg"


@dataclass(frozen=True)
class ExportResult:
    directory: str
    video: str
    caption_file: str
    hashtags_file: str
    cover: str = ""
    reused: bool = False        # la video etait deja la, elle n'a pas ete recopiee


def _safe_name(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else " " for c in (value or "")).strip()
    return " ".join(cleaned.split())[:60] or "clip"


def export_dir_for(root: str | Path, platform: str, draft: PublicationDraft) -> Path:
    label = PLATFORM_LABELS.get(platform, platform).replace(" ", "")
    return Path(root) / "Exports" / label / _safe_name(draft.clip_title or draft.content_id)


def export(draft: PublicationDraft, platform: str, root: str | Path) -> ExportResult:
    """Ecrit le dossier d'export et renvoie ce qui s'y trouve.

    La video n'est recopiee que si elle n'est pas deja la, a la meme taille :
    un clip pese des dizaines de megaoctets, et le recopier a chaque ouverture
    de la fenetre remplirait le disque pour rien (section 17, "ne pas creer de
    doublons inutiles").
    """
    directory = export_dir_for(root, platform, draft)
    directory.mkdir(parents=True, exist_ok=True)

    source = Path(draft.clip_path)
    target = directory / source.name
    reused = False
    if source.is_file():
        if target.is_file() and target.stat().st_size == source.stat().st_size:
            reused = True
        else:
            shutil.copy2(source, target)
    else:
        logger.warning(f"Export : vidéo introuvable ({source})")

    caption_path = directory / CAPTION_FILE
    caption_path.write_text(draft.full_text_for(platform) + "\n", encoding="utf-8")

    hashtags_path = directory / HASHTAGS_FILE
    hashtags_path.write_text(" ".join(draft.hashtags) + "\n", encoding="utf-8")

    cover = ""
    if draft.cover_path and Path(draft.cover_path).is_file():
        cover_target = directory / COVER_FILE
        shutil.copy2(draft.cover_path, cover_target)
        cover = str(cover_target)

    logger.info(f"Export {PLATFORM_LABELS.get(platform, platform)} : {directory}")
    return ExportResult(
        directory=str(directory),
        video=str(target) if source.is_file() else "",
        caption_file=str(caption_path),
        hashtags_file=str(hashtags_path),
        cover=cover,
        reused=reused,
    )
