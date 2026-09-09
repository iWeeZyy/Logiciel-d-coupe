"""Exports de la transcription : TXT, SRT, VTT.

Les deux formats de sous-titres passent par export/subtitles_export.py, deja
utilise par le pipeline video. Ses fonctions ne demandent d'un bloc que
`start`, `end` et `text` : un segment de transcription les porte, il n'y avait
donc rien a reecrire, et surtout pas un deuxieme formateur de minutage -- deux
formateurs finissent toujours par ne plus ecrire la meme chose.

Les minutages sont ceux de la transcription. Aucun n'est arrondi, invente, ni
recalcule.
"""
from __future__ import annotations

from pathlib import Path

from export.subtitles_export import build_srt, build_vtt
from voice_studio.transcript import VIEW_RAW, format_timestamp, view_text

FORMATS = ("txt", "srt", "vtt")


def build_txt(transcript, with_timestamps: bool = True, view: str = VIEW_RAW) -> str:
    """Texte lisible. Avec minutage, chaque bloc est precede de son intervalle ;
    sans, c'est le texte seul, pret a etre colle ailleurs."""
    if with_timestamps:
        blocks = [f"[{format_timestamp(s.start)} → {format_timestamp(s.end)}]\n{s.text}"
                  for s in getattr(transcript, "segments", []) or []]
        return "\n\n".join(blocks).strip() + "\n" if blocks else ""
    text = view_text(transcript, view=view, with_timestamps=False)
    return (text + "\n") if text else ""


def build(transcript, fmt: str, with_timestamps: bool = True, view: str = VIEW_RAW) -> str:
    segments = list(getattr(transcript, "segments", []) or [])
    if fmt == "srt":
        return build_srt(segments)
    if fmt == "vtt":
        return build_vtt(segments)
    if fmt == "txt":
        return build_txt(transcript, with_timestamps=with_timestamps, view=view)
    raise ValueError(f"Format d'export inconnu : {fmt}")


def write(transcript, path: str, fmt: str | None = None, with_timestamps: bool = True,
          view: str = VIEW_RAW) -> str:
    """Ecrit le fichier et renvoie son chemin. Le format vient de l'extension
    quand il n'est pas donne -- l'utilisateur choisit un nom, pas un format."""
    target = Path(path)
    fmt = (fmt or target.suffix.lstrip(".")).lower()
    content = build(transcript, fmt, with_timestamps=with_timestamps, view=view)
    if not content.strip():
        raise ValueError("Il n'y a rien à exporter : la transcription est vide.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


def suggested_filename(project, fmt: str) -> str:
    """Nom de fichier propose : le titre de la video quand on le connait, son
    identifiant sinon. Jamais un nom generique qui rendrait deux exports
    indistinguables."""
    base = (getattr(project, "title", "") or getattr(project, "youtube_video_id", "")
            or "transcription")
    keep = [c if (c.isalnum() or c in " -_") else " " for c in base]
    cleaned = " ".join("".join(keep).split())[:80].strip() or "transcription"
    return f"{cleaned}.{fmt}"
