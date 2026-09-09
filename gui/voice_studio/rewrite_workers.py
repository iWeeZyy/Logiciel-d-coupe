"""Fils d'execution de la reecriture.

Trois traitements longs : charger un modele (quelques secondes a une minute),
generer (des dizaines de secondes par variante sur processeur), telecharger un
modele (plusieurs gigaoctets). Aucun ne tourne dans le fil graphique, et tous
partagent le jeton d'annulation deja utilise partout ailleurs.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from utils.errors import CancelledError
from voice_studio import llm_models
from voice_studio.rewriting import service
from voice_studio.rewriting.providers.base import ProviderError


class RewriteWorker(QThread):
    """Transcript -> variantes de script."""

    step = Signal(int, int, str)         # numero, total, libelle
    done = Signal(object)                # RewriteResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, request, provider, cancel_token, model_name: str = "",
                 stricter: bool = False, use_cache: bool = True):
        super().__init__()
        self.request = request
        self.provider = provider
        self.cancel_token = cancel_token
        self.model_name = model_name
        self.stricter = stricter
        self.use_cache = use_cache

    def run(self) -> None:
        try:
            result = service.rewrite(
                self.request, self.provider, cancel_token=self.cancel_token,
                on_step=lambda i, t, label: self.step.emit(i, t, label),
                model_name=self.model_name, use_cache=self.use_cache,
                stricter=self.stricter)
        except CancelledError:
            self.cancelled.emit()
        except (service.RewriteError, ProviderError) as error:
            self.failed.emit(str(error))
        except MemoryError:
            self.failed.emit(
                "Mémoire insuffisante pour ce modèle. Essaie le modèle léger, "
                "ou ferme d'autres applications.")
        except Exception as error:                     # pragma: no cover - garde-fou
            self.failed.emit(
                "La réécriture s'est interrompue de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}")
        else:
            self.done.emit(result)


class ModelDownloadWorker(QThread):
    """Installation d'un modele GGUF."""

    progressed = Signal(object, float, object)   # fraction, Go recus, Go total
    done = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, key: str, cancel_token):
        super().__init__()
        self.key = key
        self.cancel_token = cancel_token

    def run(self) -> None:
        def _progress(fraction, done_bytes, total_bytes):
            self.progressed.emit(fraction, done_bytes / 1024 ** 3,
                                 (total_bytes / 1024 ** 3) if total_bytes else None)

        try:
            llm_models.install(self.key, on_progress=_progress, cancel_token=self.cancel_token)
        except CancelledError:
            self.cancelled.emit()
        except llm_models.LlmModelError as error:
            self.failed.emit(str(error))
        except Exception as error:                     # pragma: no cover - garde-fou
            self.failed.emit(
                "Le téléchargement du modèle a échoué de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}")
        else:
            self.done.emit(self.key)
