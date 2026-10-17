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
from video.ffmpeg_utils import run_ffmpeg, video_duration, video_resolution
from video.filter_graph import build_ffmpeg_args
from video.intro_overlay import IntroOverlay
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
    webcam_plan=None,
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

    # L'intro se pose EN INCRUSTATION sur le debut du clip, dans le MEME
    # passage ffmpeg que tout le reste (un calque de plus pour le
    # compositeur a N calques de build_ffmpeg_args -- voir intro_overlay.py) :
    # sa duree et ses dimensions reelles sont lues une fois ici, avant de
    # construire le graphe. `intro is None` ou une video manquante sur le
    # disque -> aucun calque ajoute, le clip sort exactement comme avant
    # cette fonctionnalite.
    import tempfile
    from dataclasses import replace

    from video.cropper import TARGET_H, TARGET_W

    workdir = tempfile.TemporaryDirectory(prefix="clipfarming_intro_")
    intro_overlay = None
    if intro is not None and intro.exists:
        intro_w, intro_h = video_resolution(intro.video)
        intro_overlay = IntroOverlay(
            path=intro.video, src_w=intro_w, src_h=intro_h,
            duration=video_duration(intro.video),
        )
        # Phrase d'accroche sous le medaillon (« Les clips que tu n'as pas
        # vus en live ») : dessinee ici, posee par build_ffmpeg_args.
        text = getattr(intro, "text", "")
        if text:
            from pathlib import Path

            from video.intro_text import render_intro_text

            out_w, out_h = tuple(target_size) if target_size else (TARGET_W, TARGET_H)
            png = Path(workdir.name) / "intro_text.png"
            try:
                render_intro_text(intro_overlay, text, out_w, out_h, png)
                intro_overlay = replace(intro_overlay, text_png=str(png))
            except Exception as error:  # noqa: BLE001 -- le clip sort sans la phrase
                from core.logging_setup import get_logger

                get_logger().warning(f"Phrase de l'intro non dessinee : {error}")

    # Themes en mosaique (confettis, euros, fleurs en vertical) : la copie
    # reduite de leur video est faite une fois, avant le premier export.
    theme = getattr(delire_plan, "theme", "") if delire_plan is not None else ""
    if theme:
        from video.delire_theme_filters import prepare_tile_sources
        from video.filter_graph import TARGET_H, TARGET_W

        out_w, out_h = tuple(target_size) if target_size else (TARGET_W, TARGET_H)
        prepare_tile_sources(theme, out_w, out_h, cancel_token=cancel_token)

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
        intro=intro_overlay,
        webcam_plan=webcam_plan,
        **({"target_size": tuple(target_size)} if target_size else {}),
    )
    try:
        run_ffmpeg(args, description=f"generation du clip {clip_label}", cancel_token=cancel_token)
    finally:
        workdir.cleanup()
