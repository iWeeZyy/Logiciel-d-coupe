"""Fil d'execution de la creation video.

Un rendu dure : la synthese de la voix, sa transcription et l'encodage se
comptent en dizaines de secondes, parfois en minutes. Comme partout ailleurs
dans cette application, cela ne tourne pas dans le fil graphique, et cela
s'annule avec le meme jeton (core/cancellation.py).
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from utils.errors import CancelledError, ClipFarmingError
from voice_studio import video_service


class VideoCreationWorker(QThread):
    """Demande -> fichier MP4."""

    step = Signal(int, str)              # numero d'etape, libelle
    detail = Signal(object, str)         # fraction (ou None), libelle
    done = Signal(object)                # VideoReport
    failed = Signal(str)                 # message deja lisible
    cancelled = Signal()

    def __init__(self, request, cancel_token):
        super().__init__()
        self.request = request
        self.cancel_token = cancel_token

    def run(self) -> None:
        reporter = video_service.Reporter(
            on_step=lambda index, label: self.step.emit(index, label),
            on_detail=lambda fraction, label: self.detail.emit(fraction, label),
        )
        try:
            report = video_service.create_video(self.request, reporter=reporter,
                                                cancel_token=self.cancel_token)
        except CancelledError:
            self.cancelled.emit()
        except (ClipFarmingError, ValueError) as error:
            # Ces erreurs portent deja un message ecrit pour un humain.
            self.failed.emit(str(error))
        except Exception as error:                 # pragma: no cover - garde-fou
            # Jamais une trace Python a l'ecran : on nomme ce qui a echoue.
            self.failed.emit(
                "La création de la vidéo s'est interrompue de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}"
            )
        else:
            self.done.emit(report)
