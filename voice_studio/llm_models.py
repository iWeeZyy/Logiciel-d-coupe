"""Gestionnaire des modeles de reecriture (GGUF) et lecture des ressources.

Meme forme que voice_studio/piper_models.py, et pour la meme raison : ce sont
les memes besoins (catalogue modifiable, telechargement repris, verification,
suppression, taille occupee). Le telechargement lui-meme n'est pas reecrit --
il vient de voice_studio/downloads.py, partage avec les voix.

Regles tenues :
- RIEN N'EST TELECHARGE SANS DEMANDE EXPLICITE. Plusieurs gigaoctets ne
  partent pas tout seuls.
- UN MODELE INSTALLE EST UN MODELE UTILISABLE : le fichier n'est renomme qu'a
  la fin, et sa taille est verifiee.
- CE QU'ON NE SAIT PAS, ON NE L'AFFIRME PAS. La memoire disponible est lue
  quand le systeme la donne ; sinon elle est declaree inconnue, et aucune
  recommandation n'est affichee.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.config_loader import CONFIG_DIR
from core.logging_setup import get_logger
from core.paths import user_data_dir
from voice_studio import downloads

logger = get_logger()

CATALOGUE_FILE = "rewrite_models.json"
SUFFIX = ".gguf"


class LlmModelError(Exception):
    """Probleme d'installation d'un modele, formule pour l'utilisateur."""


@dataclass(frozen=True)
class CatalogueModel:
    """Un modele propose au telechargement."""

    key: str
    label: str
    url: str = ""
    parameters: str = ""          # "7B", "12B"
    quantization: str = ""        # "Q4_K_M"
    size_gb: float = 0.0          # ordre de grandeur annonce par l'editeur
    ram_gb: float = 0.0           # memoire libre conseillee
    licence: str = ""
    notes: str = ""

    @property
    def filename(self) -> str:
        return f"{self.key}{SUFFIX}"


def models_dir() -> Path:
    """Dossier des modeles installes, sous les donnees de l'application."""
    from gui import settings_store

    try:
        configured = settings_store.get("llm_models_dir")
    except Exception:                                  # pragma: no cover
        configured = None
    return Path(configured) if configured else user_data_dir() / "voice_studio_data" / "llm"


def catalogue() -> list[CatalogueModel]:
    """Modeles proposes, lus dans config/rewrite_models.json."""
    path = CONFIG_DIR / CATALOGUE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning(f"Catalogue des modeles de reecriture illisible : {error}")
        return []
    return [CatalogueModel(
        key=entry.get("key", ""), label=entry.get("label", ""), url=entry.get("url", ""),
        parameters=entry.get("parameters", ""), quantization=entry.get("quantization", ""),
        size_gb=float(entry.get("size_gb", 0) or 0), ram_gb=float(entry.get("ram_gb", 0) or 0),
        licence=entry.get("licence", ""), notes=entry.get("notes", ""))
        for entry in data.get("models", []) if entry.get("key")]


def model_path(key: str) -> Path:
    return models_dir() / f"{key}{SUFFIX}"


def is_installed(key: str) -> bool:
    """Present ET d'une taille plausible : un GGUF fait au moins des centaines
    de megaoctets, un fichier de quelques kilooctets est un reste."""
    path = model_path(key)
    return path.is_file() and path.stat().st_size > 50 * 1024 * 1024


def installed_keys() -> list[str]:
    directory = models_dir()
    if not directory.is_dir():
        return []
    return [p.name[: -len(SUFFIX)] for p in sorted(directory.glob(f"*{SUFFIX}"))
            if is_installed(p.name[: -len(SUFFIX)])]


def installed_size_bytes() -> int:
    directory = models_dir()
    if not directory.is_dir():
        return 0
    return sum(p.stat().st_size for p in directory.glob(f"*{SUFFIX}") if p.is_file())


def describe(key: str) -> CatalogueModel:
    for model in catalogue():
        if model.key == key:
            return model
    return CatalogueModel(key=key, label=key)


def remove(key: str) -> bool:
    path = model_path(key)
    if path.is_file():
        path.unlink()
        return True
    return False


def install(key: str, on_progress: Optional[Callable] = None,
            cancel_token: Optional[CancelToken] = None, opener=None) -> Path:
    """Telecharge le modele. Rien d'automatique : appele sur un clic."""
    model = describe(key)
    if not model.url:
        raise LlmModelError(
            f"Aucune adresse de téléchargement n'est connue pour « {key} ». "
            "Le catalogue (config/rewrite_models.json) doit être complété.")
    try:
        downloads.download_file(model.url, model_path(key), on_progress=on_progress,
                                cancel_token=cancel_token, opener=opener)
    except downloads.DownloadError as error:
        raise LlmModelError(str(error)) from error

    if not is_installed(key):
        model_path(key).unlink(missing_ok=True)
        raise LlmModelError(
            "Le fichier reçu ne ressemble pas à un modèle utilisable "
            "(taille trop faible). Relance le téléchargement.")
    logger.info(f"Modele de reecriture installe : {key}")
    return model_path(key)


# ------------------------------------------------------------- ressources

@dataclass(frozen=True)
class Resources:
    """Ce que la machine peut offrir. Un champ a None est INCONNU, pas nul."""

    total_ram_gb: Optional[float] = None
    available_ram_gb: Optional[float] = None
    free_disk_gb: Optional[float] = None
    gpu_name: str = ""
    vram_gb: Optional[float] = None
    cpu_count: Optional[int] = None
    # llama.cpp s'appuie sur des instructions vectorielles modernes. Sur un
    # processeur qui ne les a pas, le chargement d'un modele ne renvoie pas une
    # erreur : il ARRETE le programme. Mieux vaut donc le dire avant.
    avx2: Optional[bool] = None

    @property
    def ram_known(self) -> bool:
        return self.available_ram_gb is not None


def _windows_memory() -> tuple[Optional[float], Optional[float]]:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None, None
    return (status.ullTotalPhys / 1024 ** 3, status.ullAvailPhys / 1024 ** 3)


def _linux_memory() -> tuple[Optional[float], Optional[float]]:
    try:
        content = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None, None
    values = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
            values[parts[0].rstrip(":")] = int(parts[1]) / 1024 ** 2
    return values.get("MemTotal"), values.get("MemAvailable")


def _has_avx2() -> Optional[bool]:
    """Le processeur a-t-il les instructions AVX2 ? None si on ne sait pas."""
    if sys.platform.startswith("linux"):
        try:
            content = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in content.splitlines():
            if line.lower().startswith("flags"):
                return " avx2 " in f" {line.lower()} "
        return None
    if sys.platform == "win32":
        try:
            import ctypes

            # PF_AVX2_INSTRUCTIONS_AVAILABLE = 40 (documentation Microsoft).
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
        except Exception:                              # pragma: no cover - API absente
            return None
    return None


def _gpu() -> tuple[str, Optional[float]]:
    """Carte graphique NVIDIA, via nvidia-smi quand il est la.

    Aucune autre marque n'est interrogee : pretendre lire la VRAM d'une carte
    AMD ou Intel sans en avoir le moyen produirait un chiffre faux.
    """
    binary = shutil.which("nvidia-smi")
    if not binary:
        return "", None
    try:
        output = subprocess.run(
            [binary, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "", None
    first = output.splitlines()[0] if output else ""
    if "," not in first:
        return "", None
    name, memory = first.split(",", 1)
    try:
        return name.strip(), float(memory.strip()) / 1024
    except ValueError:
        return name.strip(), None


def resources() -> Resources:
    """Ressources lisibles de cette machine."""
    if sys.platform == "win32":
        total, available = _windows_memory()
    elif sys.platform.startswith("linux"):
        total, available = _linux_memory()
    else:
        total, available = None, None

    try:
        free_disk = shutil.disk_usage(models_dir().parent).free / 1024 ** 3
    except OSError:
        free_disk = None

    name, vram = _gpu()
    return Resources(total_ram_gb=total, available_ram_gb=available, free_disk_gb=free_disk,
                     gpu_name=name, vram_gb=vram, cpu_count=os.cpu_count(),
                     avx2=_has_avx2())


RECOMMENDED = "recommande"
POSSIBLE = "possible"
INSUFFICIENT = "insuffisant"
UNKNOWN = "inconnu"

VERDICT_LABELS = {
    RECOMMENDED: "✓ Recommandé",
    POSSIBLE: "⚠ Peut fonctionner, mais la génération sera lente",
    INSUFFICIENT: "❌ Ressources probablement insuffisantes",
    UNKNOWN: "Ressources non mesurables sur cet ordinateur",
}


def verdict(model: CatalogueModel, machine: Resources | None = None) -> str:
    """Ce modele peut-il tourner ici ?

    Sans mesure de memoire, on repond INCONNU : une estimation inventee serait
    pire qu'une absence de reponse.
    """
    machine = machine or resources()
    if not machine.ram_known or not model.ram_gb:
        return UNKNOWN
    available = machine.available_ram_gb or 0.0
    if available >= model.ram_gb:
        return RECOMMENDED
    if available >= model.ram_gb * 0.75:
        return POSSIBLE
    return INSUFFICIENT
