"""L'environnement Python separe dans lequel Chatterbox tourne.

POURQUOI SEPARE, ET PAS DANS L'APPLICATION. Chatterbox epingle des versions
EXACTES : torch==2.6.0, torchaudio==2.6.0, transformers==5.2.0,
diffusers==0.29.0, librosa==0.11.0. Les installer a cote du reste figerait tout
le projet sur ces versions, et ajouterait pres de 200 Mo de roue PyTorch a
l'installateur pour tout le monde -- y compris ceux qui n'utiliseront jamais
Chatterbox. L'application n'a aujourd'hui aucune dependance PyTorch : la
transcription passe par CTranslate2, c'est un choix documente.

D'ou cet environnement, cree A LA DEMANDE, dans les donnees de l'application,
et utilise par un processus separe (voice_studio/chatterbox_worker.py). Trois
consequences qui valent la peine :

- l'application demarre et fonctionne sans lui, exactement comme avant ;
- une casse de son cote (mauvaise version, roue corrompue) ne peut pas empecher
  Faster-Whisper ou Piper de fonctionner ;
- il se supprime en effacant un dossier.

CE MODULE NE TELECHARGE RIEN TOUT SEUL. Chaque fonction qui installe demande
une action explicite de l'appelant, qui vient elle-meme d'un bouton.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.paths import app_base_dir, user_data_dir
from utils.errors import CancelledError
from voice_studio import chatterbox_catalogue as catalogue

logger = get_logger()

MARKER = "installed.json"


class ChatterboxRuntimeError(Exception):
    """Message deja ecrit pour un humain."""


@dataclass(frozen=True)
class PythonCandidate:
    executable: str
    version: tuple


def runtime_dir() -> Path:
    try:
        from gui import settings_store

        configured = settings_store.get("chatterbox_runtime_dir")
    except Exception:                                  # pragma: no cover - reglages illisibles
        configured = None
    if configured:
        return Path(configured)
    return user_data_dir() / "voice_studio_data" / "chatterbox_runtime"


def python_executable() -> Path:
    """Interpreteur de l'environnement, selon le systeme."""
    directory = runtime_dir()
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def worker_script() -> Path:
    """Chemin du programme execute dans l'environnement.

    Compilee, l'application n'a plus de fichier .py sur le disque : le worker
    est donc livre comme donnee a cote de l'executable (voir le .spec). On
    cherche la, puis a cote de ce module -- l'ordre importe, le second n'existe
    qu'en developpement.
    """
    packaged = app_base_dir() / "voice_studio" / "chatterbox_worker.py"
    if packaged.is_file():
        return packaged
    return Path(__file__).with_name("chatterbox_worker.py")


def marker_path() -> Path:
    return runtime_dir() / MARKER


def installed_commit() -> str:
    try:
        return json.loads(marker_path().read_text(encoding="utf-8")).get("commit", "")
    except (OSError, ValueError):
        return ""


def is_ready() -> bool:
    """Environnement utilisable : l'interpreteur existe ET le marqueur
    correspond au commit demande par le catalogue."""
    if not python_executable().is_file():
        return False
    wanted = str(catalogue.runtime_spec().get("commit") or "")
    return not wanted or installed_commit() == wanted


def is_outdated() -> bool:
    """Installe, mais pas au commit que le catalogue demande."""
    return python_executable().is_file() and not is_ready()


# ------------------------------------------------------------ interpreteur

def _probe(executable: str) -> PythonCandidate | None:
    try:
        result = subprocess.run(
            [executable, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        version = tuple(int(p) for p in result.stdout.strip().split("."))
    except ValueError:
        return None
    return PythonCandidate(executable=executable, version=version)


def _acceptable(version: tuple) -> bool:
    spec = catalogue.runtime_spec()
    low = tuple(spec.get("python_min") or (3, 10))
    high = tuple(spec.get("python_max") or (3, 13))
    return low <= version[:2] <= high


def find_python(extra: str = "") -> PythonCandidate | None:
    """Un interpreteur du systeme capable d'accueillir Chatterbox.

    On ne prend PAS l'interpreteur de l'application quand elle est compilee :
    il n'a ni pip ni bibliotheque standard installable. En developpement, il
    fait l'affaire s'il a la bonne version.
    """
    import shutil

    candidates: list[str] = []
    if extra:
        candidates.append(extra)
    if not getattr(sys, "frozen", False):
        candidates.append(sys.executable)
    for name in ("python3.12", "python3.11", "python3.10", "python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if os.name == "nt":
        launcher = shutil.which("py")
        if launcher:
            candidates.append(launcher)

    seen = set()
    for executable in candidates:
        if executable in seen:
            continue
        seen.add(executable)
        candidate = _probe(executable)
        if candidate and _acceptable(candidate.version):
            return candidate
    return None


# --------------------------------------------------------------- execution

def _run(command: list[str], on_progress: Optional[Callable],
         cancel_token: Optional[CancelToken], label: str) -> None:
    """Lance une commande en rendant compte ligne par ligne, et annulable.

    L'annulation TUE le processus : pip ne s'arrete pas poliment, et laisser
    un telechargement de 200 Mo continuer apres un clic sur Annuler serait
    mentir sur ce que fait le bouton.
    """
    logger.info(f"Chatterbox runtime : {' '.join(command[:3])}…")
    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, creationflags=creation)
    tail: list[str] = []
    try:
        for line in process.stdout or []:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-40]
                if on_progress:
                    on_progress(label, line)
            if cancel_token is not None:
                try:
                    cancel_token.check()
                except CancelledError:
                    process.kill()
                    raise
    finally:
        if process.stdout:
            process.stdout.close()
        code = process.wait()

    if code != 0:
        detail = "\n".join(tail[-12:])
        raise ChatterboxRuntimeError(
            f"L'étape « {label} » a échoué (code {code}).\n\n{detail}")


def install(on_progress: Optional[Callable] = None,
            cancel_token: Optional[CancelToken] = None,
            base_python: str = "") -> Path:
    """Cree l'environnement et y installe Chatterbox. Action explicite."""
    spec = catalogue.runtime_spec()
    source = str(spec.get("source") or "")
    if not source:
        raise ChatterboxRuntimeError(
            "Le catalogue (config/chatterbox.json) n'indique aucune source pour "
            "Chatterbox : rien ne peut être installé.")

    candidate = find_python(base_python)
    if candidate is None:
        low = ".".join(str(p) for p in (spec.get("python_min") or (3, 10)))
        high = ".".join(str(p) for p in (spec.get("python_max") or (3, 13)))
        raise ChatterboxRuntimeError(
            f"Aucun Python {low} à {high} n'a été trouvé sur cet ordinateur.\n\n"
            "Chatterbox a besoin d'un Python installé à côté de l'application pour "
            "créer son environnement. Installe-le depuis python.org (coche « Add "
            "python.exe to PATH »), puis relance cette installation.")

    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    marker_path().unlink(missing_ok=True)

    if not python_executable().is_file():
        _run([candidate.executable, "-m", "venv", str(directory)],
             on_progress, cancel_token, "création de l'environnement")

    python = str(python_executable())
    _run([python, "-m", "pip", "install", "--upgrade", "pip", "--no-input"],
         on_progress, cancel_token, "mise à jour de pip")

    packages = [str(p) for p in (spec.get("packages") or [])]
    if packages:
        command = [python, "-m", "pip", "install", "--no-input", *packages]
        index = str(spec.get("torch_index_url") or "")
        if index:
            # Index PyTorch pour la variante PROCESSEUR : la roue par defaut
            # de PyPI convient aussi, mais cet index est celui que PyTorch
            # documente pour choisir explicitement le CPU.
            command += ["--index-url", index]
        _run(command, on_progress, cancel_token, "installation de PyTorch")

    _run([python, "-m", "pip", "install", "--no-input", source],
         on_progress, cancel_token, "installation de Chatterbox")

    marker_path().write_text(json.dumps({
        "commit": spec.get("commit", ""),
        "source": source,
        "python": candidate.executable,
        "version": ".".join(str(p) for p in candidate.version),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return directory


def remove() -> bool:
    """Efface l'environnement. Les poids du modele ne sont pas touches."""
    import shutil

    directory = runtime_dir()
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()


def selftest(timeout: int = 180) -> dict:
    """Demande a l'environnement ce qu'il sait faire, et le rend tel quel.

    Rien n'est suppose : ni la version de torch, ni la presence de CUDA, ni
    meme que Chatterbox s'importe. Ce que l'interface affiche vient d'ici.
    """
    python = python_executable()
    if not python.is_file():
        return {"ok": False, "error": "L'environnement Chatterbox n'est pas installé."}

    command = [str(python), str(worker_script()), "--selftest"]
    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, creationflags=creation)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "L'environnement Chatterbox ne répond pas."}
    except OSError as error:                           # pragma: no cover - exec impossible
        return {"ok": False, "error": f"Environnement illisible : {error}"}

    payload = {}
    for line in (result.stdout or "").splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("event") in ("selftest", "error"):
            payload = data
    if payload.get("event") == "selftest":
        return {"ok": True, **{k: v for k, v in payload.items() if k != "event"}}
    message = payload.get("message") or (result.stderr or "").strip()[-400:]
    return {"ok": False, "error": message or "Chatterbox n'a pas pu être chargé."}


def describe() -> dict:
    """Etat lisible de l'environnement, sans rien lancer."""
    spec = catalogue.runtime_spec()
    return {
        "directory": str(runtime_dir()),
        "python": str(python_executable()),
        "ready": is_ready(),
        "outdated": is_outdated(),
        "commit_installed": installed_commit(),
        "commit_expected": str(spec.get("commit") or ""),
        "estimated_download_mb": spec.get("estimated_download_mb"),
        "packages": list(spec.get("packages") or []),
        "source": str(spec.get("source") or ""),
    }
