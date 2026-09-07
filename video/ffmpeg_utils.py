"""Point d'appel unique vers les binaires ffmpeg/ffprobe (subprocess).

Tout le reste de video/ passe par ici plutot que d'appeler subprocess directement,
pour centraliser la gestion d'erreurs (point 14 : messages explicites, jamais
une stack trace ffmpeg brute).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.paths import app_base_dir
from utils.errors import CancelledError, FfmpegError, InputFileError

logger = get_logger()

_STDERR_TAIL_LINES = 25

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def _resolve_binary(name: str) -> str:
    """Priorite a une copie embarquee a cote de l'executable (build .exe --
    voir build/build_windows.py), sinon le binaire du PATH systeme."""
    bundled = app_base_dir() / f"{name}{_EXE_SUFFIX}"
    if bundled.exists():
        return str(bundled)
    found = shutil.which(name)
    return found or name


FFMPEG_BIN = _resolve_binary("ffmpeg")
FFPROBE_BIN = _resolve_binary("ffprobe")


def ensure_ffmpeg_available() -> None:
    if shutil.which(FFMPEG_BIN) is None or shutil.which(FFPROBE_BIN) is None:
        raise FfmpegError(
            "ffmpeg (et/ou ffprobe) est introuvable. Installe-le (voir README.md, "
            "section Installation) puis relance -- ou utilise la version .exe qui "
            "l'embarque deja."
        )


_CANCEL_POLL_S = 0.3


def run_ffmpeg(args: list[str], description: str, cancel_token: Optional[CancelToken] = None) -> None:
    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error"] + args
    logger.debug("ffmpeg: " + " ".join(cmd))

    if cancel_token is None:
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError as e:
            raise FfmpegError("ffmpeg introuvable. Voir README.md.") from e
    else:
        # Popen + attente par petits pas plutot qu'un subprocess.run() bloquant :
        # c'est ce qui permet a la GUI d'interrompre un encodage en cours au lieu
        # d'attendre qu'il se termine de lui-meme avant de reagir a "Annuler".
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError as e:
            raise FfmpegError("ffmpeg introuvable. Voir README.md.") from e

        while proc.poll() is None:
            if cancel_token.is_cancelled:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise CancelledError("Analyse annulee pendant un encodage ffmpeg.")
            time.sleep(_CANCEL_POLL_S)

        stdout, stderr = proc.communicate()
        result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
        raise FfmpegError(f"Echec ffmpeg pendant : {description}\n--- ffmpeg stderr (fin) ---\n{tail}")


def probe(path: str) -> dict:
    cmd = [
        FFPROBE_BIN, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        raise FfmpegError("ffprobe introuvable. Voir README.md.") from e

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


def video_fps(path: str, default: float = 25.0) -> float:
    """Cadence de la piste video. Necessaire au zoom dynamique : zoompan impose
    sa propre cadence de sortie et ramenerait sinon la video a 25 images/s."""
    info = probe(path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        for key in ("avg_frame_rate", "r_frame_rate"):
            value = stream.get(key)
            if not value or "/" not in value:
                continue
            num, den = value.split("/", 1)
            try:
                num, den = float(num), float(den)
            except ValueError:
                continue
            if den > 0 and num > 0:
                return num / den
    return default
