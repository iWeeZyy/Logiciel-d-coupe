"""Fils d'execution du banc d'essai ZeroGPU.

Deux traitements longs, jamais dans le fil graphique : la generation distante
(reseau, file d'attente, GPU) et la transcription de controle (Faster-Whisper,
local). Meme forme que gui/voice_studio/workers.py -- signaux Qt, jeton
d'annulation partage, message deja lisible en cas d'echec -- mais dans un
fichier separe, pour que supprimer le banc d'essai ne touche pas aux fils du
Voice Studio normal.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from utils.errors import CancelledError, ClipFarmingError


class ZeroGpuWorker(QThread):
    """Script -> WAV unique, via le Space."""

    done = Signal(object)                # zerogpu_service.Outcome
    failed = Signal(str)
    progress = Signal(object)            # evenement (dict)
    cancelled = Signal()

    def __init__(self, script, out_wav, params, cancel_token=None):
        super().__init__()
        self.script = script
        self.out_wav = out_wav
        self.params = params
        self.cancel_token = cancel_token

    def run(self) -> None:
        from voice_studio import zerogpu_service

        try:
            outcome = zerogpu_service.generate(
                self.script, self.params, self.out_wav,
                on_progress=lambda event: self.progress.emit(event),
                cancel_token=self.cancel_token)
        except CancelledError:
            self.cancelled.emit()
        except ClipFarmingError as error:
            # Ces erreurs portent deja un message ecrit pour un humain.
            self.failed.emit(str(error))
        except Exception as error:                    # pragma: no cover - garde-fou
            self.failed.emit(
                "La génération sur ZeroGPU s'est interrompue de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}")
        else:
            self.done.emit(outcome)


class TranscribeWorker(QThread):
    """WAV -> transcription, par le Faster-Whisper LOCAL deja en place.

    Aucun code de transcription n'est ecrit ici : c'est exactement l'appel que
    fait deja le Voice Studio, sur un fichier different."""

    done = Signal(object)                # Transcript
    failed = Signal(str)
    detail = Signal(object, str)
    cancelled = Signal()

    def __init__(self, media_path, model, language, device="auto", cancel_token=None):
        super().__init__()
        self.media_path = media_path
        self.model = model
        self.language = language
        self.device = device
        self.cancel_token = cancel_token

    def run(self) -> None:
        from voice_studio import transcription

        try:
            transcript = transcription.transcribe_media(
                self.media_path, self.model, self.language, device=self.device,
                cancel_token=self.cancel_token,
                on_progress=lambda fraction, label: self.detail.emit(fraction, label))
        except CancelledError:
            self.cancelled.emit()
        except ClipFarmingError as error:
            self.failed.emit(str(error))
        except Exception as error:                    # pragma: no cover - garde-fou
            self.failed.emit(
                "La transcription s'est interrompue de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}")
        else:
            self.done.emit(transcript)
