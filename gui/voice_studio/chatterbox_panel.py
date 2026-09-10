"""Reglages Chatterbox dans le bloc « Generer une voix ».

Volontairement court : un style, deux curseurs, et un repli « Parametres
avances » pour ce qui ne sert pas tous les jours. Les noms affiches sont en
francais (« Expressivite », « Rythme ») mais les valeurs partent telles quelles
vers les parametres du modele (`exaggeration`, `cfg_weight`) : renommer dans le
code aurait rendu la correspondance invisible.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from voice_studio import chatterbox_catalogue as catalogue

# Les curseurs travaillent en entiers ; les valeurs du modele sont des reels.
SCALE = 100


class ChatterboxPanel(QWidget):
    """Expose `params()`. Ne genere rien : c'est le bloc de voix qui le fait."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reference = ""
        bounds = catalogue.limits()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style"))
        self.preset_combo = QComboBox()
        for preset in catalogue.presets():
            self.preset_combo.addItem(preset.label, preset.key)
        default = catalogue.defaults().get("preset", "")
        index = self.preset_combo.findData(default)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        style_row.addWidget(self.preset_combo)

        style_row.addWidget(QLabel("Expressivité"))
        self.exaggeration = QSlider(Qt.Orientation.Horizontal)
        self.exaggeration.setRange(int(bounds["exaggeration"][0] * SCALE),
                                   int(bounds["exaggeration"][1] * SCALE))
        self.exaggeration.setFixedWidth(120)
        self.exaggeration.valueChanged.connect(self._update_labels)
        style_row.addWidget(self.exaggeration)
        self.exaggeration_label = QLabel("")
        style_row.addWidget(self.exaggeration_label)

        style_row.addWidget(QLabel("Rythme"))
        self.cfg_weight = QSlider(Qt.Orientation.Horizontal)
        self.cfg_weight.setRange(int(bounds["cfg_weight"][0] * SCALE),
                                 int(bounds["cfg_weight"][1] * SCALE))
        self.cfg_weight.setFixedWidth(120)
        self.cfg_weight.valueChanged.connect(self._update_labels)
        style_row.addWidget(self.cfg_weight)
        self.cfg_label = QLabel("")
        style_row.addWidget(self.cfg_label)
        style_row.addStretch(1)
        layout.addLayout(style_row)

        self.warning = QLabel(
            "Au-delà de 1,2, l'expressivité peut produire des résultats instables.")
        self.warning.setProperty("role", "muted")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        layout.addWidget(self.warning)

        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("🎙️ Voix"))
        self.voice_mode = QComboBox()
        self.voice_mode.addItem("Voix intégrée", "")
        self.voice_mode.addItem("Fichier de référence", "reference")
        self.voice_mode.currentIndexChanged.connect(self._update_labels)
        reference_row.addWidget(self.voice_mode)

        self.reference_label = QLabel("")
        self.reference_label.setProperty("role", "muted")
        self.reference_label.setWordWrap(True)
        reference_row.addWidget(self.reference_label, stretch=1)

        self.pick_btn = QPushButton("Sélectionner…")
        self.pick_btn.clicked.connect(self._pick_reference)
        reference_row.addWidget(self.pick_btn)
        self.play_btn = QPushButton("Écouter")
        self.play_btn.clicked.connect(self._play_reference)
        reference_row.addWidget(self.play_btn)
        self.clear_btn = QPushButton("Supprimer")
        self.clear_btn.clicked.connect(self._clear_reference)
        reference_row.addWidget(self.clear_btn)
        layout.addLayout(reference_row)

        self.rights = QLabel(
            "Utilisez uniquement une voix pour laquelle vous disposez des droits ou de "
            "l'autorisation nécessaires.")
        self.rights.setProperty("role", "muted")
        self.rights.setWordWrap(True)
        layout.addWidget(self.rights)

        self.advanced_check = QCheckBox("Paramètres avancés")
        self.advanced_check.toggled.connect(self._update_labels)
        layout.addWidget(self.advanced_check)

        self.advanced = QFrame()
        advanced_row = QHBoxLayout(self.advanced)
        advanced_row.setContentsMargins(0, 0, 0, 0)
        advanced_row.addWidget(QLabel("Température"))
        self.temperature = QSlider(Qt.Orientation.Horizontal)
        self.temperature.setRange(int(bounds["temperature"][0] * SCALE),
                                  int(bounds["temperature"][1] * SCALE))
        self.temperature.setFixedWidth(120)
        self.temperature.valueChanged.connect(self._update_labels)
        advanced_row.addWidget(self.temperature)
        self.temperature_label = QLabel("")
        advanced_row.addWidget(self.temperature_label)

        advanced_row.addWidget(QLabel("Graine"))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_147_483_647)
        self.seed_spin.setSpecialValueText("Aléatoire")
        advanced_row.addWidget(self.seed_spin)
        advanced_row.addStretch(1)
        self.advanced.setVisible(False)
        layout.addWidget(self.advanced)

        self._apply_preset()

    # ------------------------------------------------------------- etats
    def _apply_preset(self) -> None:
        preset = catalogue.preset(self.preset_combo.currentData() or "")
        if preset is not None:
            self.exaggeration.setValue(int(preset.exaggeration * SCALE))
            self.cfg_weight.setValue(int(preset.cfg_weight * SCALE))
            self.temperature.setValue(int(preset.temperature * SCALE))
        self._update_labels()

    def _update_labels(self) -> None:
        self.exaggeration_label.setText(f"{self.exaggeration.value() / SCALE:.2f}")
        self.cfg_label.setText(f"{self.cfg_weight.value() / SCALE:.2f}")
        self.temperature_label.setText(f"{self.temperature.value() / SCALE:.2f}")
        self.warning.setVisible(self.exaggeration.value() / SCALE > 1.2)
        self.advanced.setVisible(self.advanced_check.isChecked())

        uses_reference = self.voice_mode.currentData() == "reference"
        for widget in (self.pick_btn, self.play_btn, self.clear_btn):
            widget.setEnabled(uses_reference)
        self.rights.setVisible(uses_reference)
        if uses_reference:
            self.reference_label.setText(Path(self._reference).name if self._reference
                                         else "aucun fichier choisi")
        else:
            self.reference_label.setText("")
        self.changed.emit()

    # -------------------------------------------------------- reference
    def _pick_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Voix de référence", str(Path.home()),
            "Audio (*.wav *.mp3 *.m4a *.flac *.ogg);;Tous les fichiers (*)")
        if not path:
            return
        self._reference = self._as_wav(path)
        self._update_labels()

    def _as_wav(self, path: str) -> str:
        """Chatterbox lit le fichier avec librosa, qui gere le WAV sans rien de
        plus. Pour les autres formats on convertit AVEC FFMPEG, deja livre avec
        l'application, plutot que de compter sur une bibliotheque optionnelle."""
        source = Path(path)
        if source.suffix.lower() == ".wav":
            return str(source)
        from voice_studio import chatterbox_models as models_module

        target = models_module.models_dir().parent / "chatterbox_reference.wav"
        try:
            from video.ffmpeg_utils import run_ffmpeg

            target.parent.mkdir(parents=True, exist_ok=True)
            run_ffmpeg(["-y", "-i", str(source), "-ac", "1", "-ar", "24000", str(target)],
                       description="conversion de la voix de référence")
            return str(target)
        except Exception:
            # La conversion a echoue : on garde le fichier d'origine et c'est
            # la generation qui dira si le format ne passe pas -- plutot que de
            # refuser un fichier qui aurait peut-etre marche.
            return str(source)

    def _play_reference(self) -> None:
        if not (self._reference and Path(self._reference).is_file()):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._reference))

    def _clear_reference(self) -> None:
        self._reference = ""
        self._update_labels()

    # ------------------------------------------------------------ sortie
    def params(self) -> catalogue.Params:
        reference = self._reference if self.voice_mode.currentData() == "reference" else ""
        return catalogue.params_for(
            self.preset_combo.currentData() or "",
            exaggeration=self.exaggeration.value() / SCALE,
            cfg_weight=self.cfg_weight.value() / SCALE,
            temperature=self.temperature.value() / SCALE,
            seed=self.seed_spin.value(),
            reference=reference,
        )
