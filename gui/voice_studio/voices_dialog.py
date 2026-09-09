"""Gestionnaire des voix locales Piper.

Une fenetre, une liste, et pour chaque voix un etat vrai : installee ou non.
Rien n'est telecharge sans un clic, la progression est affichee, et le
telechargement tourne dans un fil separe -- plusieurs dizaines de megaoctets
ne doivent pas figer l'interface.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from utils.errors import CancelledError
from gui.voice_studio.workers import VoiceWorker
from voice_studio import piper_models, tts


class DownloadWorker(QThread):
    """Installation d'une voix -- ou du moteur -- hors du fil de l'interface."""

    progressed = Signal(object, float, object)     # fraction, Mo recus, Mo total
    done = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, key: str, cancel_token: CancelToken, engine: bool = False):
        super().__init__()
        self.key = key
        self.cancel_token = cancel_token
        self.engine = engine

    def run(self) -> None:
        def _progress(fraction, done_bytes, total_bytes):
            self.progressed.emit(fraction, done_bytes / 1_000_000,
                                 (total_bytes / 1_000_000) if total_bytes else None)

        try:
            if self.engine:
                piper_models.install_engine(on_progress=_progress,
                                            cancel_token=self.cancel_token)
            else:
                piper_models.install(self.key, on_progress=_progress,
                                     cancel_token=self.cancel_token)
        except CancelledError:
            self.cancelled.emit()
        except piper_models.PiperModelError as error:
            self.failed.emit(str(error))
        except Exception as error:                     # pragma: no cover - garde-fou
            self.failed.emit(
                "L'installation de la voix a échoué de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}")
        else:
            self.done.emit(self.key)


class VoicesDialog(QDialog):
    """« Gérer les voix » : catalogue, installation, suppression."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Voix locales — Piper")
        self.resize(720, 520)
        self._worker = None
        self._voice_worker = None
        self._cancel_token = None
        self._current_key = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("🎙️  VOIX LOCALES")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(title)

        self.engine_label = QLabel("")
        self.engine_label.setWordWrap(True)
        self.engine_label.setProperty("role", "muted")
        layout.addWidget(self.engine_label)

        explanation = QLabel(
            "Les voix sont téléchargées une seule fois, puis fonctionnent entièrement "
            "hors ligne : ni compte, ni abonnement, et le texte ne quitte jamais cet "
            "ordinateur.")
        explanation.setWordWrap(True)
        explanation.setProperty("role", "muted")
        layout.addWidget(explanation)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.list_layout = QVBoxLayout(content)
        self.list_layout.setContentsMargins(0, 0, 8, 0)
        self.list_layout.setSpacing(8)
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        footer = QHBoxLayout()
        self.usage_label = QLabel("")
        self.usage_label.setProperty("role", "muted")
        footer.addWidget(self.usage_label)
        footer.addStretch(1)

        self.install_engine_btn = QPushButton("Installer le moteur Piper")
        self.install_engine_btn.setProperty("variant", "primary")
        self.install_engine_btn.setVisible(False)
        self.install_engine_btn.clicked.connect(self._install_engine)
        footer.addWidget(self.install_engine_btn)

        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self.refresh)
        footer.addWidget(refresh)

        self.cancel_btn = QPushButton("Annuler le téléchargement")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        footer.addWidget(self.cancel_btn)

        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.refresh()

    # ------------------------------------------------------------- liste
    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        engine = tts.PiperEngine()
        runtime = engine.runtime()
        if runtime:
            where = ("bibliothèque locale" if runtime == "library"
                     else f"programme {piper_models.engine_binary()}")
            self.engine_label.setText(
                f"Moteur Piper détecté ({where}).  "
                f"Dossier des voix : {piper_models.models_dir()}")
        else:
            # Dire OU l'on a cherche : sans cela, un moteur installe mais non
            # detecte laisse l'utilisateur sans aucune prise sur le probleme.
            self.engine_label.setText(
                "Piper n'est pas encore configuré sur cet ordinateur. Installe le "
                "moteur ci-dessous : c'est un programme d'une vingtaine de mégaoctets, "
                "téléchargé une seule fois. Les voix de Windows, elles, restent "
                "disponibles en attendant.\n"
                f"Dossier vérifié : {piper_models.engine_dir()} "
                f"({'présent mais aucun programme piper dedans' if piper_models.engine_dir().is_dir() else 'absent'})")
        self.install_engine_btn.setVisible(not runtime)

        installed = set(piper_models.installed_keys())
        entries = piper_models.catalogue()
        known = {voice.key for voice in entries}
        for key in sorted(installed - known):
            entries.append(piper_models.describe(key))

        if not entries:
            empty = QLabel("Aucune voix au catalogue et aucune voix installée.")
            empty.setProperty("role", "muted")
            self.list_layout.addWidget(empty)
        for voice in entries:
            self.list_layout.addWidget(self._voice_row(voice, voice.key in installed))
        self.list_layout.addStretch(1)

        count = len(installed)
        size = piper_models.installed_size_bytes() / 1_000_000
        self.usage_label.setText(
            f"{count} voix installée{'s' if count > 1 else ''}  •  {size:.0f} Mo utilisés")

    def _voice_row(self, voice, installed: bool) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        name = QLabel(f"{voice.language_label} — {voice.label}")
        name.setStyleSheet("font-weight: 600;")
        name.setWordWrap(True)
        top.addWidget(name, stretch=1)

        state = QLabel("✅ Installée" if installed else "Non installée")
        state.setProperty("role", "muted")
        top.addWidget(state)
        layout.addLayout(top)

        details = []
        if voice.quality:
            details.append(f"qualité {voice.quality}")
        if voice.gender:
            details.append({"female": "voix féminine", "male": "voix masculine"}
                           .get(voice.gender, voice.gender))
        if installed:
            size = piper_models.model_path(voice.key).stat().st_size / 1_000_000
            details.append(f"{size:.0f} Mo sur le disque")
        else:
            details.append("taille annoncée par le serveur au téléchargement")
        detail = QLabel("  •  ".join(details))
        detail.setProperty("role", "muted")
        layout.addWidget(detail)

        actions = QHBoxLayout()
        actions.addStretch(1)
        if installed:
            test = QPushButton("Tester")
            test.setToolTip("Génère une phrase avec cette voix et dit ce qui s'est passé.")
            test.clicked.connect(lambda _c=False, k=voice.key: self._test(k))
            actions.addWidget(test)

            remove = QPushButton("Supprimer")
            remove.clicked.connect(lambda _c=False, k=voice.key: self._remove(k))
            actions.addWidget(remove)
        else:
            download = QPushButton("Télécharger")
            download.setProperty("variant", "primary")
            download.clicked.connect(lambda _c=False, k=voice.key: self._download(k))
            actions.addWidget(download)
        layout.addLayout(actions)
        return frame

    # ---------------------------------------------------------- actions
    def _download(self, key: str) -> None:
        if self._worker is not None:
            return
        answer = QMessageBox.question(
            self, "Télécharger cette voix",
            "Cette voix va être téléchargée depuis le dépôt officiel des voix Piper "
            "(plusieurs dizaines de mégaoctets).\n\nUne fois installée, elle "
            "fonctionne entièrement hors ligne.\n\nContinuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._current_key = key
        self._cancel_token = CancelToken()
        self._worker = DownloadWorker(key, self._cancel_token)
        self._worker.progressed.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.status.setText(f"Téléchargement de {key}...")
        self._worker.start()

    def _install_engine(self) -> None:
        if self._worker is not None:
            return
        answer = QMessageBox.question(
            self, "Installer le moteur Piper",
            "Le programme Piper va être téléchargé depuis son dépôt officiel "
            "(une vingtaine de mégaoctets), puis installé dans les données de "
            "l'application.\n\nContinuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cancel_token = CancelToken()
        self._worker = DownloadWorker("", self._cancel_token, engine=True)
        self._worker.progressed.connect(self._on_progress)
        self._worker.done.connect(lambda _k: self.status.setText("Moteur Piper installé."))
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.status.setText("Téléchargement du moteur Piper...")
        self._worker.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.status.setText("Annulation en cours...")

    def _on_progress(self, fraction, done_mb, total_mb) -> None:
        if fraction is None:
            self.progress.setRange(0, 0)
            self.status.setText(f"Téléchargement : {done_mb:.0f} Mo")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
            self.status.setText(f"Téléchargement : {done_mb:.0f} / {total_mb:.0f} Mo")

    def _on_done(self, key: str) -> None:
        self.status.setText(f"Voix installée : {key}")

    def _on_failed(self, message: str) -> None:
        self.status.setText("")
        QMessageBox.warning(self, "Téléchargement impossible", message)

    def _on_cancelled(self) -> None:
        self.status.setText("Téléchargement annulé. Il reprendra là où il s'est arrêté.")

    def _on_finished(self) -> None:
        self._worker = None
        self._cancel_token = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.refresh()

    def _test(self, key: str) -> None:
        """Essai reel de la voix : c'est la seule facon de savoir si elle
        fonctionne vraiment sur cette machine, plutot que de le supposer."""
        if self._voice_worker is not None:
            return
        engine = tts.PiperEngine()
        if not engine.runtime():
            QMessageBox.warning(
                self, "Moteur absent",
                "Le programme Piper n'est pas installé : cette voix ne peut pas "
                "encore être utilisée.")
            return
        voice = next((v for v in engine.voices()
                      if v.id == str(piper_models.model_path(key))), None)
        if voice is None:
            QMessageBox.warning(self, "Voix introuvable",
                                "Cette voix n'est plus lisible sur le disque.")
            return

        from voice_studio import store

        self.status.setText("Essai de la voix en cours...")
        self._voice_worker = VoiceWorker(
            "Bonjour, ceci est un essai de voix locale.",
            str(store.audio_dir() / "essai_voix.wav"), voice, 1.0, 1.0, 0.0)
        self._voice_worker.done.connect(self._on_test_done)
        self._voice_worker.failed.connect(self._on_test_failed)
        self._voice_worker.finished.connect(self._on_test_finished)
        self._voice_worker.start()

    def _on_test_done(self, path: str) -> None:
        import wave

        try:
            with wave.open(path) as handle:
                seconds = handle.getnframes() / handle.getframerate()
        except Exception:                              # pragma: no cover - fichier illisible
            seconds = 0.0
        self.status.setText(f"Voix fonctionnelle : {seconds:.1f} s produites — {path}")

    def _on_test_failed(self, message: str) -> None:
        self.status.setText("")
        QMessageBox.warning(self, "Cette voix ne fonctionne pas", message)

    def _on_test_finished(self) -> None:
        self._voice_worker = None

    def _remove(self, key: str) -> None:
        answer = QMessageBox.question(
            self, "Supprimer cette voix",
            f"Supprimer définitivement la voix {key} de cet ordinateur ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        piper_models.remove(key)
        self.status.setText(f"Voix supprimée : {key}")
        self.refresh()

    def cleanup(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        for worker in (self._worker, self._voice_worker):
            if worker is not None and worker.isRunning():
                worker.wait(4000)

    def reject(self) -> None:                          # pragma: no cover - interaction
        self.cleanup()
        super().reject()
