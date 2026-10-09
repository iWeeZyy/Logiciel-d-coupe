"""Fenetre « IA Claude » : saisir la cle API Anthropic qui permet au logiciel
de lire les articles cine et d'en rediger l'info principale
(news_story/ai_summary.py).

La cle est rangee dans le coffre de Windows (Gestionnaire d'identifiants),
jamais dans un fichier, le depot ou le .exe, et n'est jamais reaffichee en
clair : seuls son debut et sa fin servent a la reconnaitre.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from news_story import ai_summary


class _TestThread(QThread):
    done = Signal(bool, str)

    def __init__(self, key: str, model: str):
        super().__init__()
        self.key, self.model = key, model

    def run(self) -> None:
        try:
            ai_summary.test_key(self.key, self.model)
        except ai_summary.AiSummaryError as error:
            self.done.emit(False, str(error))
        else:
            self.done.emit(True, "Clé valide : Claude répond.")


class ClaudeKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IA Claude — news ciné")
        self.setMinimumWidth(520)
        self._thread: _TestThread | None = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Avec une clé API Claude, le logiciel lit chaque article ciné et écrit sous le "
            "titre l'information principale, et la réponse quand le titre pose une question "
            "ou cache l'info. Claude n'utilise que le contenu de l'article.\n\n"
            "Clé à créer sur console.anthropic.com (API Keys). Chaque article lu est facturé "
            "sur ton compte Anthropic (Économique : environ 0,1 centime par article). "
            "Pense à fixer une limite de dépense dans la console.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        current = ai_summary.load_api_key()
        self.key_edit.setPlaceholderText(
            f"Clé enregistrée : {ai_summary.mask_key(current)} — colle une nouvelle clé pour la remplacer"
            if current else "sk-ant-…")
        form.addRow("Clé API :", self.key_edit)
        self.model_combo = QComboBox()
        for model, label in ai_summary.MODELS.items():
            self.model_combo.addItem(label, model)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(ai_summary.load_model())))
        form.addRow("Modèle :", self.model_combo)
        layout.addLayout(form)

        self.status_label = QLabel(
            "IA activée." if current else "IA désactivée : texte tiré des articles, sans IA.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("Tester")
        self.test_btn.clicked.connect(self._test)
        buttons.addWidget(self.test_btn)
        self.delete_btn = QPushButton("Supprimer la clé")
        self.delete_btn.setEnabled(bool(current))
        self.delete_btn.clicked.connect(self._delete)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        save_btn = QPushButton("Enregistrer")
        save_btn.setProperty("variant", "primary")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _typed_or_saved_key(self) -> str:
        return self.key_edit.text().strip() or ai_summary.load_api_key()

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        if key:
            if not ai_summary.save_api_key(key):
                from publishing.tokens import UNAVAILABLE_MESSAGE

                self.status_label.setText("Clé non enregistrée. " + UNAVAILABLE_MESSAGE)
                return
            self.key_edit.clear()
            self.key_edit.setPlaceholderText(f"Clé enregistrée : {ai_summary.mask_key(key)}")
        ai_summary.save_model(self.model_combo.currentData())
        self.delete_btn.setEnabled(ai_summary.is_configured())
        self.status_label.setText("Enregistré. IA activée." if ai_summary.is_configured()
                                  else "Modèle enregistré. Aucune clé : IA désactivée.")

    def _delete(self) -> None:
        ai_summary.save_api_key("")
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("sk-ant-…")
        self.delete_btn.setEnabled(False)
        self.status_label.setText("Clé supprimée de ce PC. IA désactivée." if not
                                  ai_summary.is_configured() else
                                  "Clé du coffre supprimée, mais la variable d'environnement "
                                  "ANTHROPIC_API_KEY est encore définie.")

    def _test(self) -> None:
        key = self._typed_or_saved_key()
        if not key:
            self.status_label.setText("Colle d'abord une clé.")
            return
        self.test_btn.setEnabled(False)
        self.status_label.setText("Test en cours…")
        self._thread = _TestThread(key, self.model_combo.currentData())
        self._thread.done.connect(self._on_tested)
        self._thread.start()

    def _on_tested(self, ok: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.status_label.setText(("✅ " if ok else "❌ ") + message)
        self._thread = None

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(30000)
        super().reject()
