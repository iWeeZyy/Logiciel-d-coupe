"""Detection GPU/CPU pour faster-whisper (CTranslate2), sans dependance a torch.

Point d'attention documente en amont (etape 2 du projet) : si les libs CUDA sont
installees via pip (nvidia-cublas-cu12/nvidia-cudnn-cu12) plutot qu'un CUDA
systeme, LD_LIBRARY_PATH doit etre positionne AVANT le demarrage du process
Python -- CTranslate2 resout le chemin des libs a l'import. Si ce n'est pas le
cas, resolve_device() ne plante pas : il retombe sur CPU. C'est pour ca que
resolve_device() est verbeux sur son choix plutot que de le faire silencieusement.
"""
from __future__ import annotations

from core.logging_setup import get_logger

logger = get_logger()


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        # ctranslate2 absent, pas encore installe, ou build sans support CUDA.
        return 0


def resolve_device(requested: str) -> tuple[str, str]:
    """requested: "auto" | "cpu" | "cuda". Renvoie (device, compute_type).

    compute_type choisi pour un bon compromis vitesse/precision par defaut :
    int8 sur CPU (rapide, empreinte memoire reduite), float16 sur GPU.
    """
    requested = (requested or "auto").lower()

    if requested == "cpu":
        logger.info("Peripherique : CPU (force par --device cpu).")
        return "cpu", "int8"

    n_gpus = cuda_device_count()

    if requested == "cuda":
        if n_gpus == 0:
            logger.warning(
                "GPU demande via --device cuda mais aucun GPU CUDA n'a ete detecte "
                "par ctranslate2 -- verifie CUDA/cuDNN (voir README) et que "
                "LD_LIBRARY_PATH a bien ete positionne avant le lancement de python. "
                "Bascule sur CPU."
            )
            return "cpu", "int8"
        logger.info(f"Peripherique : GPU CUDA ({n_gpus} detecte(s), force par --device cuda).")
        return "cuda", "float16"

    # auto
    if n_gpus > 0:
        logger.info(f"Peripherique : GPU CUDA detecte automatiquement ({n_gpus}).")
        return "cuda", "float16"
    logger.info("Peripherique : CPU (aucun GPU CUDA detecte).")
    return "cpu", "int8"
