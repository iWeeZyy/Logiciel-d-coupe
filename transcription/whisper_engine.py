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
from core.paths import user_data_dir
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


# Le plus petit modele reel (tiny) pese environ 72 Mo. Un model.bin en dessous
# de ce seuil n'est pas un modele : c'est un fichier tronque, laisse par un
# telechargement coupe ou une copie interrompue. Verifier la seule PRESENCE du
# fichier laissait passer ce cas, et le chargement echouait ensuite avec un
# message de bibliotheque incomprehensible.
MIN_MODEL_BIN_BYTES = 10 * 1024 * 1024


def model_bin_size(snapshot_path: str | Path) -> int:
    """Taille du fichier de poids, 0 s'il est absent ou illisible.

    Un lien symbolique casse -- ce que laisse un blob supprime alors que
    l'instantane reste -- rend 0 lui aussi : `is_file()` suit le lien et repond
    False, et c'est bien ce qu'on veut.
    """
    try:
        target = Path(snapshot_path) / "model.bin"
        return target.stat().st_size if target.is_file() else 0
    except OSError:
        return 0


def snapshot_is_complete(snapshot_path: str | Path) -> bool:
    """Un instantane sans fichier de poids utilisable est un telechargement raté.

    C'est exactement l'etat que laisse une application fermee pendant le
    telechargement : huggingface_hub voit le dossier, le considere comme deja
    la, et ne retelecharge plus jamais rien. Le detecter AVANT de charger le
    modele evite de dependre du texte d'un message d'erreur."""
    return model_bin_size(snapshot_path) >= MIN_MODEL_BIN_BYTES


def describe_snapshot(snapshot_path: str | Path | None) -> str:
    """Etat reel du modele sur le disque, en clair.

    Sert dans les messages d'erreur. Sans ces faits, un echec de chargement ne
    dit pas s'il manque le fichier, s'il est tronque, ou s'il n'y a plus de
    place -- trois problemes qui n'ont pas la meme reponse.
    """
    if not snapshot_path:
        return "aucun dossier de modèle trouvé dans le cache"
    path = Path(snapshot_path)
    size = model_bin_size(path)
    free = free_disk_mb(path if path.exists() else path.parent)
    place = f", {free:.0f} Mo libres sur ce disque" if free is not None else ""
    if size == 0:
        return f"le fichier model.bin est absent de {path}{place}"
    if size < MIN_MODEL_BIN_BYTES:
        return (f"le fichier model.bin de {path} ne fait que {size / (1024 * 1024):.1f} Mo : "
                f"il est tronqué{place}")
    return f"model.bin fait {size / (1024 * 1024):.0f} Mo dans {path}{place}"


def plain_model_dir(model_name: str) -> Path:
    """Dossier simple ou telecharger un modele quand le cache Hugging Face pose
    probleme.

    Le cache normal range les fichiers dans `blobs/` et fabrique des liens dans
    `snapshots/`. Sous Windows, creer un lien symbolique demande des droits que
    l'utilisateur n'a en general pas : la bibliotheque recopie alors le fichier,
    ce qui demande deux fois la place le temps de la copie et laisse un fichier
    a moitie ecrit quand elle echoue. Un dossier simple n'a ni blobs ni liens :
    le fichier est ecrit une fois, a sa place definitive.
    """
    return user_data_dir() / "whisper-models" / model_repo_id(model_name).replace("/", "--")


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
    output_dir: Optional[Path] = None,
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
            if output_dir is not None:
                download_model(model_name, output_dir=str(output_dir))
            else:
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
) -> str:
    """Garantit un modele utilisable, et renvoie ce qu'il faut charger.

    La valeur renvoyee est le nom du modele quand le cache Hugging Face est
    sain, ou le CHEMIN d'un dossier simple quand il ne l'est pas. Le point
    important est la : la fonction ne se contente plus de lancer un
    telechargement et de supposer qu'il a marche, elle REGARDE le resultat.
    C'est ce qui manquait -- un telechargement pouvait se terminer sans erreur
    en laissant un instantane sans fichier de poids, et l'echec n'apparaissait
    qu'au chargement, sous la forme d'un message de bibliotheque.
    """
    cached = _cached_snapshot_path(model_name)
    if cached is not None and snapshot_is_complete(cached):
        return model_name

    plain = plain_model_dir(model_name)
    if snapshot_is_complete(plain):
        logger.info(f"Modele '{model_name}' utilise depuis {plain}.")
        return str(plain)

    cache_dir = hf_cache_dir_for(model_name)
    if cached is not None:
        logger.warning(
            f"Le modele '{model_name}' est present dans le cache mais inutilisable "
            f"({describe_snapshot(cached)}). Suppression de {cache_dir} et "
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
    try:
        _download_with_progress(model_name, cache_dir, on_download_progress, cancel_token)
    except (CancelledError, ModelDownloadError):
        raise
    except Exception as error:
        logger.warning(f"Telechargement via le cache Hugging Face echoue : {error}")
    else:
        downloaded = _cached_snapshot_path(model_name)
        if downloaded is not None and snapshot_is_complete(downloaded):
            return model_name
        logger.warning(
            "Le telechargement s'est termine sans erreur mais le modele reste "
            f"inutilisable : {describe_snapshot(downloaded or cache_dir)}."
        )

    # Repli : un dossier simple, sans blobs ni liens symboliques. C'est la seule
    # facon de sortir d'un cache que la bibliotheque croit valide alors qu'il ne
    # l'est pas, et cela evite aussi la copie en double que Windows impose quand
    # il ne peut pas creer de lien.
    logger.info(f"Nouvelle tentative dans un dossier simple : {plain}")
    plain.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(plain, ignore_errors=True)
    check_disk_space_for(model_name, plain.parent)
    _download_with_progress(model_name, plain, on_download_progress, cancel_token,
                            output_dir=plain)
    if not snapshot_is_complete(plain):
        raise ModelDownloadError(
            f"Le modele Whisper '{model_name}' n'a pas pu etre telecharge, ni dans le "
            f"cache Hugging Face, ni dans un dossier simple.\n\n"
            f"Cache : {describe_snapshot(_cached_snapshot_path(model_name) or cache_dir)}\n"
            f"Dossier simple : {describe_snapshot(plain)}\n\n"
            "Verifie l'espace disponible sur ce disque et ta connexion internet, ou "
            "choisis un modele plus petit dans les Parametres."
        )
    return str(plain)


def _looks_like_incomplete_download(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _INCOMPLETE_CACHE_MARKERS)


def _load_model(target: str, device: str, compute_type: str, model_name: str | None = None):
    """Charge le modele, avec un dernier recours si le fichier est illisible.

    ensure_model_downloaded a deja garanti un fichier de poids de taille
    plausible. Il peut malgre tout etre corrompu -- une copie interrompue, un
    secteur abime, un antivirus qui a tronque le fichier. Dans ce cas, le seul
    recours utile est de le retelecharger ailleurs, dans un dossier simple, et
    non de reessayer au meme endroit.
    """
    from faster_whisper import WhisperModel

    # `target` peut etre un chemin ; le nom court reste necessaire pour savoir
    # quel modele retelecharger et ou.
    model_name = model_name or target

    try:
        return WhisperModel(target, device=device, compute_type=compute_type)
    except Exception as first_error:
        if not _looks_like_incomplete_download(first_error):
            raise

        plain = plain_model_dir(model_name)
        logger.warning(
            f"Le modele '{model_name}' est present mais illisible ({first_error}). "
            f"Retelechargement complet dans {plain}."
        )
        shutil.rmtree(plain, ignore_errors=True)
        if Path(target) != plain:
            shutil.rmtree(hf_cache_dir_for(model_name), ignore_errors=True)

        try:
            plain.parent.mkdir(parents=True, exist_ok=True)
            check_disk_space_for(model_name, plain.parent)
            _download_with_progress(model_name, plain, None, None, output_dir=plain)
            return WhisperModel(str(plain), device=device, compute_type=compute_type)
        except (CancelledError, ModelDownloadError):
            raise
        except Exception as second_error:
            raise ModelDownloadError(
                f"Le modele Whisper '{model_name}' reste inutilisable apres un "
                f"telechargement complet.\n\n"
                f"Etat du fichier : {describe_snapshot(plain)}\n\n"
                f"Ce modele represente "
                f"{'environ 3 Go' if 'large' in model_name else 'quelques centaines de Mo'} "
                "a telecharger : verifie l'espace disque disponible sur ce disque, et "
                "qu'aucun antivirus ne bloque l'ecriture dans ce dossier. Un modele plus "
                "petit (Parametres -> modele) demande moins de place.\n"
                f"Detail : {second_error}"
            ) from second_error


def transcribe(
    wav_path: str,
    model_name: str,
    language: str | None,
    device_pref: str,
    cancel_token: Optional[CancelToken] = None,
    on_segment_progress: Optional[Callable[[float, float], None]] = None,
    on_download_progress: Optional[Callable[[float, Optional[float]], None]] = None,
    vad_filter: bool = True,
) -> Transcript:
    """Transcrit wav_path (mono 16kHz, produit par video/audio_extractor.py).

    Renvoie un Transcript avec segments + mots horodates. La detection de langue
    est automatique si language est None (comportement natif de Whisper).

    `vad_filter` : le detecteur de voix ecarte les zones jugees sans parole
    AVANT la reconnaissance. C'est ce qu'il faut pour decouper des clips -- on
    y cherche des passages parles, et sauter les blancs fait gagner du temps.
    Ce n'est PAS ce qu'il faut pour une retranscription integrale : un mot
    prononce dans une zone jugee muette disparait du texte sans que rien ne le
    signale. Voice Studio le met donc a False. Le defaut reste True, le
    comportement du pipeline video ne change pas.
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
        target = ensure_model_downloaded(model_name, on_download_progress, cancel_token)
        model = _load_model(target, device, compute_type, model_name)
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
        options = {
            "language": language,
            "word_timestamps": True,
            "vad_filter": bool(vad_filter),
        }
        if vad_filter:
            options["vad_parameters"] = {"min_silence_duration_ms": 500}
        segments_iter, info = model.transcribe(wav_path, **options)
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
