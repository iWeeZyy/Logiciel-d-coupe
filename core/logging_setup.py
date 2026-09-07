"""Logger central + affichage de la progression "[i/n] Etape..." demande par le cahier des charges."""
from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "clip_farming"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


class StepProgress:
    """Affiche "[1/5] Extraction audio..." sur stdout, une ligne par etape.

    Usage:
        progress = StepProgress(5)
        progress.step("Extraction audio")
        ...
        progress.step("Transcription")
    """

    def __init__(self, total_steps: int):
        self.total = total_steps
        self.current = 0

    def step(self, label: str) -> None:
        self.current += 1
        print(f"[{self.current}/{self.total}] {label}...", flush=True)

    def substep(self, label: str) -> None:
        print(f"    -> {label}", flush=True)
