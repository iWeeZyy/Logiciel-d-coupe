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
    QCheckBox,
    QComboBox,
    QMessageBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import settings_store
from gui.branding import APP_TAGLINE
from content_factory import planning
from video import ffmpeg_utils
from gui.controller import AppController
from gui.widgets.drop_zone import DropZone
from gui.widgets.production_options import ProductionOptionsBox
from utils.hardware import cuda_device_count

_DURATION_PRESETS = [15, 30, 45, 60]

# Duree "automatique" : la detection de contexte ajuste ensuite chaque clip sur
# la structure du discours. On part d'une valeur mediane plutot que d'un choix
# fantaisiste -- et l'etiquette le dit, pour ne pas laisser croire a une magie
# qui devinerait la duree ideale.
_AUTO_DURATION = 45

# Modules proposes directement sur l'accueil (section 8). Les autres
# (contexte, cadrage, silences, hesitations, zoom) se reglent dans Parametres :
# les mettre tous ici transformerait l'accueil en tableau de bord.
_MODEL_CHOICES = [
    ("tiny", "Tiny", "Très rapide • qualité basique • ~1 Go RAM"),
    ("base", "Base", "Rapide • qualité correcte • ~1 Go RAM"),
    ("small", "Small", "Bon compromis vitesse/qualité • ~2 Go RAM • recommandé sans GPU"),
    ("medium", "Medium", "Lent sur CPU • bonne qualité • ~5 Go RAM • ~1,5 Go à télécharger"),
    ("large-v3", "Large", "Très lent sur CPU • meilleure qualité • ~10 Go RAM • ~3 Go à télécharger"),
]

# Modeles dont le cout sur processeur justifie une confirmation explicite : sans
# GPU, ils transcrivent souvent plus lentement que la duree de la video, et
# l'utilisateur lancerait des heures de calcul sans l'avoir voulu.
_HEAVY_MODELS = {"medium", "large", "large-v2", "large-v3"}


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 12.5px; font-weight: 600; margin-top: 4px;")
    return label


class HomePage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.selected_video_path: str | None = None
        self.video_duration_s: float | None = None

        # La page defile. Sans cela, des que la carte depassait la hauteur de la
        # fenetre, Qt comprimait les widgets les uns sur les autres : les champs
        # "Duree" et "Nombre de clips" se retrouvaient PAR-DESSUS la zone de
        # depot. Une page trop haute doit defiler, pas se replier sur elle-meme.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        page_layout.addWidget(scroll)

        outer = QVBoxLayout(content)
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
        self.duration_combo.addItem(f"Automatique (~{_AUTO_DURATION} s, ajustée au contexte)", 0)
        for seconds in _DURATION_PRESETS:
            self.duration_combo.addItem(f"{seconds} secondes", seconds)
        self.duration_combo.addItem("Personnalisée", -1)
        self.duration_combo.setCurrentIndex(0)
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
        nb_row = QHBoxLayout()
        self.nb_clips_auto = QCheckBox("Automatique")
        self.nb_clips_auto.setChecked(True)
        self.nb_clips_auto.toggled.connect(self._on_nb_clips_mode_changed)
        nb_row.addWidget(self.nb_clips_auto)

        self.nb_clips_spin = QSpinBox()
        self.nb_clips_spin.setRange(1, 50)
        self.nb_clips_spin.setValue(planning.MIN_CLIPS)
        self.nb_clips_spin.setEnabled(False)
        nb_row.addWidget(self.nb_clips_spin, stretch=1)
        card_layout.addLayout(nb_row)

        # Le chiffre propose ne doit pas tomber du ciel : on dit d'ou il vient.
        self.nb_clips_hint = QLabel("Déduit de la durée de la vidéo une fois celle-ci choisie.")
        self.nb_clips_hint.setProperty("role", "muted")
        card_layout.addWidget(self.nb_clips_hint)

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

        # --- Options de production ---
        # La ligne "Format : 9:16 (1080 x 1920)" qui se trouvait ici annoncait
        # un fait ; c'est devenu un choix, et la laisser aurait affiche deux
        # formats a l'ecran, l'un fixe et l'autre reglable.
        card_layout.addWidget(_field_label("Édition automatique"))
        # Trois colonnes : six cases sur une seule ligne dans une carte de
        # 620 px tronquaient les libelles ("Cadrage int...", "Montage au...").
        self.production_options = ProductionOptionsBox(columns=3)
        card_layout.addWidget(self.production_options)

        modules_hint = QLabel(
            "Cadrage suivi, contexte, silences et zooms se règlent dans Paramètres."
        )
        modules_hint.setProperty("role", "muted")
        card_layout.addWidget(modules_hint)

        card_layout.addSpacing(8)
        self.generate_btn = QPushButton("🚀  CRÉER LES CONTENUS")
        self.generate_btn.setProperty("variant", "primary")
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.setMinimumHeight(52)
        self.generate_btn.setStyleSheet("font-size: 15px; font-weight: 800;")
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

    def _on_nb_clips_mode_changed(self, automatic: bool) -> None:
        self.nb_clips_spin.setEnabled(not automatic)
        self._refresh_nb_clips_hint()

    def _refresh_nb_clips_hint(self) -> None:
        if not self.nb_clips_auto.isChecked():
            self.nb_clips_hint.setText("Nombre fixé manuellement.")
            return
        if self.video_duration_s is None:
            self.nb_clips_hint.setText("Déduit de la durée de la vidéo une fois celle-ci choisie.")
            return
        self.nb_clips_hint.setText(planning.describe(self.video_duration_s))

    def _on_file_selected(self, path: str) -> None:
        self.selected_video_path = path
        self.generate_btn.setEnabled(True)

        # Duree lue tout de suite : c'est elle qui permet de proposer un nombre
        # de clips. Une video illisible par ffprobe ne doit pas empecher de
        # lancer -- le pipeline le signalera bien mieux que l'accueil.
        try:
            self.video_duration_s = ffmpeg_utils.video_duration(path)
        except Exception:
            self.video_duration_s = None
        if self.video_duration_s:
            self.nb_clips_spin.setValue(planning.suggested_clip_count(self.video_duration_s))
        self._refresh_nb_clips_hint()

    def _clip_duration(self) -> int:
        data = self.duration_combo.currentData()
        if data == -1:
            return self.custom_duration_spin.value()
        return _AUTO_DURATION if data == 0 else int(data)

    def _editing_overrides(self) -> dict:
        return self.production_options.editing_overrides()

    def _confirm_heavy_model(self) -> bool:
        """Sans GPU, un gros modele peut transcrire plus lentement que la duree
        de la video. Mieux vaut le dire avant de lancer que de laisser
        l'utilisateur decouvrir au bout d'une heure que ce n'est pas fini."""
        model = self.model_combo.currentData()
        if model not in _HEAVY_MODELS or cuda_device_count() > 0:
            return True

        reply = QMessageBox.warning(
            self,
            "Modèle lourd sans GPU",
            f"Aucun GPU n'a été détecté sur cette machine.\n\n"
            f"Sur processeur, le modèle « {self.model_combo.currentText()} » transcrit souvent "
            f"plus lentement que la durée de la vidéo elle-même, et doit d'abord être "
            f"téléchargé (plusieurs Go au premier lancement).\n\n"
            f"Le modèle « Small » donne une très bonne qualité en une fraction du temps.\n\n"
            f"Lancer quand même avec ce modèle ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_generate_clicked(self) -> None:
        if not self.selected_video_path:
            return
        if not self._confirm_heavy_model():
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
            aspect=self.production_options.aspect(),
            fill_mode=self.production_options.fill_mode(),
            fit_mode=self.production_options.fit_mode(),
        )
        name = Path(self.selected_video_path).stem
        self.controller.start_analysis(
            cli_args, name=name, source_label=Path(self.selected_video_path).name,
            source_kind="local", editing_overrides=self._editing_overrides(),
        )

    def on_shown(self) -> None:
        pass
