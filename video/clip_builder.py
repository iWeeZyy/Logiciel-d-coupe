"""Produit un clip fini en UN SEUL appel ffmpeg : montage, cadrage, zoom,
mise a l'echelle 9:16 et sous-titres incrustes.

Ce module reste volontairement mince : il ecrit le fichier de sous-titres puis
delegue la construction de la ligne de commande a video/filter_graph.py, seul
endroit qui connait la syntaxe des filtres. Un seul encodage, donc aucune perte
de qualite due a des passes successives.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from core.cancellation import CancelToken
from editing.timeline import EditList
from video.cropper import TARGET_H, TARGET_W
from video.ffmpeg_utils import has_audio_stream, run_ffmpeg, video_resolution
from video.filter_graph import build_ffmpeg_args
from video.intro_concat import build_concat_args
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
    watermark=None,
    fill: str = "flou",
    fit: str = "recadrer",
    delire_plan=None,
    intro=None,
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
        watermark=watermark,
        delire_plan=delire_plan,
        fill=fill,
        fit=fit,
        **({"target_size": tuple(target_size)} if target_size else {}),
    )
    run_ffmpeg(args, description=f"generation du clip {clip_label}", cancel_token=cancel_token)

    # L'intro se pose APRES coup, par un second appel ffmpeg : le clip
    # principal est deja un fichier fini a ce stade (voir intro_concat.py pour
    # pourquoi ce n'est pas fondu dans le graphe ci-dessus). `intro is None`
    # ou une video manquante sur le disque -> ce bloc ne fait rien, le clip
    # sort exactement comme avant cette fonctionnalite.
    if intro is not None and intro.exists:
        resolved_target = tuple(target_size) if target_size else (TARGET_W, TARGET_H)
        with_intro_path = out_mp4_path + ".intro.mp4"
        concat_args = build_concat_args(
            intro_path=intro.video,
            intro_src_size=video_resolution(intro.video),
            clip_path=out_mp4_path,
            clip_has_audio=has_audio_stream(out_mp4_path),
            out_mp4_path=with_intro_path,
            target_size=resolved_target,
            fps=fps,
            fill=fill,
            watermark=watermark,
            export_settings=export_settings,
        )
        try:
            run_ffmpeg(concat_args, description=f"ajout de l'intro au clip {clip_label}",
                      cancel_token=cancel_token)
        except Exception:
            # Meme principe que le nettoyage plus haut dans pipeline.py : un
            # fichier temporaire incomplet ne doit jamais rester sur le
            # disque. out_mp4_path, lui, est intact -- c'est encore le clip
            # SANS intro a ce point, pipeline.py le nettoiera comme d'habitude
            # si l'appelant considere l'echec fatal.
            Path(with_intro_path).unlink(missing_ok=True)
            raise
        os.replace(with_intro_path, out_mp4_path)
