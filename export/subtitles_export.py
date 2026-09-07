"""Export des sous-titres en fichiers .srt / .vtt (section 4 du cahier des
charges : incrustation obligatoire, fichiers optionnels).

Les fichiers sont produits a partir des MEMES CaptionGroup que le .ass incruste
dans la video (editing/captions.py) : impossible qu'un .srt exporte affiche un
decoupage ou des timings differents de ce qu'on voit a l'ecran.

La mise en evidence des mots n'est pas transcrite : .srt est un format de texte
brut, et les balises que certains lecteurs acceptent (<b>, <font>) sont mal
supportees ailleurs. Un fichier lisible partout vaut mieux qu'un fichier
enrichi que la moitie des lecteurs afficherait avec des balises visibles.
"""
from __future__ import annotations

from pathlib import Path

from editing.captions import CaptionGroup


def _timestamp(seconds: float, separator: str) -> str:
    seconds = max(0.0, seconds)
    ms_total = int(round(seconds * 1000))
    h, ms_total = divmod(ms_total, 3_600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{separator}{ms:03d}"


def build_srt(groups: list[CaptionGroup]) -> str:
    blocks = []
    for i, group in enumerate(groups, start=1):
        blocks.append(
            f"{i}\n"
            f"{_timestamp(group.start, ',')} --> {_timestamp(group.end, ',')}\n"
            f"{group.text}\n"
        )
    return "\n".join(blocks)


def build_vtt(groups: list[CaptionGroup]) -> str:
    blocks = ["WEBVTT\n"]
    for group in groups:
        blocks.append(
            f"{_timestamp(group.start, '.')} --> {_timestamp(group.end, '.')}\n"
            f"{group.text}\n"
        )
    return "\n".join(blocks)


def write_subtitles(
    output_dir: str,
    clip_stem: str,
    groups: list[CaptionGroup],
    srt: bool = True,
    vtt: bool = False,
) -> list[str]:
    """Ecrit subtitles/<clip_stem>.srt et/ou .vtt. Renvoie les chemins ecrits.

    Un clip sans mot detecte n'ecrit rien plutot qu'un fichier vide : un .srt
    de zero octet ressemble a un bug, une absence de fichier se comprend.
    """
    if not groups:
        return []

    base = Path(output_dir) / "subtitles"
    base.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    if srt:
        path = base / f"{clip_stem}.srt"
        path.write_text(build_srt(groups), encoding="utf-8")
        written.append(str(path))
    if vtt:
        path = base / f"{clip_stem}.vtt"
        path.write_text(build_vtt(groups), encoding="utf-8")
        written.append(str(path))
    return written
