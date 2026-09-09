"""Transcription locale d'une video YouTube : audio puis Faster-Whisper.

Aucun deuxieme moteur de transcription n'est cree ici. Ce module ne fait que
deux choses : recuperer une piste audio exploitable, et appeler
transcription/whisper_engine.py -- le seul moteur du projet, celui qui sert
deja au decoupage des clips.

VERBATIM. La reconnaissance est lancee sans detecteur de voix (`vad_filter=
False`). Le detecteur ecarte les zones jugees muettes AVANT la reconnaissance :
c'est utile pour chercher des passages a clipper, c'est inacceptable pour une
retranscription integrale -- un mot prononce dans une zone mal jugee
disparaitrait du texte sans que rien ne le signale. Le texte rendu est celui
que Whisper a reconnu, mot pour mot : rien n'est resume, reformule, corrige ni
filtre par ce module.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.models import Transcript
from transcription import cache as transcript_cache
from transcription.whisper_engine import transcribe as whisper_transcribe
from utils.errors import RightsNotConfirmedError, YouTubeDownloadError
from video.audio_extractor import extract_audio
from video.ffmpeg_utils import FFMPEG_BIN
from voice_studio.youtube_source import explain_error, watch_url

logger = get_logger()

# Formats produits par yt-dlp pour une piste audio seule.
AUDIO_EXTENSIONS = (".m4a", ".webm", ".opus", ".mp3", ".mp4", ".mka", ".wav")

RIGHTS_NOTICE = (
    "Voice Studio récupère la piste audio de la vidéo pour la transcrire sur ton "
    "ordinateur. Récupérer un média YouTube autrement que par le bouton officiel "
    "de YouTube va à l'encontre de ses conditions d'utilisation, quelle que soit "
    "la licence affichée. N'utilise cette fonction que pour un contenu que tu as "
    "le droit de traiter : le tien, ou un contenu dont tu as l'autorisation."
)


def download_audio(video_id: str, out_dir: str, consent_confirmed: bool,
                   on_progress: Optional[Callable[[Optional[float], str], None]] = None,
                   cancel_token: Optional[CancelToken] = None,
                   ydl_factory=None) -> str:
    """Piste audio seule de la video.

    Audio seul et non video complete : c'est tout ce dont la transcription a
    besoin, c'est dix fois plus leger, et cela evite de rapatrier une image
    dont personne ne fera rien ici.
    """
    if not consent_confirmed:
        raise RightsNotConfirmedError(RIGHTS_NOTICE)

    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError as error:
            raise YouTubeDownloadError(
                "yt-dlp n'est pas installé. Lance : pip install -r requirements.txt"
            ) from error
        ydl_factory = yt_dlp.YoutubeDL

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    def _hook(status: dict) -> None:
        # Seul endroit ou l'on repasse pendant un transfert : c'est donc ici
        # que l'annulation est vue. Lever une exception interrompt yt-dlp.
        if cancel_token is not None:
            cancel_token.check()
        if on_progress is None:
            return
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            done = status.get("downloaded_bytes") or 0
            fraction = (done / total) if total else None
            done_mb = done / 1_000_000
            label = (f"téléchargement de l'audio : {done_mb:.0f} / {total / 1_000_000:.0f} Mo"
                     if total else f"téléchargement de l'audio : {done_mb:.0f} Mo")
            on_progress(fraction, label)
        elif status.get("status") == "finished":
            on_progress(1.0, "audio récupéré")

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(Path(out_dir) / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "progress_hooks": [_hook],
    }
    if Path(FFMPEG_BIN).exists():
        options["ffmpeg_location"] = str(Path(FFMPEG_BIN).resolve().parent)

    try:
        with ydl_factory(options) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=True) or {}
    except Exception as error:
        if isinstance(error, Exception) and error.__class__.__name__ == "CancelledError":
            raise
        raise YouTubeDownloadError(explain_error(str(error))) from error

    path = find_audio_file(out_dir, info.get("id") or video_id)
    if path is None:
        raise YouTubeDownloadError(
            "Le téléchargement s'est terminé sans erreur, mais aucun fichier audio "
            "n'a été trouvé. Réessaie ; si cela se reproduit, la vidéo n'expose "
            "peut-être aucune piste audio téléchargeable."
        )
    return str(path)


def find_audio_file(out_dir: str, video_id: str) -> Path | None:
    directory = Path(out_dir)
    for extension in AUDIO_EXTENSIONS:
        candidate = directory / f"{video_id}{extension}"
        if candidate.is_file():
            return candidate
    matches = sorted(directory.glob(f"{video_id}.*"))
    return matches[0] if matches else None


def transcribe_media(media_path: str, model: str, language: str | None, device: str = "auto",
                     cancel_token: Optional[CancelToken] = None,
                     on_progress: Optional[Callable[[Optional[float], str], None]] = None,
                     use_cache: bool = True) -> Transcript:
    """Transcription integrale d'un fichier audio ou video local.

    Le cache de transcription du projet est reutilise tel quel : rouvrir la
    meme video avec le meme modele ne relance pas le calcul. La cle du cache
    porte deja le modele et la langue, donc changer l'un des deux retranscrit.
    """
    if use_cache:
        cached = transcript_cache.load(media_path, model, language)
        if cached is not None:
            logger.info("Voice Studio : transcription reprise du cache.")
            if on_progress:
                on_progress(1.0, "transcription déjà disponible")
            return cached

    with tempfile.TemporaryDirectory(prefix="voice_studio_") as temporary:
        wav_path = str(Path(temporary) / "audio_16k_mono.wav")
        if on_progress:
            on_progress(None, "préparation de l'audio")
        extract_audio(media_path, wav_path)

        def _segments(elapsed: float, total: float) -> None:
            if on_progress and total > 0:
                on_progress(min(elapsed / total, 0.999),
                            f"transcription : {elapsed:.0f} s / {total:.0f} s")

        def _download(done_mb: float, total_mb) -> None:
            # Le premier argument est un NOMBRE DE MEGAOCTETS, pas une fraction.
            if on_progress:
                if total_mb:
                    on_progress(min(done_mb / total_mb, 0.999),
                                f"téléchargement du modèle Whisper : "
                                f"{done_mb:.0f} / {total_mb:.0f} Mo")
                else:
                    on_progress(None, f"téléchargement du modèle Whisper : {done_mb:.0f} Mo")

        transcript = whisper_transcribe(
            wav_path, model, language, device,
            cancel_token=cancel_token,
            on_segment_progress=_segments,
            on_download_progress=_download,
            # Retranscription integrale : aucun filtrage avant reconnaissance.
            vad_filter=False,
        )

    if use_cache:
        try:
            transcript_cache.save(media_path, model, language, transcript)
        except OSError as error:                       # pragma: no cover - disque plein
            logger.warning(f"Transcription non mise en cache : {error}")
    return transcript
