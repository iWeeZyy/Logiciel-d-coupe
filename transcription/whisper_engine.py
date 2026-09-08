"""Wrapper autour de faster-whisper (CTranslate2) -- transcription 100% locale.

Choisi plutot que openai-whisper : pas de dependance PyTorch, quantification
int8 native sur CPU, ~4x plus rapide a qualite egale (voir etape 1/2 du design).
Le modele est telecharge une seule fois (cache Hugging Face local, ~/.cache/huggingface)
puis reutilise hors ligne.
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.models import Segment, Transcript, Word
from utils.errors import CancelledError, ModelDownloadError, NoAudioError
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


def model_repo_id(model_name: str) -> str:
    """Depot Hugging Face reellement utilise par faster-whisper pour ce modele.

    La correspondance n'est pas mecanique : "large" pointe sur
    Systran/faster-whisper-large-v3, pas sur un depot "...-large". Deduire le
    nom du dossier de cache du nom du modele nous faisait donc chercher un
    dossier inexistant pour "large" -- et la reparation d'un cache incomplet ne
    s'y serait jamais declenchee. On lit la table de faster-whisper.
    """
    if "/" in model_name:
        return model_name
    try:
        from faster_whisper.utils import _MODELS

        repo = _MODELS.get(model_name)
        if repo:
            return repo
    except Exception:
        pass
    return f"Systran/faster-whisper-{model_name}"


def hf_cache_dir_for(model_name: str) -> Path:
    """Dossier du cache Hugging Face correspondant a un modele faster-whisper.

    faster-whisper resout un nom court ("large-v3") en depot
    "Systran/faster-whisper-large-v3", que huggingface_hub range sous
    "models--Systran--faster-whisper-large-v3". On reconstruit ce nom de dossier
    pour pouvoir NOMMER a l'utilisateur le dossier a supprimer, au lieu de le
    laisser deviner ou se trouve un cache qu'il n'a jamais cree lui-meme."""
    return hf_hub_cache_root() / ("models--" + model_repo_id(model_name).replace("/", "--"))


def _cached_snapshot_path(model_name: str) -> Optional[str]:
    """Chemin du modele deja present dans le cache, ou None s'il n'y est pas."""
    try:
        from faster_whisper.utils import download_model

        return download_model(model_name, local_files_only=True)
    except Exception:
        return None


def snapshot_is_complete(snapshot_path: str | Path) -> bool:
    """Un instantane sans son fichier de poids est un telechargement interrompu.

    C'est exactement l'etat que laisse une application fermee pendant le
    telechargement : huggingface_hub voit le dossier, le considere comme deja
    la, et ne retelecharge plus jamais rien. Le detecter AVANT de charger le
    modele evite de dependre du texte d'un message d'erreur."""
    return (Path(snapshot_path) / "model.bin").is_file()


def _dir_size_mb(path: Path) -> float:
    total = 0
    if not path.exists():
        return 0.0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total / (1024 * 1024)


def _download_with_progress(
    model_name: str,
    cache_dir: Path,
    on_download_progress: Optional[Callable[[float, Optional[float]], None]],
    cancel_token: Optional[CancelToken] = None,
) -> None:
    """Telecharge le modele en rendant compte de l'avancement.

    Sans ce compte rendu, l'interface reste figee sur "Transcription" pendant
    tout le telechargement -- plusieurs minutes pour 484 Mo, beaucoup plus pour
    3 Go -- et fermer une application qui semble bloquee est la reaction
    naturelle. C'est precisement ce qui laisse le cache incomplet.

    huggingface_hub n'expose pas de rappel d'avancement exploitable ici : on
    telecharge donc dans un fil et on mesure la taille reellement ecrite sur le
    disque. C'est approximatif -- la barre peut avancer par a-coups -- mais
    honnete : ce sont les octets vraiment arrives.

    Ce meme fil donne a "Annuler" un point de controle pendant le
    telechargement. Sans lui, le telechargement est un appel tiers bloquant sans
    aucun point d'arret : le jeton cooperatif restait sans effet et l'interface
    finissait par recourir a QThread.terminate(), qui coupe l'ecriture du
    fichier de poids n'importe ou -- exactement le cache incomplet que ce module
    passe son temps a reparer.
    """
    from faster_whisper.utils import download_model

    total_mb = _MODEL_DOWNLOAD_MB.get(model_name)
    already_mb = _dir_size_mb(cache_dir)
    outcome: dict[str, BaseException | None] = {"error": None}

    def worker() -> None:
        try:
            download_model(model_name)
        except BaseException as exc:  # remonte tel quel dans le fil principal
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="whisper-model-download", daemon=True)
    thread.start()

    while thread.is_alive():
        thread.join(timeout=0.5)
        if cancel_token is not None and cancel_token.is_cancelled:
            # Le fil de telechargement n'est pas interrompu : huggingface_hub
            # ecrit dans un fichier ".incomplete" qu'il sait reprendre, et le
            # tuer en pleine ecriture est precisement ce qu'on cherche a eviter.
            # On rend simplement la main tout de suite a l'interface.
            logger.info(
                "Annulation demandee pendant le telechargement du modele -- "
                "le telechargement se poursuit en arriere-plan et sera repris "
                "au prochain lancement."
            )
            cancel_token.check()
        if on_download_progress is None:
            continue
        downloaded = max(0.0, _dir_size_mb(cache_dir) - already_mb)
        on_download_progress(downloaded, float(total_mb) if total_mb else None)

    if outcome["error"] is not None:
        raise outcome["error"]


def ensure_model_downloaded(
    model_name: str,
    on_download_progress: Optional[Callable[[float, Optional[float]], None]] = None,
    cancel_token: Optional[CancelToken] = None,
) -> None:
    """Garantit un modele complet dans le cache avant tout chargement.

    Trois cas, dans cet ordre : deja complet -> rien a faire ; present mais sans
    son fichier de poids -> le dossier est supprime puis retelecharge (sans
    quoi huggingface_hub le croira eternellement present) ; absent -> verification
    de l'espace disque puis telechargement avec avancement.
    """
    cached = _cached_snapshot_path(model_name)
    if cached is not None and snapshot_is_complete(cached):
        return

    cache_dir = hf_cache_dir_for(model_name)
    if cached is not None:
        logger.warning(
            f"Le modele '{model_name}' est present dans le cache mais son fichier de poids "
            f"manque (telechargement interrompu). Suppression de {cache_dir} et "
            "nouveau telechargement."
        )
        shutil.rmtree(cache_dir, ignore_errors=True)

    check_disk_space_for(model_name, cache_dir)
    size = _MODEL_DOWNLOAD_MB.get(model_name)
    logger.info(
        f"Telechargement du modele Whisper '{model_name}'"
        + (f" (~{size} Mo)" if size else "")
        + " -- une seule fois, il sera ensuite reutilise hors ligne."
    )
    _download_with_progress(model_name, cache_dir, on_download_progress, cancel_token)


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
    on_download_progress: Optional[Callable[[float, Optional[float]], None]] = None,
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
        ensure_model_downloaded(model_name, on_download_progress, cancel_token)
        model = _load_model(model_name, device, compute_type)
    except (ModelDownloadError, CancelledError):
        # Une annulation demandee par l'utilisateur n'est pas un echec de
        # telechargement : la transformer en ModelDownloadError afficherait un
        # message d'erreur reseau a quelqu'un qui vient d'appuyer sur Annuler.
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
