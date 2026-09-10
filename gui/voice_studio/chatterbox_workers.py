"""Fils d'execution de Chatterbox : installation, telechargement, diagnostic.

Trois traitements qui durent, et qui doivent donc rendre compte et s'annuler :
creer l'environnement Python (plusieurs centaines de megaoctets), telecharger
les poids du modele, et demander a l'environnement ce qu'il sait faire. Meme
jeton d'annulation que partout ailleurs.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from utils.errors import CancelledError
from voice_studio import chatterbox_models as models
from voice_studio import chatterbox_runtime as runtime

MODE_RUNTIME = "runtime"
MODE_MODEL = "model"
MODE_SELFTEST = "selftest"


class ChatterboxSetupWorker(QThread):
    """Installe l'environnement, telecharge le modele, ou teste l'ensemble."""

    log = Signal(str)                     # ligne de compte rendu (pip, telechargement)
    progress = Signal(object, str)        # fraction (ou None), libelle
    done = Signal(object)                 # dict : ce qui s'est passe
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, mode: str, cancel_token=None, base_python: str = ""):
        super().__init__()
        self.mode = mode
        self.cancel_token = cancel_token
        self.base_python = base_python

    def run(self) -> None:
        try:
            if self.mode == MODE_RUNTIME:
                result = self._install_runtime()
            elif self.mode == MODE_MODEL:
                result = self._install_model()
            else:
                result = runtime.selftest()
        except CancelledError:
            self.cancelled.emit()
        except (runtime.ChatterboxRuntimeError, models.ChatterboxModelError) as error:
            self.failed.emit(str(error))
        except Exception as error:                     # pragma: no cover - garde-fou
            self.failed.emit(
                "L'installation de Chatterbox s'est interrompue de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}")
        else:
            self.done.emit(result)

    def _install_runtime(self) -> dict:
        def _on_line(step: str, line: str) -> None:
            self.log.emit(line)
            self.progress.emit(None, step)

        runtime.install(on_progress=_on_line, cancel_token=self.cancel_token,
                        base_python=self.base_python)
        return {"mode": MODE_RUNTIME, **runtime.describe()}

    def _install_model(self) -> dict:
        def _on_file(fraction, name, index, total) -> None:
            self.progress.emit(fraction, f"{name}  ({index}/{total})")

        models.install(on_progress=_on_file, cancel_token=self.cancel_token)
        return {"mode": MODE_MODEL, **models.describe()}
