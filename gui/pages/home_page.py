"""Page Accueil -- deposer une video, choisir 3 reglages rapides, lancer.
Les reglages avances (pre/post-roll, min-gap, style de sous-titres, GPU/CPU)
vivent dans Paramètres, pas ici : le cahier des charges est explicite sur le
fait que cette page doit rester simple (section 19 -- "simplicite + rapidite
+ lisibilite")."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import settings_store
from gui.branding import APP_TAGLINE
from gui.controller import AppController
from gui.widgets.drop_zone import DropZone
from utils.hardware import cuda_device_count

_DURATION_PRESETS = [15, 30, 45, 60]

_MODEL_CHOICES = [
    ("tiny", "Tiny", "Très rapide • qualité basique • ~1 Go RAM"),
    ("base", "Base", "Rapide • qualité correcte • ~1 Go RAM"),
    ("small", "Small", "Bon compromis vitesse/qualité • ~2 Go RAM"),
    ("medium", "Medium", "Lent sur CPU • bonne qualité • ~5 Go RAM"),
    ("large-v3", "Large", "Très lent sur CPU • meilleure qualité • ~10 Go RAM"),
]


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 12.5px; font-weight: 600; margin-top: 4px;")
    return label


class HomePage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.selected_video_path: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 36, 40, 36)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel(APP_TAGLINE)
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)
        outer.addSpacing(20)

        card = QFrame()
        card.setProperty("role", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(14)
        card.setMaximumWidth(620)
        outer.addWidget(card)

        self.drop_zone = DropZone()
        self.drop_zone.file_selected.connect(self._on_file_selected)
        card_layout.addWidget(self.drop_zone)

        # --- Duree des clips ---
        card_layout.addWidget(_field_label("Durée des clips"))
        duration_row = QHBoxLayout()
        self.duration_combo = QComboBox()
        for seconds in _DURATION_PRESETS:
            self.duration_combo.addItem(f"{seconds} secondes", seconds)
        self.duration_combo.addItem("Personnalisée", -1)
        self.duration_combo.setCurrentIndex(_DURATION_PRESETS.index(45))
        self.duration_combo.currentIndexChanged.connect(self._on_duration_changed)
        duration_row.addWidget(self.duration_combo, stretch=1)

        self.custom_duration_spin = QSpinBox()
        self.custom_duration_spin.setRange(5, 600)
        self.custom_duration_spin.setValue(45)
        self.custom_duration_spin.setSuffix(" s")
        self.custom_duration_spin.setVisible(False)
        duration_row.addWidget(self.custom_duration_spin)
        card_layout.addLayout(duration_row)

        # --- Nombre de clips ---
        card_layout.addWidget(_field_label("Nombre de clips"))
        self.nb_clips_spin = QSpinBox()
        self.nb_clips_spin.setRange(1, 50)
        self.nb_clips_spin.setValue(5)
        card_layout.addWidget(self.nb_clips_spin)

        # --- Modele Whisper ---
        card_layout.addWidget(_field_label("Modèle Whisper"))
        self.model_combo = QComboBox()
        for key, label, _hint in _MODEL_CHOICES:
            self.model_combo.addItem(label, key)
        default_model = settings_store.get("default_model")
        default_index = next((i for i, (k, _, _) in enumerate(_MODEL_CHOICES) if k == default_model), 2)
        self.model_combo.setCurrentIndex(default_index)
        self.model_combo.currentIndexChanged.connect(self._update_model_hint)
        card_layout.addWidget(self.model_combo)

        self.model_hint = QLabel("")
        self.model_hint.setProperty("role", "muted")
        card_layout.addWidget(self.model_hint)
        self._update_model_hint()

        # --- GPU / CPU (information seule -- le choix se fait dans Parametres) ---
        gpu_count = cuda_device_count()
        gpu_text = f"GPU détecté ✓ ({gpu_count})" if gpu_count > 0 else "Aucun GPU compatible détecté — traitement CPU"
        gpu_label = QLabel(gpu_text)
        gpu_label.setProperty("role", "muted")
        card_layout.addWidget(gpu_label)

        card_layout.addSpacing(8)
        self.generate_btn = QPushButton("GÉNÉRER LES CLIPS")
        self.generate_btn.setProperty("variant", "primary")
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.setMinimumHeight(44)
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        card_layout.addWidget(self.generate_btn)

    def _on_duration_changed(self) -> None:
        is_custom = self.duration_combo.currentData() == -1
        self.custom_duration_spin.setVisible(is_custom)

    def _update_model_hint(self) -> None:
        key = self.model_combo.currentData()
        hint = next((h for k, _, h in _MODEL_CHOICES if k == key), "")
        self.model_hint.setText(hint)

    def _on_file_selected(self, path: str) -> None:
        self.selected_video_path = path
        self.generate_btn.setEnabled(True)

    def _clip_duration(self) -> int:
        data = self.duration_combo.currentData()
        return self.custom_duration_spin.value() if data == -1 else int(data)

    def _on_generate_clicked(self) -> None:
        if not self.selected_video_path:
            return

        cli_args = SimpleNamespace(
            input=self.selected_video_path,
            clip_duration=self._clip_duration(),
            nb_clips=self.nb_clips_spin.value(),
            model=self.model_combo.currentData(),
            language=None,
            pre_roll=None,
            post_roll=None,
            min_gap=None,
            subtitle_style=settings_store.get("default_subtitle_style"),
            device=settings_store.get("default_device"),
            no_cache=False,
            debug_scores=False,
        )
        name = Path(self.selected_video_path).stem
        self.controller.start_analysis(cli_args, name=name, source_label=Path(self.selected_video_path).name, source_kind="local")

    def on_shown(self) -> None:
        pass
