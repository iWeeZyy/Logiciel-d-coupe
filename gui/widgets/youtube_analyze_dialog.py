"""Dialogue "Analyser" pour une video YouTube trouvee -- meme reglages que
la page Accueil (duree/nb clips/modele), plus le verrou de consentement
explicite (section 10) : le bouton reste desactive tant que la case n'est
pas cochee, exactement comme --confirm-rights cote CLI."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from gui import settings_store
from gui.widgets.clip_card import RIGHTS_NOTICE

_DURATION_PRESETS = [15, 30, 45, 60]
_MODEL_CHOICES = [("tiny", "Tiny"), ("base", "Base"), ("small", "Small"), ("medium", "Medium"), ("large-v3", "Large")]


class YoutubeAnalyzeDialog(QDialog):
    def __init__(self, video_title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analyser cette vidéo")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(video_title)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(title)

        layout.addWidget(QLabel("Durée des clips"))
        self.duration_combo = QComboBox()
        for seconds in _DURATION_PRESETS:
            self.duration_combo.addItem(f"{seconds} secondes", seconds)
        self.duration_combo.setCurrentIndex(_DURATION_PRESETS.index(45))
        layout.addWidget(self.duration_combo)

        layout.addWidget(QLabel("Nombre de clips"))
        self.nb_clips_spin = QSpinBox()
        self.nb_clips_spin.setRange(1, 50)
        self.nb_clips_spin.setValue(5)
        layout.addWidget(self.nb_clips_spin)

        layout.addWidget(QLabel("Modèle Whisper"))
        self.model_combo = QComboBox()
        for key, label in _MODEL_CHOICES:
            self.model_combo.addItem(label, key)
        default_model = settings_store.get("default_model")
        idx = next((i for i, (k, _) in enumerate(_MODEL_CHOICES) if k == default_model), 2)
        self.model_combo.setCurrentIndex(idx)
        layout.addWidget(self.model_combo)

        notice = QLabel(RIGHTS_NOTICE.split("\n\n")[0])
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "background: #FBEEDA; color: #7A5010; border-radius: 8px; padding: 10px; font-size: 12px;"
        )
        layout.addWidget(notice)

        self.consent_checkbox = QCheckBox("Je confirme disposer des droits nécessaires pour ce traitement.")
        self.consent_checkbox.toggled.connect(self._update_ok_enabled)
        layout.addWidget(self.consent_checkbox)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Lancer l'analyse")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.consent_checkbox.isChecked())

    def clip_duration(self) -> int:
        return int(self.duration_combo.currentData())

    def nb_clips(self) -> int:
        return self.nb_clips_spin.value()

    def model(self) -> str:
        return self.model_combo.currentData()
