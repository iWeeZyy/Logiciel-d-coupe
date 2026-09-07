"""Assemble decoupe + crop 9:16 + incrustation sous-titres en UN SEUL appel
ffmpeg par clip (un seul encodage, pas trois passes separees)."""
from __future__ import annotations

from typing import Optional

from core.cancellation import CancelToken
from video.cropper import build_crop_filter
from video.ffmpeg_utils import run_ffmpeg
from video.subtitle_renderer import render_ass_file, subtitle_filter


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
    ass_path: str,
    export_settings: dict,
    clip_label: str,
    cancel_token: Optional[CancelToken] = None,
) -> None:
    duration = max(0.05, end - start)

    crop_filter = build_crop_filter(src_w, src_h, face_hint)
    render_ass_file(words, clip_start=start, style=subtitle_style, out_ass_path=ass_path)
    sub_filter = subtitle_filter(ass_path)

    vf = f"{crop_filter},{sub_filter}"

    run_ffmpeg(
        [
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", str(export_settings.get("video_preset", "medium")),
            "-crf", str(export_settings.get("video_bitrate_crf", 20)),
            "-c:a", "aac",
            "-b:a", str(export_settings.get("audio_bitrate", "160k")),
            "-movflags", "+faststart",
            out_mp4_path,
        ],
        description=f"generation du clip {clip_label}",
        cancel_token=cancel_token,
    )
