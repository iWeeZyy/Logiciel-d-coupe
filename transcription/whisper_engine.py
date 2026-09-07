"""Wrapper autour de faster-whisper (CTranslate2) -- transcription 100% locale.

Choisi plutot que openai-whisper : pas de dependance PyTorch, quantification
int8 native sur CPU, ~4x plus rapide a qualite egale (voir etape 1/2 du design).
Le modele est telecharge une seule fois (cache Hugging Face local, ~/.cache/huggingface)
puis reutilise hors ligne.
"""
from __future__ import annotations

from core.logging_setup import get_logger
from core.models import Segment, Transcript, Word
from utils.errors import ModelDownloadError, NoAudioError
from utils.hardware import resolve_device

logger = get_logger()

_VALID_MODELS = {"tiny", "base", "small", "medium", "large-v2", "large-v3", "large"}


def transcribe(
    wav_path: str,
    model_name: str,
    language: str | None,
    device_pref: str,
) -> Transcript:
    """Transcrit wav_path (mono 16kHz, produit par video/audio_extractor.py).

    Renvoie un Transcript avec segments + mots horodates. La detection de langue
    est automatique si language est None (comportement natif de Whisper).
    """
    if model_name not in _VALID_MODELS:
        logger.warning(
            f"Modele Whisper '{model_name}' non reconnu parmi {sorted(_VALID_MODELS)} -- "
            "tentative de chargement quand meme (nom Hugging Face personnalise ?)."
        )

    device, compute_type = resolve_device(device_pref)

    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ModelDownloadError(
            "faster-whisper n'est pas installe. Lance : pip install -r requirements.txt"
        ) from e

    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        raise ModelDownloadError(
            f"Impossible de charger/telecharger le modele Whisper '{model_name}'. "
            "Verifie ta connexion internet (necessaire uniquement au premier "
            f"telechargement) ou choisis un autre --model. Detail : {e}"
        ) from e

    logger.info(f"Transcription en cours (modele={model_name}, device={device}, compute_type={compute_type})...")

    try:
        segments_iter, info = model.transcribe(
            wav_path,
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
    except Exception as e:
        raise NoAudioError(
            f"Echec de la transcription -- l'audio extrait est peut-etre vide ou "
            f"illisible. Detail : {e}"
        ) from e

    segments: list[Segment] = []
    total_words = 0
    for i, seg in enumerate(segments_iter):
        words = []
        if seg.words:
            for w in seg.words:
                words.append(Word(text=w.word.strip(), start=w.start, end=w.end, probability=w.probability))
                total_words += 1
        segments.append(
            Segment(
                id=i,
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
                words=words,
                avg_logprob=seg.avg_logprob,
                no_speech_prob=seg.no_speech_prob,
            )
        )

    if total_words == 0:
        logger.warning(
            "Aucun mot horodate n'a ete produit par Whisper -- l'audio est peut-etre "
            "silencieux ou de tres mauvaise qualite. Les sous-titres mot-par-mot et "
            "certains scores textuels seront vides."
        )

    full_text = " ".join(s.text for s in segments).strip()

    transcript = Transcript(
        language=info.language,
        language_probability=float(info.language_probability),
        duration=float(info.duration),
        full_text=full_text,
        segments=segments,
    )
    logger.info(
        f"Transcription terminee : langue={transcript.language} "
        f"(confiance={transcript.language_probability:.2f}), "
        f"{len(segments)} segments, {total_words} mots."
    )
    return transcript
