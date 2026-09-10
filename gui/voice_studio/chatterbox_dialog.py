"""Fenetre « Chatterbox » : installer, telecharger, verifier, supprimer.

Elle ne cache rien : elle affiche ce qui est installe, ce qui manque, ou les
fichiers sont ranges, ce que l'environnement repond quand on l'interroge, et
combien pese le telechargement. Rien ne part avant un clic.

Separee de la fenetre des voix Piper a dessein : Piper installe des voix, ici
on installe un environnement Python et un modele. Melanger les deux aurait
produit un ecran ou la moitie des boutons ne concerne pas ce qu'on regarde.
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
    QTextEdit,
    QVBoxLayout,
)

from core.cancellation import CancelToken
from gui.voice_studio.chatterbox_workers import (
    MODE_MODEL,
    MODE_RUNTIME,
    MODE_SELFTEST,
    ChatterboxSetupWorker,
)
from voice_studio import chatterbox_catalogue as catalogue
from voice_studio import chatterbox_models as models
from voice_studio import chatterbox_runtime as runtime


def _giga(value) -> str:
    try:
        return f"{float(value) / 1_000_000_000:.1f} Go"
    except (TypeError, ValueError):
        return "inconnu"


class ChatterboxDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chatterbox — voix expressive")
        self.resize(760, 560)
        self._worker = None
        self._cancel_token = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("🗣️  CHATTERBOX MULTILINGUAL V3")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(title)

        self.intro = QLabel("")
        self.intro.setWordWrap(True)
        self.intro.setProperty("role", "muted")
        layout.addWidget(self.intro)

        self.runtime_card = self._card()
        layout.addWidget(self.runtime_card)
        self.model_card = self._card()
        layout.addWidget(self.model_card)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.journal = QTextEdit()
        self.journal.setReadOnly(True)
        self.journal.setMaximumHeight(130)
        self.journal.setVisible(False)
        layout.addWidget(self.journal)

        footer = QHBoxLayout()
        self.install_runtime_btn = QPushButton("Installer Chatterbox")
        self.install_runtime_btn.setProperty("variant", "primary")
        self.install_runtime_btn.clicked.connect(lambda: self._start(MODE_RUNTIME))
        footer.addWidget(self.install_runtime_btn)

        self.download_btn = QPushButton("Télécharger le modèle")
        self.download_btn.clicked.connect(lambda: self._start(MODE_MODEL))
        footer.addWidget(self.download_btn)

        self.check_btn = QPushButton("Vérifier")
        self.check_btn.clicked.connect(lambda: self._start(MODE_SELFTEST))
        footer.addWidget(self.check_btn)

        self.remove_btn = QPushButton("Supprimer")
        self.remove_btn.clicked.connect(self._remove)
        footer.addWidget(self.remove_btn)

        footer.addStretch(1)
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        footer.addWidget(self.cancel_btn)

        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.refresh()

    @staticmethod
    def _card() -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 12, 14, 12)
        inner.setSpacing(4)
        label = QLabel("")
        label.setWordWrap(True)
        label.setTextInteractionFlags(label.textInteractionFlags())
        inner.addWidget(label)
        frame.label = label
        return frame

    # ------------------------------------------------------------- etats
    def refresh(self) -> None:
        model_state = models.describe()
        runtime_state = runtime.describe()
        spec = catalogue.model_spec()

        self.intro.setText(
            "Voix locale plus expressive que Piper. Deux téléchargements distincts : "
            "l'environnement d'exécution, puis le modèle. Tout reste sur cet "
            "ordinateur — aucun texte n'est envoyé nulle part.\n"
            + (spec.get("watermark") or ""))

        estimated = runtime_state.get("estimated_download_mb")
        self.runtime_card.label.setText(
            f"<b>Environnement</b> — {'installé' if runtime_state['ready'] else 'absent'}"
            + (" (version différente de celle demandée)" if runtime_state["outdated"] else "")
            + f"<br>Dossier : {runtime_state['directory']}"
            + f"<br>Contenu : {', '.join(runtime_state['packages']) or '—'}, puis Chatterbox"
            + (f"<br>Téléchargement estimé : environ {estimated} Mo" if estimated else "")
            + f"<br>Version épinglée : {runtime_state['commit_expected'][:12] or '—'}")

        missing = model_state["missing"]
        self.model_card.label.setText(
            f"<b>Modèle {model_state['label']}</b> — "
            + ("installé" if model_state["installed"]
               else f"{len(missing)} fichier(s) manquant(s) sur {model_state['files_total']}")
            + f"<br>Dossier : {model_state['directory']}"
            + f"<br>Taille sur le disque : {_giga(model_state['size_bytes'])}"
            + f" — espace libre : {_giga(model_state['free_bytes'])}"
            + "<br>Taille annoncée : inconnue tant que le téléchargement n'a pas commencé"
            + f"<br>Langues : {len(model_state['languages'])}, dont le français"
            + f"<br>Licence du code : {model_state['code_license'] or 'à vérifier'}")

        running = self._worker is not None
        self.install_runtime_btn.setEnabled(not running)
        self.install_runtime_btn.setText("Réinstaller Chatterbox" if runtime_state["ready"]
                                         else "Installer Chatterbox")
        self.download_btn.setEnabled(not running and not model_state["installed"])
        self.check_btn.setEnabled(not running and runtime_state["ready"])
        self.remove_btn.setEnabled(not running
                                   and (runtime_state["ready"] or model_state["installed"]))

    # ---------------------------------------------------------- actions
    def _start(self, mode: str) -> None:
        if self._worker is not None:
            return
        if mode == MODE_RUNTIME and runtime.find_python() is None:
            QMessageBox.warning(
                self, "Python introuvable",
                "Chatterbox a besoin d'un Python 3.10 à 3.13 installé sur cet ordinateur "
                "pour créer son environnement.\n\nInstalle-le depuis python.org en cochant "
                "« Add python.exe to PATH », puis relance cette installation.")
            return

        self._cancel_token = CancelToken()
        self._worker = ChatterboxSetupWorker(mode, self._cancel_token)
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)

        self.journal.clear()
        self.journal.setVisible(mode == MODE_RUNTIME)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.status.setText({
            MODE_RUNTIME: "Installation de l'environnement Chatterbox…",
            MODE_MODEL: "Téléchargement du modèle…",
            MODE_SELFTEST: "Vérification…",
        }[mode])
        self.refresh()
        self._worker.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.cancel_btn.setEnabled(False)
        self.status.setText("Annulation en cours…")

    def _on_log(self, line: str) -> None:
        self.journal.append(line)

    def _on_progress(self, fraction, label: str) -> None:
        if fraction is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.status.setText(label)

    def _on_done(self, result: dict) -> None:
        mode = result.get("mode")
        if mode == MODE_RUNTIME:
            self.status.setText("Environnement installé. Vérifie-le pour voir ce qu'il annonce.")
        elif mode == MODE_MODEL:
            self.status.setText("Modèle téléchargé.")
        else:
            if result.get("ok"):
                self.status.setText(
                    f"Chatterbox répond — PyTorch {result.get('torch', '?')}, "
                    f"processeur : {result.get('device', '?')}, "
                    f"{len(result.get('languages') or [])} langues.")
            else:
                self.status.setText(f"⚠️ {result.get('error', 'vérification impossible')}")
        self.refresh()

    def _on_failed(self, message: str) -> None:
        self.status.setText("")
        QMessageBox.warning(self, "Chatterbox", message)

    def _on_cancelled(self) -> None:
        self.status.setText("Annulé. Ce qui a déjà été téléchargé est conservé et reprendra.")

    def _on_finished(self) -> None:
        self._worker = None
        self._cancel_token = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.refresh()

    def _remove(self) -> None:
        """Supprime l'environnement et/ou le modele, apres confirmation.

        Les deux sont proposes ensemble parce qu'ils ne servent qu'ensemble ;
        ce qui est efface est nomme, et rien d'autre n'est touche.
        """
        runtime_state = runtime.describe()
        model_state = models.describe()
        pieces = []
        if runtime_state["ready"] or runtime.python_executable().is_file():
            pieces.append(f"l'environnement ({runtime_state['directory']})")
        if model_state["installed"] or model_state["size_bytes"]:
            pieces.append(f"le modèle ({_giga(model_state['size_bytes'])})")
        if not pieces:
            return

        answer = QMessageBox.question(
            self, "Supprimer Chatterbox",
            "Supprimer " + " et ".join(pieces) + " ?\n\n"
            "Les voix Piper et les voix du système ne sont pas concernées.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        removed_files = models.remove()
        removed_runtime = runtime.remove()
        self.status.setText(
            f"Supprimé : {removed_files} fichier(s) de modèle"
            + (", environnement effacé" if removed_runtime else ""))
        self.refresh()

    def cleanup(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(4000)

    def reject(self) -> None:                          # pragma: no cover - interaction
        self.cleanup()
        super().reject()
