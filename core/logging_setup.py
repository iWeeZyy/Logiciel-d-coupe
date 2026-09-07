"""Logger central + affichage de la progression "[i/n] Etape..." demande par le cahier des charges.

StepProgress accepte un callback optionnel on_progress(ProgressEvent) -- utilise
par la GUI (gui/controller.py) pour piloter sa barre de progression. La CLI ne
passe jamais ce callback : son affichage texte reste strictement identique."""
from __future__ import annotations

import logging
import sys
import time
from typing import Callable, Optional

from core.models import ProgressEvent

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

    Prend la liste ORDONNEE des etapes de ce run (core/steps.py) plutot qu'un
    simple compte : elle est retransmise dans chaque ProgressEvent, ce qui
    permet a l'interface d'afficher exactement les etapes reellement executees
    au lieu d'une copie figee de son cote.

    Usage:
        progress = StepProgress(build_step_labels(...))
        progress.step("Extraction audio")
        ...
    """

    def __init__(self, step_labels: list[str], on_progress: Optional[Callable[[ProgressEvent], None]] = None):
        self.step_labels = list(step_labels)
        self.total = len(self.step_labels)
        self.current = 0
        self.on_progress = on_progress
        self._start = time.monotonic()
        self._current_label = ""
        self._clips_found: Optional[int] = None

    def step(self, label: str) -> None:
        self.current += 1
        self._current_label = label
        print(f"[{self.current}/{self.total}] {label}...", flush=True)
        self._emit(sub_label=None, fraction=0.0)

    def substep(self, label: str, fraction: Optional[float] = None) -> None:
        print(f"    -> {label}", flush=True)
        self._emit(sub_label=label, fraction=fraction)

    def set_clips_found(self, count: int) -> None:
        self._clips_found = count

    def report(self, sub_label: str, fraction: Optional[float] = None) -> None:
        """Emet un ProgressEvent SANS imprimer -- pour des mises a jour frequentes
        (ex: avancement segment par segment pendant la transcription) qui
        spammeraient la sortie CLI si elles passaient par substep()."""
        self._emit(sub_label=sub_label, fraction=fraction)

    def _emit(self, sub_label: Optional[str], fraction: Optional[float] = None) -> None:
        if self.on_progress is None:
            return
        self.on_progress(
            ProgressEvent(
                step_index=self.current,
                total_steps=self.total,
                label=self._current_label,
                sub_label=sub_label,
                elapsed_s=time.monotonic() - self._start,
                clips_found=self._clips_found,
                step_fraction=fraction,
                step_labels=self.step_labels,
            )
        )
