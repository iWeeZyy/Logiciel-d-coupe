"""Video -> wav mono 16kHz (utilise pour la transcription Whisper ET l'analyse audio)."""
from __future__ import annotations

from video.ffmpeg_utils import has_audio_stream, run_ffmpeg
from utils.errors import NoAudioError


def extract_audio(video_path: str, out_wav_path: str, sample_rate: int = 16000) -> None:
    if not has_audio_stream(video_path):
        raise NoAudioError(
            f"'{video_path}' ne contient aucune piste audio -- impossible de "
            "transcrire ou d'analyser des hooks audio sur cette video."
        )
    run_ffmpeg(
        [
            "-i", video_path,
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-acodec", "pcm_s16le",
            out_wav_path,
        ],
        description="extraction audio",
    )
