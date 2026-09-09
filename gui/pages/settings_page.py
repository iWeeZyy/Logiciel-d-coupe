"""Page Paramètres (section 12). Modele/peripherique/dossier/sous-titres sont
des preferences GUI (gui/settings_store.py). Ponderation et mots-cles
modifient DIRECTEMENT config/settings.json et config/hooks_keywords.json --
les memes fichiers que lit le moteur (CLI et GUI partagent une seule source
de verite), jamais une copie GUI qui divergerait."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import CONFIG_DIR
from gui import settings_store
from gui.controller import AppController
from utils.hardware import cuda_device_count

_MODEL_CHOICES = [("tiny", "Tiny"), ("base", "Base"), ("small", "Small"), ("medium", "Medium"), ("large-v3", "Large")]
_DEVICE_CHOICES = [("auto", "Automatique"), ("cpu", "CPU"), ("cuda", "GPU (CUDA)")]
# La qualite, c'est le CRF. Le preset ne regle que l'EFFORT de l'encodeur : a
# CRF egal, "slow" ne rend pas une meilleure image, il cherche plus longtemps
# une facon de la compresser. Mesure sur un clip reel de 24 s en 1080x1920 :
# slow 54 s pour 25,2 Mo et un SSIM de 0,9892, medium 30 s pour 25,4 Mo et un
# SSIM de 0,9893. Presque deux fois plus de temps pour 0,2 Mo, donc medium des
# deux cotes.
_QUALITY_CHOICES = [("standard", "Standard", 23, "medium"), ("high", "Haute qualité", 18, "medium")]

_WEIGHT_KEYS = [
    ("audio", "Audio"),
    ("keywords", "Mots-clés"),
    ("questions", "Questions"),
    ("speech_density", "Densité de parole"),
    ("silence_build_up", "Silence / buildup"),
    ("intensity", "Intensité"),
]


def _settings_json_path():
    return CONFIG_DIR / "settings.json"


def _keywords_json_path():
    return CONFIG_DIR / "hooks_keywords.json"


def _subtitles_json_path():
    return CONFIG_DIR / "subtitles.json"


def _editing_json_path():
    return CONFIG_DIR / "editing.json"


# Les interrupteurs de la section 12, chacun designe par son chemin reel
# dans config/editing.json. Ecrire ce chemin ici plutot qu'une correspondance
# dans le code evite d'avoir un jour une case qui ne pilote plus rien.
_EDITING_MODULES = [
    (("context_detection", "enabled"), "Détection du contexte",
     "Recale le début et la fin de chaque clip sur les phrases réellement prononcées."),
    (("framing", "enabled"), "Cadrage intelligent",
     "Le recadrage 9:16 suit le sujet au lieu de rester figé."),
    (("montage", "remove_silences", "enabled"), "Suppression des silences",
     "Retire les blancs inutiles entre deux phrases, jamais les pauses volontaires."),
    (("montage", "remove_fillers", "enabled"), "Suppression des hésitations",
     "Retire les « euh », « hmm » et les faux départs."),
    (("montage", "dynamic_zoom", "enabled"), "Zoom dynamique",
     "Léger zoom sur les moments forts, bornés en nombre et en amplitude."),
    (("captions", "enabled"), "Sous-titres intelligents",
     "Découpage adapté, mots importants mis en évidence, placement évitant le visage."),
    (("metadata", "enabled"), "Titres automatiques",
     "Trois propositions extraites du contenu réel du clip."),
    (("metadata", "descriptions"), "Descriptions automatiques",
     "Description et hashtags tirés uniquement de ce qui est dit dans le clip."),
    (("thumbnails", "enabled"), "Miniatures automatiques",
     "Trois miniatures 1080×1920 par clip."),
    (("watermark", "enabled"), "Filigrane",
     "Pose le logo en transparence en haut à droite de chaque clip."),
]


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "sectionLabel")
    return label


class SettingsPage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Paramètres")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)
        outer.addSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        container = QWidget()
        self.col = QVBoxLayout(container)
        self.col.setSpacing(18)
        self.col.setAlignment(Qt.AlignmentFlag.AlignTop)
        container.setMaximumWidth(640)
        scroll.setWidget(container)

        self._build_general_card()
        self._build_editing_card()
        self._build_video_card()
        self._build_tts_card()
        self._build_weights_card()
        self._build_keywords_card()

    # ---------- Modules d'edition automatique ----------

    def _build_editing_card(self) -> None:
        card = self._card()
        card.layout().addWidget(_section_title("ÉDITION AUTOMATIQUE"))

        intro = QLabel(
            "Chaque module peut être désactivé indépendamment. Désactivé, il rend "
            "exactement le comportement d'avant son ajout — jamais un résultat dégradé."
        )
        intro.setProperty("role", "muted")
        intro.setWordWrap(True)
        card.layout().addWidget(intro)

        try:
            config = _load_json(_editing_json_path())
        except (OSError, json.JSONDecodeError):
            # config/editing.json absent ou illisible : l'application fonctionne
            # sans (tous modules eteints), la page ne doit pas planter pour autant.
            missing = QLabel("config/editing.json introuvable — modules indisponibles.")
            missing.setProperty("role", "muted")
            card.layout().addWidget(missing)
            return

        self._editing_boxes: list[tuple[tuple, QCheckBox]] = []
        for path, label, hint in _EDITING_MODULES:
            box = QCheckBox(label)
            box.setChecked(bool(self._read_path(config, path)))
            box.setToolTip(hint)
            box.toggled.connect(self._save_editing_modules)
            card.layout().addWidget(box)
            self._editing_boxes.append((path, box))

    @staticmethod
    def _read_path(config: dict, path: tuple):
        node = config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        return node

    def _save_editing_modules(self) -> None:
        try:
            config = _load_json(_editing_json_path())
        except (OSError, json.JSONDecodeError):
            return
        for path, box in self._editing_boxes:
            node = config
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = box.isChecked()
        _save_json(_editing_json_path(), config)

    # ---------- Général (modele/peripherique/dossier/sous-titres) ----------

    def _build_general_card(self) -> None:
        card = self._card()
        card.layout().addWidget(_section_title("GÉNÉRAL"))

        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        row = 0

        grid.addWidget(QLabel("Modèle Whisper par défaut"), row, 0)
        self.model_combo = QComboBox()
        for key, label in _MODEL_CHOICES:
            self.model_combo.addItem(label, key)
        self._select(self.model_combo, settings_store.get("default_model"))
        self.model_combo.currentIndexChanged.connect(
            lambda: settings_store.save({"default_model": self.model_combo.currentData()})
        )
        grid.addWidget(self.model_combo, row, 1)
        row += 1

        gpu_count = cuda_device_count()
        gpu_text = f"GPU détecté ✓ ({gpu_count})" if gpu_count > 0 else "GPU compatible non détecté"
        grid.addWidget(QLabel("Traitement"), row, 0)
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        for key, label in _DEVICE_CHOICES:
            self.device_combo.addItem(label, key)
        self._select(self.device_combo, settings_store.get("default_device"))
        self.device_combo.currentIndexChanged.connect(
            lambda: settings_store.save({"default_device": self.device_combo.currentData()})
        )
        device_row.addWidget(self.device_combo)
        gpu_label = QLabel(gpu_text)
        gpu_label.setProperty("role", "muted")
        device_row.addWidget(gpu_label)
        grid.addLayout(device_row, row, 1)
        row += 1

        grid.addWidget(QLabel("Style de sous-titres par défaut"), row, 0)
        self.subtitle_combo = QComboBox()
        # Liste construite depuis config/subtitles.json plutot qu'ecrite ici :
        # une liste codee en dur n'aurait jamais montre les styles ajoutes
        # ensuite, et divergeait deja de la config.
        subtitles_config = _load_json(_subtitles_json_path())
        for key, style in subtitles_config.get("styles", {}).items():
            label = key.replace("_", " ").capitalize()
            if style.get("mode") == "smart":
                label += " (intelligent)"
            self.subtitle_combo.addItem(label, key)
            self.subtitle_combo.setItemData(
                self.subtitle_combo.count() - 1, style.get("description", ""), Qt.ItemDataRole.ToolTipRole
            )
        # Aucun choix enregistre -> celui de config/subtitles.json, seule source
        # du style par defaut.
        self._select(
            self.subtitle_combo,
            settings_store.get("default_subtitle_style") or subtitles_config.get("default_style"),
        )
        self.subtitle_combo.currentIndexChanged.connect(
            lambda: settings_store.save({"default_subtitle_style": self.subtitle_combo.currentData()})
        )
        grid.addWidget(self.subtitle_combo, row, 1)
        row += 1

        grid.addWidget(QLabel("Dossier des projets"), row, 0)
        folder_row = QHBoxLayout()
        self.folder_label = QLabel(str(settings_store.projects_dir()))
        self.folder_label.setProperty("role", "mono")
        self.folder_label.setWordWrap(True)
        folder_row.addWidget(self.folder_label, stretch=1)
        browse_btn = QPushButton("Parcourir…")
        browse_btn.clicked.connect(self._browse_projects_dir)
        folder_row.addWidget(browse_btn)
        grid.addLayout(folder_row, row, 1)
        row += 1

        grid.addWidget(QLabel("Dossier des clips téléchargés"), row, 0)
        clips_row = QHBoxLayout()
        self.clips_label = QLabel(str(settings_store.clips_dir()))
        self.clips_label.setProperty("role", "mono")
        self.clips_label.setWordWrap(True)
        clips_row.addWidget(self.clips_label, stretch=1)
        clips_btn = QPushButton("Parcourir…")
        clips_btn.clicked.connect(self._browse_clips_dir)
        clips_row.addWidget(clips_btn)
        grid.addLayout(clips_row, row, 1)

        card.layout().addLayout(grid)

    def _browse_projects_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier des projets", str(settings_store.projects_dir()))
        if folder:
            settings_store.save({"projects_dir": folder})
            self.folder_label.setText(folder)

    def _browse_clips_dir(self) -> None:
        """Les clips deja telecharges ne sont PAS deplaces.

        Les recopier a l'insu de l'utilisateur pourrait remplir le disque
        choisi sans prevenir ; les nouveaux clips iront au nouvel endroit, les
        anciens restent la ou ils sont et peuvent etre supprimes ou deplaces a
        la main.
        """
        folder = QFileDialog.getExistingDirectory(
            self, "Dossier des clips téléchargés", str(settings_store.clips_dir()))
        if folder:
            settings_store.save({"clips_dir": folder})
            self.clips_label.setText(folder)

    # ---------- Video ----------

    def _build_video_card(self) -> None:
        card = self._card()
        card.layout().addWidget(_section_title("EXPORT VIDÉO"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Qualité"))
        self.quality_combo = QComboBox()
        for key, label, _crf, _preset in _QUALITY_CHOICES:
            self.quality_combo.addItem(label, key)

        try:
            current_crf = _load_json(_settings_json_path())["export"]["video_bitrate_crf"]
        except (KeyError, FileNotFoundError):
            current_crf = 20
        current_key = "high" if current_crf <= 18 else "standard"
        self._select(self.quality_combo, current_key)
        self.quality_combo.currentIndexChanged.connect(self._save_quality)
        row.addWidget(self.quality_combo)
        row.addStretch(1)
        card.layout().addLayout(row)

        hint = QLabel("« Haute qualité » produit des fichiers plus lourds et un encodage plus lent.")
        hint.setProperty("role", "muted")
        card.layout().addWidget(hint)

    def _save_quality(self) -> None:
        key = self.quality_combo.currentData()
        _, _, crf, preset = next(c for c in _QUALITY_CHOICES if c[0] == key)
        data = _load_json(_settings_json_path())
        data.setdefault("export", {})["video_bitrate_crf"] = crf
        data["export"]["video_preset"] = preset
        _save_json(_settings_json_path(), data)

    # ---------- Synthese vocale (Voice Studio) ----------

    def _build_tts_card(self) -> None:
        """Reglages de voix par defaut + acces au gestionnaire de voix.

        Les valeurs sont celles de gui/settings_store.py, les memes que la page
        Voice Studio ecrit quand on genere une voix : un seul endroit ou le
        defaut est range, deux endroits ou on peut le changer.
        """
        from voice_studio import piper_models, tts

        card = self._card()
        card.layout().addWidget(_section_title("🎙️ SYNTHÈSE VOCALE"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Moteur par défaut"))
        self.tts_engine_combo = QComboBox()
        self.tts_engine_combo.addItem("Automatique", "")
        for engine in tts.all_engines():
            label = tts.ENGINE_LABELS.get(engine.name, engine.name)
            if not engine.available():
                label += " (non disponible)"
            self.tts_engine_combo.addItem(label, engine.name)
            index = self.tts_engine_combo.count() - 1
            self.tts_engine_combo.model().item(index).setEnabled(engine.available())
        self._select(self.tts_engine_combo, settings_store.get("tts_engine") or "")
        self.tts_engine_combo.currentIndexChanged.connect(self._save_tts_engine)
        row.addWidget(self.tts_engine_combo)

        row.addWidget(QLabel("Vitesse"))
        self.tts_rate_combo = QComboBox()
        for value in (0.75, 0.85, 1.00, 1.10, 1.25, 1.50):
            self.tts_rate_combo.addItem(f"{value:.2f}x", value)
        self._select(self.tts_rate_combo, float(settings_store.get("tts_rate") or 1.0))
        self.tts_rate_combo.currentIndexChanged.connect(self._save_tts_rate)
        row.addWidget(self.tts_rate_combo)
        row.addStretch(1)
        card.layout().addLayout(row)

        folder = QHBoxLayout()
        folder.addWidget(QLabel("Dossier des voix Piper"))
        self.piper_dir_label = QLabel(str(piper_models.models_dir()))
        self.piper_dir_label.setProperty("role", "muted")
        self.piper_dir_label.setWordWrap(True)
        folder.addWidget(self.piper_dir_label, stretch=1)
        change = QPushButton("Changer...")
        change.clicked.connect(self._choose_piper_dir)
        folder.addWidget(change)
        card.layout().addLayout(folder)

        actions = QHBoxLayout()
        self.tts_usage_label = QLabel("")
        self.tts_usage_label.setProperty("role", "muted")
        actions.addWidget(self.tts_usage_label, stretch=1)
        manage = QPushButton("Gérer les voix")
        manage.clicked.connect(self._manage_voices)
        actions.addWidget(manage)
        card.layout().addLayout(actions)

        self._refresh_tts_usage()

    def _refresh_tts_usage(self) -> None:
        from voice_studio import piper_models

        count = len(piper_models.installed_keys())
        size = piper_models.installed_size_bytes() / 1_000_000
        self.tts_usage_label.setText(
            f"{count} voix Piper installée{'s' if count > 1 else ''}  •  {size:.0f} Mo utilisés")

    def _save_tts_engine(self) -> None:
        settings_store.save({"tts_engine": self.tts_engine_combo.currentData() or None})

    def _save_tts_rate(self) -> None:
        settings_store.save({"tts_rate": float(self.tts_rate_combo.currentData() or 1.0)})

    def _choose_piper_dir(self) -> None:
        from voice_studio import piper_models

        folder = QFileDialog.getExistingDirectory(
            self, "Dossier des voix Piper", str(piper_models.models_dir()))
        if folder:
            settings_store.save({"piper_models_dir": folder})
            self.piper_dir_label.setText(folder)
            self._refresh_tts_usage()

    def _manage_voices(self) -> None:
        from gui.voice_studio.voices_dialog import VoicesDialog

        dialog = VoicesDialog(self)
        dialog.exec()
        dialog.cleanup()
        self._refresh_tts_usage()

    # ---------- Ponderation du scoring ----------

    def _build_weights_card(self) -> None:
        card = self._card()
        card.layout().addWidget(_section_title("PONDÉRATION DU SCORE DE HOOK"))
        note = QLabel("La somme des poids doit valoir 1.00.")
        note.setProperty("role", "muted")
        card.layout().addWidget(note)

        data = _load_json(_settings_json_path())
        weights = data.get("weights", {})

        grid = QGridLayout()
        self.weight_spins: dict[str, QDoubleSpinBox] = {}
        for row, (key, label) in enumerate(_WEIGHT_KEYS):
            grid.addWidget(QLabel(label), row, 0)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setValue(weights.get(key, 0.0))
            spin.valueChanged.connect(self._update_weight_sum)
            grid.addWidget(spin, row, 1)
            self.weight_spins[key] = spin
        card.layout().addLayout(grid)

        self.sum_label = QLabel("")
        card.layout().addWidget(self.sum_label)
        self._update_weight_sum()

        save_btn = QPushButton("Enregistrer la pondération")
        save_btn.setProperty("variant", "primary")
        save_btn.clicked.connect(self._save_weights)
        card.layout().addWidget(save_btn)

    def _update_weight_sum(self) -> None:
        total = sum(spin.value() for spin in self.weight_spins.values())
        ok = abs(total - 1.0) < 0.01
        color = "#3FAF77" if ok else "#E1554A"
        self.sum_label.setText(f"Somme actuelle : {total:.2f}")
        self.sum_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _save_weights(self) -> None:
        total = sum(spin.value() for spin in self.weight_spins.values())
        if abs(total - 1.0) > 0.01:
            QMessageBox.warning(self, "Pondération invalide", f"La somme doit valoir 1.00 (actuellement {total:.2f}).")
            return
        data = _load_json(_settings_json_path())
        data["weights"] = {key: spin.value() for key, spin in self.weight_spins.items()}
        _save_json(_settings_json_path(), data)
        QMessageBox.information(self, "Enregistré", "La pondération du score a été mise à jour.")

    # ---------- Mots-cles ----------

    def _build_keywords_card(self) -> None:
        card = self._card()
        card.layout().addWidget(_section_title("MOTS-CLÉS ACCROCHEURS"))

        self.keywords_data = _load_json(_keywords_json_path())
        self.keywords_list = QListWidget()
        self.keywords_list.addItems(self.keywords_data.get("strong_keywords", []))
        self.keywords_list.setMaximumHeight(180)
        card.layout().addWidget(self.keywords_list)

        buttons = QHBoxLayout()
        add_btn = QPushButton("+ Ajouter")
        add_btn.clicked.connect(self._add_keyword)
        buttons.addWidget(add_btn)
        remove_btn = QPushButton("Retirer la sélection")
        remove_btn.clicked.connect(self._remove_keyword)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        card.layout().addLayout(buttons)

    def _add_keyword(self) -> None:
        text, ok = QInputDialog.getText(self, "Ajouter un mot-clé", "Mot ou expression :")
        text = text.strip()
        if ok and text:
            self.keywords_list.addItem(text)
            self._save_keywords()

    def _remove_keyword(self) -> None:
        for item in self.keywords_list.selectedItems():
            self.keywords_list.takeItem(self.keywords_list.row(item))
        self._save_keywords()

    def _save_keywords(self) -> None:
        self.keywords_data["strong_keywords"] = [
            self.keywords_list.item(i).text() for i in range(self.keywords_list.count())
        ]
        _save_json(_keywords_json_path(), self.keywords_data)

    # ---------- Utilitaires ----------

    def _card(self) -> QFrame:
        card = QFrame()
        card.setProperty("role", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self.col.addWidget(card)
        return card

    @staticmethod
    def _select(combo: QComboBox, data_value) -> None:
        index = combo.findData(data_value)
        if index >= 0:
            combo.setCurrentIndex(index)
