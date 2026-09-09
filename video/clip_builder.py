"""Produit un clip fini en UN SEUL appel ffmpeg : montage, cadrage, zoom,
mise a l'echelle 9:16 et sous-titres incrustes.

Ce module reste volontairement mince : il ecrit le fichier de sous-titres puis
delegue la construction de la ligne de commande a video/filter_graph.py, seul
endroit qui connait la syntaxe des filtres. Un seul encodage, donc aucune perte
de qualite due a des passes successives.
"""
from __future__ import annotations

from typing import Optional

from core.cancellation import CancelToken
from editing.timeline import EditList
from video.ffmpeg_utils import run_ffmpeg
from video.filter_graph import build_ffmpeg_args
from video.subtitle_renderer import render_ass_file


def build_clip(
    video_path: str,
    start: float,
    end: float,
    src_w: int,
    src_h: int,
    face_hint,
    words,
    subtitle_style: dict,
    out_mp4_path: str,
    ass_path: str | None,
    export_settings: dict,
    clip_label: str,
    cancel_token: Optional[CancelToken] = None,
    caption_groups=None,
    subtitle_margin_v: Optional[int] = None,
    edit_list: Optional[EditList] = None,
    framing_plan=None,
    zoom_track=None,
    audio_cfg: Optional[dict] = None,
    fps: float = 25.0,
    target_size: Optional[tuple] = None,
) -> None:
    """`edit_list`, `framing_plan`, `zoom_track` et `audio_cfg` viennent des
    modules d'edition automatique. Tous absents, le rendu est exactement celui
    d'avant leur ajout : decoupe simple, cadrage fixe, sous-titres incrustes."""
    edit_list = edit_list or EditList.identity(start, end)

    # ass_path a None = sous-titres desactives. On ne fabrique alors AUCUN
    # fichier de sous-titres : produire un .ass pour ne pas s'en servir
    # laisserait croire, en lisant le dossier de travail, qu'ils ont ete
    # incrustes.
    if ass_path:
        render_ass_file(
            words, clip_start=start, style=subtitle_style, out_ass_path=ass_path,
            caption_groups=caption_groups, margin_v=subtitle_margin_v,
        )

    args = build_ffmpeg_args(
        video_path=video_path,
        edit_list=edit_list,
        framing_plan=framing_plan,
        zoom_track=zoom_track,
        src_w=src_w,
        src_h=src_h,
        fps=fps,
        face_hint=face_hint,
        ass_path=ass_path,
        audio_cfg=audio_cfg,
        export_settings=export_settings,
        out_mp4_path=out_mp4_path,
        **({"target_size": tuple(target_size)} if target_size else {}),
    )
    run_ffmpeg(args, description=f"generation du clip {clip_label}", cancel_token=cancel_token)
