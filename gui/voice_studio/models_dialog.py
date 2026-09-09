"""Gestionnaire des modeles de reecriture.

Meme forme que le gestionnaire des voix : une liste, un etat vrai par ligne,
un telechargement qui ne fige rien et qui s'annule. En plus ici : ce que la
machine peut supporter, mesure quand c'est possible et declare inconnu sinon.
"""
from __future__ import annotations

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
from gui.voice_studio.rewrite_workers import ModelDownloadWorker
from voice_studio import llm_models
from voice_studio.rewriting.providers.llama_cpp_provider import LlamaCppProvider


class ModelsDialog(QDialog):
    """« Gérer les modèles » : catalogue, ressources, installation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modèles de réécriture")
        self.resize(760, 560)
        self._worker = None
        self._cancel_token = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("⚙️  MODÈLES DE RÉÉCRITURE")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(title)

        self.machine_label = QLabel("")
        self.machine_label.setWordWrap(True)
        self.machine_label.setProperty("role", "muted")
        layout.addWidget(self.machine_label)

        self.runtime_label = QLabel("")
        self.runtime_label.setWordWrap(True)
        self.runtime_label.setProperty("role", "muted")
        layout.addWidget(self.runtime_label)

        note = QLabel("Le modèle est téléchargé une seule fois. Ensuite, la réécriture "
                      "fonctionne entièrement hors ligne : le texte ne quitte jamais cet "
                      "ordinateur.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout.addWidget(note)

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

    # -------------------------------------------------------------- liste
    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        machine = llm_models.resources()
        pieces = []
        if machine.total_ram_gb:
            pieces.append(f"RAM : {machine.total_ram_gb:.0f} Go "
                          f"(dont {machine.available_ram_gb:.0f} Go libres)")
        else:
            pieces.append("RAM : non mesurable sur cet ordinateur")
        if machine.gpu_name:
            vram = f" ({machine.vram_gb:.0f} Go)" if machine.vram_gb else ""
            pieces.append(f"GPU : {machine.gpu_name}{vram}")
        if machine.free_disk_gb:
            pieces.append(f"Disque libre : {machine.free_disk_gb:.0f} Go")
        self.machine_label.setText("Votre PC — " + "  •  ".join(pieces))

        if LlamaCppProvider.library_available():
            self.runtime_label.setText("Moteur de réécriture présent dans cette version.")
        else:
            self.runtime_label.setText(
                "⚠️ Le moteur de réécriture n'est pas disponible dans cette version de "
                "l'application : un modèle téléchargé ici ne pourrait pas être utilisé.")

        installed = set(llm_models.installed_keys())
        for model in llm_models.catalogue():
            self.list_layout.addWidget(self._model_row(model, model.key in installed, machine))
        if not llm_models.catalogue():
            empty = QLabel("Aucun modèle au catalogue.")
            empty.setProperty("role", "muted")
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch(1)

        count = len(installed)
        size = llm_models.installed_size_bytes() / 1024 ** 3
        self.usage_label.setText(
            f"{count} modèle{'s' if count > 1 else ''} installé{'s' if count > 1 else ''}"
            f"  •  {size:.1f} Go utilisés")

    def _model_row(self, model, installed: bool, machine) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        name = QLabel(model.label)
        name.setStyleSheet("font-weight: 600;")
        name.setWordWrap(True)
        top.addWidget(name, stretch=1)
        state = QLabel("✅ Installé" if installed else "Non installé")
        state.setProperty("role", "muted")
        top.addWidget(state)
        layout.addLayout(top)

        details = [f"{model.parameters} • {model.quantization}"]
        if installed:
            details.append(f"{llm_models.model_path(model.key).stat().st_size / 1024 ** 3:.1f} Go "
                           "sur le disque")
        elif model.size_gb:
            details.append(f"environ {model.size_gb:.1f} Go à télécharger")
        if model.ram_gb:
            details.append(f"RAM conseillée : {model.ram_gb:.0f} Go")
        if model.licence:
            details.append(f"licence {model.licence}")
        detail = QLabel("  •  ".join(details))
        detail.setProperty("role", "muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        if model.notes:
            notes = QLabel(model.notes)
            notes.setProperty("role", "muted")
            notes.setWordWrap(True)
            layout.addWidget(notes)

        verdict = QLabel(llm_models.VERDICT_LABELS[llm_models.verdict(model, machine)])
        layout.addWidget(verdict)

        actions = QHBoxLayout()
        actions.addStretch(1)
        if installed:
            remove = QPushButton("Supprimer")
            remove.clicked.connect(lambda _c=False, k=model.key: self._remove(k))
            actions.addWidget(remove)
        else:
            download = QPushButton("Télécharger")
            download.setProperty("variant", "primary")
            download.clicked.connect(lambda _c=False, k=model.key: self._download(k))
            actions.addWidget(download)
        layout.addLayout(actions)
        return frame

    # ------------------------------------------------------------ actions
    def _download(self, key: str) -> None:
        if self._worker is not None:
            return
        model = llm_models.describe(key)
        machine = llm_models.resources()
        message = (f"« {model.label} » va être téléchargé "
                   f"({model.size_gb:.1f} Go environ).\n\n"
                   "Une fois installé, la réécriture fonctionne hors ligne.\n\nContinuer ?")
        if machine.free_disk_gb and model.size_gb and machine.free_disk_gb < model.size_gb + 1:
            message = (f"⚠️ Il ne reste que {machine.free_disk_gb:.1f} Go libres sur le "
                       f"disque, et ce modèle en demande environ {model.size_gb:.1f} Go.\n\n"
                       + message)
        if QMessageBox.question(self, "Télécharger ce modèle", message,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return

        self._cancel_token = CancelToken()
        self._worker = ModelDownloadWorker(key, self._cancel_token)
        self._worker.progressed.connect(self._on_progress)
        self._worker.done.connect(lambda k: self.status.setText(f"Modèle installé : {k}"))
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.status.setText("Téléchargement en cours...")
        self._worker.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.status.setText("Annulation en cours...")

    def _on_progress(self, fraction, done_gb, total_gb) -> None:
        if fraction is None:
            self.progress.setRange(0, 0)
            self.status.setText(f"Téléchargement : {done_gb:.2f} Go")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
            self.status.setText(f"Téléchargement : {done_gb:.2f} / {total_gb:.2f} Go")

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

    def _remove(self, key: str) -> None:
        if QMessageBox.question(
                self, "Supprimer ce modèle",
                f"Supprimer définitivement « {llm_models.describe(key).label} » "
                "de cet ordinateur ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        # Le modele peut etre charge en memoire : on le libere avant d'effacer
        # son fichier, sinon Windows refuse la suppression.
        LlamaCppProvider().unload()
        llm_models.remove(key)
        self.status.setText("Modèle supprimé.")
        self.refresh()

    def cleanup(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(4000)

    def reject(self) -> None:                          # pragma: no cover - interaction
        self.cleanup()
        super().reject()
