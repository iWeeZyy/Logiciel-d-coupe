"""Fils d'execution de Voice Studio.

Deux traitements peuvent durer : la transcription (plusieurs minutes) et la
generation de voix (quelques secondes a quelques dizaines). Aucun des deux ne
tourne dans le fil graphique -- meme regle que le reste de l'application, et
meme jeton d'annulation (core/cancellation.py) que le pipeline video.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from utils.errors import CancelledError, ClipFarmingError
from voice_studio import services, tts


class AnalysisWorker(QThread):
    """URL -> projet transcrit."""

    step = Signal(int, str)              # numero d'etape, libelle
    detail = Signal(object, str)         # fraction (ou None), libelle
    done = Signal(object)                # AnalysisReport
    failed = Signal(str)                 # message deja lisible
    cancelled = Signal()

    def __init__(self, request, cancel_token):
        super().__init__()
        self.request = request
        self.cancel_token = cancel_token

    def run(self) -> None:
        reporter = services.Reporter(
            on_step=lambda index, label: self.step.emit(index, label),
            on_detail=lambda fraction, label: self.detail.emit(fraction, label),
        )
        try:
            report = services.run_analysis(self.request, reporter=reporter,
                                           cancel_token=self.cancel_token)
        except CancelledError:
            self.cancelled.emit()
        except (ClipFarmingError, ValueError) as error:
            # Ces erreurs portent deja un message ecrit pour un humain.
            self.failed.emit(str(error))
        except Exception as error:                     # pragma: no cover - garde-fou
            # Jamais une trace Python a l'ecran : on nomme ce qui a echoue.
            self.failed.emit(
                "L'analyse s'est interrompue de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}"
            )
        else:
            self.done.emit(report)


class VoiceWorker(QThread):
    """Texte -> fichier audio."""

    done = Signal(str)                   # chemin du WAV produit
    failed = Signal(str)

    def __init__(self, text, out_path, voice, rate, volume, sentence_pause_s):
        super().__init__()
        self.text = text
        self.out_path = out_path
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.sentence_pause_s = sentence_pause_s

    def run(self) -> None:
        try:
            path = tts.synthesize(self.text, self.out_path, voice=self.voice,
                                  rate=self.rate, volume=self.volume,
                                  sentence_pause_s=self.sentence_pause_s)
        except tts.TtsError as error:
            self.failed.emit(str(error))
        except Exception as error:                     # pragma: no cover - garde-fou
            self.failed.emit(
                "La génération de la voix a échoué de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}"
            )
        else:
            self.done.emit(path)
