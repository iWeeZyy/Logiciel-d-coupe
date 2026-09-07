"""Point d'appel unique vers les binaires ffmpeg/ffprobe (subprocess).

Tout le reste de video/ passe par ici plutot que d'appeler subprocess directement,
pour centraliser la gestion d'erreurs (point 14 : messages explicites, jamais
une stack trace ffmpeg brute).
"""
from __future__ import annotations

import json
import shutil
import subprocess

from core.logging_setup import get_logger
from utils.errors import FfmpegError, InputFileError

logger = get_logger()

_STDERR_TAIL_LINES = 25


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise FfmpegError(
            "ffmpeg (et/ou ffprobe) est introuvable dans le PATH. Installe-le "
            "(voir README.md, section Installation) puis relance."
        )


def run_ffmpeg(args: list[str], description: str) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    logger.debug("ffmpeg: " + " ".join(cmd))
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        raise FfmpegError("ffmpeg introuvable dans le PATH. Voir README.md.") from e

    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
        raise FfmpegError(f"Echec ffmpeg pendant : {description}\n--- ffmpeg stderr (fin) ---\n{tail}")


def probe(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        raise FfmpegError("ffprobe introuvable dans le PATH. Voir README.md.") from e

    if result.returncode != 0:
        raise InputFileError(
            f"Impossible de lire '{path}' avec ffprobe -- fichier corrompu, "
            f"format non supporte, ou chemin incorrect.\n{result.stderr.strip()[-500:]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise InputFileError(f"Sortie ffprobe illisible pour '{path}'.") from e


def video_duration(path: str) -> float:
    info = probe(path)
    fmt_duration = info.get("format", {}).get("duration")
    if fmt_duration is not None:
        return float(fmt_duration)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("duration"):
            return float(stream["duration"])
    raise InputFileError(f"Impossible de determiner la duree de '{path}'.")


def has_audio_stream(path: str) -> bool:
    info = probe(path)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def video_resolution(path: str) -> tuple[int, int]:
    info = probe(path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise InputFileError(f"Aucune piste video trouvee dans '{path}'.")
