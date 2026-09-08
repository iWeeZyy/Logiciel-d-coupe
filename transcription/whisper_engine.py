"""Wrapper autour de faster-whisper (CTranslate2) -- transcription 100% locale.

Choisi plutot que openai-whisper : pas de dependance PyTorch, quantification
int8 native sur CPU, ~4x plus rapide a qualite egale (voir etape 1/2 du design).
Le modele est telecharge une seule fois (cache Hugging Face local, ~/.cache/huggingface)
puis reutilise hors ligne.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.models import Segment, Transcript, Word
from utils.errors import ModelDownloadError, NoAudioError
from utils.hardware import resolve_device

logger = get_logger()

_VALID_MODELS = {"tiny", "base", "small", "medium", "large-v2", "large-v3", "large"}

# Signes d'un cache Hugging Face incomplet plutot que d'un vrai probleme reseau :
# le dossier du modele existe, mais le poids lui-meme n'y est pas. C'est ce que
# laisse un telechargement interrompu (coupure reseau, fenetre fermee, disque
# plein) -- large-v3 pese environ 3 Go, la fenetre pour etre coupe est large.
_INCOMPLETE_CACHE_MARKERS = (
    "unable to open file",
    "no such file",
    "model.bin",
    "does not exist",
)


# Poids approximatif du telechargement de chaque modele, en mega-octets (depots
# Systran/faster-whisper-*). Sert uniquement a verifier l'espace disque AVANT de
# lancer un telechargement de plusieurs gigaoctets : un disque plein en cours de
# route laisse justement le cache incomplet repare plus bas, et l'utilisateur
# perd le temps du telechargement pour rien.
_MODEL_DOWNLOAD_MB = {
    "tiny": 75,
    "base": 145,
    "small": 484,
    "medium": 1530,
    "large": 3090,
    "large-v2": 3090,
    "large-v3": 3090,
}

# Marge au-dessus de la taille annoncee : le fichier est telecharge sous
# ".incomplete" puis DEPLACE dans le cache (pas copie -- verifie dans
# huggingface_hub/file_download.py), il n'y a donc pas de doublement temporaire,
# seulement les fichiers annexes (tokenizer, config) et un peu de jeu.
_DISK_HEADROOM = 1.15


def free_disk_mb(path: Path) -> float | None:
    """Espace libre, en Mo, sur le volume qui contiendra `path`.

    On remonte au premier parent existant : le dossier du cache lui-meme
    n'existe pas encore au premier telechargement. None si la mesure echoue --
    auquel cas on n'empeche jamais un telechargement sur un simple doute."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / (1024 * 1024)
    except OSError:
        return None


def check_disk_space_for(model_name: str, cache_dir: Path) -> None:
    """Refuse tot un telechargement qui ne tient manifestement pas sur le disque.

    Ne dit rien pour un modele de taille inconnue ou une mesure impossible :
    bloquer sur une estimation incertaine serait pire que laisser essayer."""
    needed_mb = _MODEL_DOWNLOAD_MB.get(model_name)
    if needed_mb is None:
        return
    free_mb = free_disk_mb(cache_dir)
    if free_mb is None or free_mb >= needed_mb * _DISK_HEADROOM:
        return
    raise ModelDownloadError(
        f"Espace disque insuffisant pour telecharger le modele Whisper '{model_name}' : "
        f"il faut environ {needed_mb / 1024:.1f} Go libres et il en reste "
        f"{free_mb / 1024:.1f} sur le disque qui heberge\n{cache_dir.parent}\n\n"
        "Libere de la place, choisis un modele plus petit, ou deplace le cache sur "
        "un autre disque en definissant la variable d'environnement HF_HOME "
        "(par exemple HF_HOME=E:\\huggingface)."
    )


def hf_hub_cache_root() -> Path:
    """Racine du cache Hugging Face reellement utilisee par la bibliotheque.

    On lit la constante de huggingface_hub plutot que de reconstruire
    ~/.cache/huggingface/hub a la main : le dossier est deplacable par les
    variables d'environnement HF_HUB_CACHE, HUGGINGFACE_HUB_CACHE, HF_HOME ou
    XDG_CACHE_HOME (utile quand le disque systeme est trop plein pour un modele
    de plusieurs gigaoctets). Reconstruire le chemin nous ferait alors chercher,
    et surtout SUPPRIMER, dans un dossier qui n'est pas celui que la
    bibliotheque utilise. Le repli ne sert que si l'import echoue.
    """
    try:
        from huggingface_hub import constants as hf_constants

        return Path(hf_constants.HF_HUB_CACHE)
    except Exception:
        return Path.home() / ".cache" / "huggingface" / "hub"


def hf_cache_dir_for(model_name: str) -> Path:
    """Dossier du cache Hugging Face correspondant a un modele faster-whisper.

    faster-whisper resout un nom court ("large-v3") en depot
    "Systran/faster-whisper-large-v3", que huggingface_hub range sous
    "models--Systran--faster-whisper-large-v3". On reconstruit ce nom de dossier
    pour pouvoir NOMMER a l'utilisateur le dossier a supprimer, au lieu de le
    laisser deviner ou se trouve un cache qu'il n'a jamais cree lui-meme."""
    return hf_hub_cache_root() / f"models--Systran--faster-whisper-{model_name}"


def _looks_like_incomplete_download(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _INCOMPLETE_CACHE_MARKERS)


def _load_model(model_name: str, device: str, compute_type: str):
    """Charge le modele, en reparant une seule fois un cache incomplet.

    Un telechargement interrompu laisse un dossier de modele sans son fichier de
    poids, et huggingface_hub le considere alors comme deja present : il ne
    retelecharge jamais rien et l'application echoue a chaque lancement. Le seul
    moyen d'en sortir est de supprimer ce dossier -- on le fait donc nous-memes,
    une fois, plutot que de demander a l'utilisateur d'aller fouiller dans un
    cache qu'il n'a jamais cree.
    """
    from faster_whisper import WhisperModel

    cache_dir = hf_cache_dir_for(model_name)
    if not cache_dir.exists():
        check_disk_space_for(model_name, cache_dir)

    try:
        return WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as first_error:
        if not (_looks_like_incomplete_download(first_error) and cache_dir.exists()):
            raise

        logger.warning(
            f"Le modele '{model_name}' est present dans le cache mais incomplet "
            f"(telechargement interrompu). Suppression de {cache_dir} et nouvelle tentative."
        )
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
        except OSError as cleanup_error:
            raise ModelDownloadError(
                f"Le modele Whisper '{model_name}' est incomplet dans le cache et n'a pas pu "
                f"etre supprime automatiquement. Supprime ce dossier a la main puis relance :\n"
                f"{cache_dir}\nDetail : {cleanup_error}"
            ) from first_error

        check_disk_space_for(model_name, cache_dir)
        try:
            return WhisperModel(model_name, device=device, compute_type=compute_type)
        except Exception as second_error:
            raise ModelDownloadError(
                f"Le modele Whisper '{model_name}' n'a pas pu etre telecharge, meme apres "
                f"nettoyage du cache. Verifie ta connexion internet et l'espace disque "
                f"disponible ({'environ 3 Go' if 'large' in model_name else 'quelques centaines de Mo'} "
                f"necessaires), ou choisis un modele plus petit.\nDetail : {second_error}"
            ) from second_error


def transcribe(
    wav_path: str,
    model_name: str,
    language: str | None,
    device_pref: str,
    cancel_token: Optional[CancelToken] = None,
    on_segment_progress: Optional[Callable[[float, float], None]] = None,
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

    if device == "cpu" and ("large" in model_name or model_name == "medium"):
        logger.warning(
            f"Modele '{model_name}' sur processeur : la transcription peut durer plus longtemps "
            "que la video elle-meme. Le modele 'small' est nettement plus rapide pour une "
            "qualite tres correcte."
        )

    try:
        model = _load_model(model_name, device, compute_type)
    except ModelDownloadError:
        raise
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
    total_duration = float(info.duration) or 0.0
    for i, seg in enumerate(segments_iter):
        if cancel_token is not None:
            cancel_token.check()
        if on_segment_progress is not None and total_duration > 0:
            on_segment_progress(float(seg.end), total_duration)

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
