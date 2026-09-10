"""Page 🎙️ Voice Studio.

Cinq blocs, dans l'ordre du travail : la source, la transcription (le coeur de
l'ecran), la recherche, la generation de voix, l'export. Aucun bouton ne fait
semblant : une action impossible sur cette machine est desactivee et dit
pourquoi -- pas de voix installee, pas de transcription encore chargee, pas de
lecture synchronisee sans le media en local.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gui import settings_store
from gui.voice_studio.rewrite_panel import RewritePanel
from gui.voice_studio.transcript_view import TranscriptView
from gui.voice_studio.video_panel import VideoPanel
from gui.voice_studio.voices_dialog import VoicesDialog
from gui.voice_studio.workers import AnalysisWorker, VoiceWorker
from voice_studio import exporters, piper_models, services, store, tts
from voice_studio.models import SOURCE_LABELS, TtsSettings
from voice_studio.transcript import VIEW_CLEAN, VIEW_RAW, coverage, format_timestamp
from voice_studio.transcription import RIGHTS_NOTICE

LANGUAGES = [("Détection automatique", None), ("Français", "fr"), ("Anglais", "en"),
             ("Espagnol", "es"), ("Allemand", "de"), ("Italien", "it")]

MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Paliers de vitesse, plutot qu'un curseur libre : ce sont les valeurs qui ont
# un sens a l'oreille, et elles restent comparables d'une generation a l'autre.
RATES = [0.75, 0.85, 1.00, 1.10, 1.25, 1.50]
DEFAULT_RATE = 1.00


def _card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    if title:
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(heading)
    return frame, layout


class VoiceStudioPage(QWidget):
    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller
        self.project = None
        self._worker = None
        self._voice_worker = None
        self._cancel_token = None
        self._last_audio = ""
        self._voices = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 28, 40, 20)
        outer.setSpacing(10)

        title = QLabel("🎙️  VOICE STUDIO")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)

        subtitle = QLabel("Transcris intégralement une vidéo YouTube "
                          "et transforme ton texte en voix.")
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 8, 0)
        self.body.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self.body.addWidget(self._build_source_card())
        self.body.addWidget(self._build_transcript_card(), stretch=1)
        self.body.addWidget(self._build_rewrite_card())
        self.body.addWidget(self._build_voice_card())
        self.body.addWidget(self._build_video_card())
        self.body.addStretch(0)

        self._refresh_state()

    # ------------------------------------------------------------- source
    def _build_source_card(self) -> QFrame:
        frame, layout = _card("🎬  SOURCE YOUTUBE")

        row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://youtu.be/...")
        self.url_input.returnPressed.connect(self._analyze)
        row.addWidget(self.url_input, stretch=1)

        paste_btn = QPushButton("Coller")
        paste_btn.clicked.connect(self._paste)
        row.addWidget(paste_btn)

        self.analyze_btn = QPushButton("🔍  Analyser la vidéo")
        self.analyze_btn.setProperty("variant", "primary")
        self.analyze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analyze_btn.clicked.connect(self._analyze)
        row.addWidget(self.analyze_btn)

        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)

        options = QHBoxLayout()
        options.addWidget(QLabel("Langue"))
        self.language_combo = QComboBox()
        for label, code in LANGUAGES:
            self.language_combo.addItem(label, code)
        options.addWidget(self.language_combo)

        options.addWidget(QLabel("Modèle"))
        self.model_combo = QComboBox()
        for name in MODELS:
            self.model_combo.addItem(name, name)
        configured = settings_store.get("default_model") or "small"
        if configured in MODELS:
            self.model_combo.setCurrentIndex(MODELS.index(configured))
        self.model_combo.currentIndexChanged.connect(lambda _i: self._sync_video_options())
        options.addWidget(self.model_combo)
        options.addStretch(1)
        layout.addLayout(options)

        self.audio_check = QCheckBox(
            "Autoriser la récupération de la piste audio si aucun sous-titre n'est publié")
        self.audio_check.setToolTip(RIGHTS_NOTICE)
        layout.addWidget(self.audio_check)

        self.force_check = QCheckBox(
            "Transcrire avec Faster-Whisper même si des sous-titres existent")
        layout.addWidget(self.force_check)

        rights = QLabel("Utilise uniquement des contenus auxquels tu as accès et que tu es "
                        "autorisé à traiter ou réutiliser.")
        rights.setProperty("role", "muted")
        rights.setWordWrap(True)
        layout.addWidget(rights)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.video_label = QLabel("")
        self.video_label.setWordWrap(True)
        self.video_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.video_label)
        return frame

    # -------------------------------------------------------- transcription
    def _build_transcript_card(self) -> QFrame:
        frame, layout = _card("📝  TRANSCRIPTION")

        views = QHBoxLayout()
        self.raw_btn = QPushButton("Transcription brute")
        self.raw_btn.setCheckable(True)
        self.raw_btn.setChecked(True)
        self.raw_btn.clicked.connect(lambda: self._set_view(VIEW_RAW))
        views.addWidget(self.raw_btn)

        self.clean_btn = QPushButton("Version nettoyée")
        self.clean_btn.setCheckable(True)
        self.clean_btn.clicked.connect(lambda: self._set_view(VIEW_CLEAN))
        views.addWidget(self.clean_btn)
        views.addStretch(1)

        self.source_label = QLabel("")
        self.source_label.setProperty("role", "muted")
        views.addWidget(self.source_label)
        layout.addLayout(views)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔎  Rechercher dans la transcription...")
        self.search_input.textChanged.connect(self._search)
        self.search_input.returnPressed.connect(self._next_match)
        search_row.addWidget(self.search_input, stretch=1)

        self.prev_btn = QPushButton("↑")
        self.prev_btn.clicked.connect(self._previous_match)
        search_row.addWidget(self.prev_btn)
        self.next_btn = QPushButton("↓")
        self.next_btn.clicked.connect(self._next_match)
        search_row.addWidget(self.next_btn)

        self.matches_label = QLabel("")
        self.matches_label.setProperty("role", "muted")
        search_row.addWidget(self.matches_label)
        layout.addLayout(search_row)

        self.transcript_view = TranscriptView()
        self.transcript_view.setMinimumHeight(280)
        layout.addWidget(self.transcript_view, stretch=1)

        self.coverage_label = QLabel("")
        self.coverage_label.setProperty("role", "muted")
        self.coverage_label.setWordWrap(True)
        layout.addWidget(self.coverage_label)

        actions = QHBoxLayout()
        self.copy_all_btn = QPushButton("Copier tout")
        self.copy_all_btn.clicked.connect(lambda: self._copy(self.transcript_view.plain_text(True)))
        actions.addWidget(self.copy_all_btn)

        self.copy_plain_btn = QPushButton("Copier sans minutages")
        self.copy_plain_btn.clicked.connect(
            lambda: self._copy(self.transcript_view.plain_text(False)))
        actions.addWidget(self.copy_plain_btn)

        self.copy_selection_btn = QPushButton("Copier le passage")
        self.copy_selection_btn.clicked.connect(
            lambda: self._copy(self.transcript_view.selected_text()))
        actions.addWidget(self.copy_selection_btn)
        actions.addStretch(1)

        for label, fmt in (("📤 TXT", "txt"), ("📤 SRT", "srt"), ("📤 VTT", "vtt")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, f=fmt: self._export(f))
            actions.addWidget(button)
            setattr(self, f"export_{fmt}_btn", button)
        layout.addLayout(actions)
        return frame

    # ---------------------------------------------------------- reecriture
    def _build_rewrite_card(self) -> QFrame:
        """Bloc de reecriture. Masque tant qu'aucun transcript n'existe : il
        n'aurait rien a analyser ni a reecrire."""
        frame, layout = _card()
        self.rewrite_panel = RewritePanel()
        self.rewrite_panel.setVisible(False)
        # Le texte choisi part vers le bloc de voix EXISTANT : il n'y a pas de
        # second systeme de synthese, seulement un champ qui se remplit.
        self.rewrite_panel.send_to_voice.connect(self._use_script)
        layout.addWidget(self.rewrite_panel)
        self.rewrite_card = frame
        frame.setVisible(False)
        return frame

    def _use_script(self, text: str) -> None:
        if not text.strip():
            return
        self.voice_text.setPlainText(text.strip())
        self.voice_status.setText("Script repris : choisis une voix puis génère.")
        self._refresh_state()

    # --------------------------------------------------------------- voix
    def _build_voice_card(self) -> QFrame:
        frame, layout = _card("🎤  GÉNÉRER UNE VOIX")

        hint = QLabel("Sélectionne un passage de la transcription puis clique sur "
                      "« Reprendre la sélection », ou écris ton propre texte.")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.voice_text = QTextEdit()
        self.voice_text.setPlaceholderText("Texte à lire à voix haute...")
        self.voice_text.setMaximumHeight(110)
        layout.addWidget(self.voice_text)

        take = QHBoxLayout()
        self.take_selection_btn = QPushButton("Reprendre la sélection")
        self.take_selection_btn.clicked.connect(self._take_selection)
        take.addWidget(self.take_selection_btn)
        take.addStretch(1)
        layout.addLayout(take)

        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Moteur"))
        self.engine_combo = QComboBox()
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self.engine_combo)

        engine_row.addWidget(QLabel("Voix"))
        self.voice_combo = QComboBox()
        engine_row.addWidget(self.voice_combo, stretch=1)

        self.manage_btn = QPushButton("Gérer les voix")
        self.manage_btn.clicked.connect(self._manage_voices)
        engine_row.addWidget(self.manage_btn)
        layout.addLayout(engine_row)

        settings = QHBoxLayout()
        settings.addWidget(QLabel("Vitesse"))
        self.rate_combo = QComboBox()
        for value in RATES:
            self.rate_combo.addItem(f"{value:.2f}x", value)
        self.rate_combo.setCurrentIndex(RATES.index(DEFAULT_RATE))
        settings.addWidget(self.rate_combo)

        settings.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._update_voice_labels)
        settings.addWidget(self.volume_slider)
        self.volume_label = QLabel("100 %")
        settings.addWidget(self.volume_label)

        settings.addWidget(QLabel("Pause"))
        self.pause_slider = QSlider(Qt.Orientation.Horizontal)
        self.pause_slider.setRange(0, 20)
        self.pause_slider.setValue(0)
        self.pause_slider.setFixedWidth(90)
        self.pause_slider.valueChanged.connect(self._update_voice_labels)
        settings.addWidget(self.pause_slider)
        self.pause_label = QLabel("0.0 s")
        settings.addWidget(self.pause_label)
        settings.addStretch(1)
        layout.addLayout(settings)

        row = QHBoxLayout()
        self.preview_btn = QPushButton("▶  Écouter un aperçu")
        self.preview_btn.clicked.connect(self._preview_voice)
        row.addWidget(self.preview_btn)

        self.generate_btn = QPushButton("▶  Générer la voix")
        self.generate_btn.setProperty("variant", "primary")
        self.generate_btn.clicked.connect(self._generate_voice)
        row.addWidget(self.generate_btn)

        self.play_btn = QPushButton("🔊  Écouter")
        self.play_btn.clicked.connect(self._play_audio)
        row.addWidget(self.play_btn)

        self.export_wav_btn = QPushButton("Exporter WAV")
        self.export_wav_btn.clicked.connect(lambda: self._export_audio("wav"))
        row.addWidget(self.export_wav_btn)

        self.export_mp3_btn = QPushButton("Exporter MP3")
        self.export_mp3_btn.clicked.connect(lambda: self._export_audio("mp3"))
        row.addWidget(self.export_mp3_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.voice_status = QLabel("")
        self.voice_status.setProperty("role", "muted")
        self.voice_status.setWordWrap(True)
        self.voice_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.voice_status)
        return frame

    # -------------------------------------------------------------- video
    def _build_video_card(self) -> QFrame:
        """Bloc de creation video.

        Il lit la voix, la vitesse, le volume et la pause du bloc precedent au
        lieu d'en proposer une deuxieme serie : deux jeux de reglages sur le
        meme ecran finiraient par ne plus dire la meme chose.
        """
        frame, layout = _card()
        self.video_panel = VideoPanel(
            voice_provider=lambda: (self._current_voice(), self._rate(),
                                    self.volume_slider.value() / 100.0,
                                    self.pause_slider.value() / 10.0),
            script_provider=lambda: self.voice_text.toPlainText(),
        )
        self.video_panel.status_changed.connect(self._on_video_created)
        layout.addWidget(self.video_panel)
        self.video_card = frame
        return frame

    def open_source_video(self, payload: dict) -> None:
        """Appelee quand une video telechargee depuis Recherche arrive ici.

        Le projet Voice Studio de cette video est charge s'il existe deja (sa
        transcription et son script de narration reviennent avec lui), sinon il
        est cree : la video reste rattachee a son identifiant YouTube, pas a un
        chemin de fichier qui peut changer.
        """
        payload = payload or {}
        path = payload.get("path") or ""
        video_id = payload.get("video_id") or ""
        title = payload.get("title") or ""

        key = video_id or store.key_for_video_path(path)
        project = store.load(key)
        if project is None:
            from voice_studio.models import VoiceStudioProject

            project = VoiceStudioProject(youtube_video_id=key)
        project.youtube_url = payload.get("url") or project.youtube_url
        project.title = title or project.title
        project.duration_s = payload.get("duration_s") or project.duration_s
        project.source_video_path = path
        project.source_video_title = title
        store.save(project)

        if project.has_transcript:
            self._show_project(project, notes=[])
        else:
            self.project = project
            self.video_label.setText(title or path)

        self.video_panel.set_source(path, title, payload.get("duration_s"))
        if project.narration_script and not self.video_panel.script():
            self.video_panel.script_edit.setPlainText(project.narration_script)
        self._sync_video_options()
        self.status_label.setText(
            "Vidéo prête dans le bloc « Créer une vidéo » ci-dessous.")
        self._refresh_state()

    def _sync_video_options(self) -> None:
        """Le modele et le peripherique choisis en haut de la page servent
        aussi a analyser la voix generee : un seul choix, pas deux.

        La LANGUE n'est pas transmise : celle-ci est celle de la video a
        transcrire, pas celle de la narration (voir
        video_panel.set_transcription_options).
        """
        if getattr(self, "video_panel", None) is None:
            return
        self.video_panel.set_transcription_options(
            self.model_combo.currentData() or "small",
            settings_store.get("default_device") or "auto",
        )

    def _on_video_created(self, message: str) -> None:
        """Enregistre ce qui a servi au rendu : script, reglages, fichier."""
        if self.project is None:
            return
        self.project.narration_script = self.video_panel.script()
        self.project.video_settings = self.video_panel.settings()
        self.project.source_video_path = self.video_panel.source_path()
        path = message.split(" : ", 1)[-1].strip()
        if path and not message.startswith("Aperçu") and path not in self.project.video_exports:
            # Un apercu n'est pas un export : il vit dans le dossier de travail
            # et sera ecrase au prochain essai.
            self.project.video_exports.append(path)
        store.save(self.project)

    # ------------------------------------------------------------- etats
    def on_shown(self) -> None:
        if not self._voices:
            self._load_voices()
        self._sync_video_options()

    def _load_voices(self) -> None:
        """Recharge moteurs et voix. Aucun moteur indisponible n'est propose."""
        previous = self.engine_combo.currentData()
        self.engine_combo.blockSignals(True)
        self.engine_combo.clear()
        for engine in tts.all_engines():
            if engine.available():
                self.engine_combo.addItem(tts.ENGINE_LABELS.get(engine.name, engine.name),
                                          engine.name)
        if previous is not None:
            index = self.engine_combo.findData(previous)
            if index >= 0:
                self.engine_combo.setCurrentIndex(index)
        elif self.engine_combo.count():
            preferred = settings_store.get("tts_engine")
            index = self.engine_combo.findData(preferred) if preferred else -1
            self.engine_combo.setCurrentIndex(index if index >= 0 else 0)
        self.engine_combo.blockSignals(False)
        self._load_voices_for_engine()

    def _load_voices_for_engine(self) -> None:
        engine_name = self.engine_combo.currentData() or ""
        self._voices = tts.available_voices(engine_name)
        self.voice_combo.clear()
        for voice in self._voices:
            self.voice_combo.addItem(voice.display_label, voice.id)
        preferred = settings_store.get("tts_voice")
        if preferred:
            index = self.voice_combo.findData(preferred)
            if index >= 0:
                self.voice_combo.setCurrentIndex(index)

        piper = tts.PiperEngine()
        if not self._voices and not tts.available_voices():
            self.voice_status.setText(
                "Aucune voix n'est installée sur cet ordinateur : la génération est "
                "indisponible. Sous Windows, les voix système s'ajoutent dans "
                "Paramètres > Heure et langue > Voix.")
        elif not piper.available():
            installed = len(piper_models.installed_keys())
            if piper.runtime() and installed == 0:
                self.voice_status.setText(
                    "Aucune voix locale Piper installée. Clique sur « Gérer les voix » "
                    "pour en télécharger une : les voix Piper sont plus naturelles que "
                    "les voix de Windows et fonctionnent ensuite hors ligne.")
            elif not piper.runtime():
                self.voice_status.setText(
                    "Piper n'est pas encore configuré : seules les voix du système sont "
                    "disponibles. Ouvre « Gérer les voix » pour voir ce qu'il manque.")
        self._refresh_state()

    def _on_engine_changed(self) -> None:
        self._load_voices_for_engine()

    def _manage_voices(self) -> None:
        dialog = VoicesDialog(self)
        dialog.exec()
        dialog.cleanup()
        # Une voix telechargee doit apparaitre tout de suite dans la liste.
        self._load_voices()

    def _refresh_state(self) -> None:
        has_transcript = bool(self.project and self.project.has_transcript)
        for widget in (self.copy_all_btn, self.copy_plain_btn, self.copy_selection_btn,
                       self.export_txt_btn, self.export_srt_btn, self.export_vtt_btn,
                       self.search_input, self.prev_btn, self.next_btn,
                       self.take_selection_btn, self.raw_btn, self.clean_btn):
            widget.setEnabled(has_transcript)

        has_voice = bool(self._voices)
        has_text = bool(self.voice_text.toPlainText().strip())
        self.generate_btn.setEnabled(has_voice)
        self.preview_btn.setEnabled(has_voice)
        self.voice_combo.setEnabled(has_voice)
        self.engine_combo.setEnabled(self.engine_combo.count() > 1)
        ready = bool(self._last_audio and Path(self._last_audio).is_file())
        self.play_btn.setEnabled(ready)
        self.export_wav_btn.setEnabled(ready)
        self.export_mp3_btn.setEnabled(ready)
        self._update_voice_labels()
        return has_text

    def _update_voice_labels(self) -> None:
        self.volume_label.setText(f"{self.volume_slider.value()} %")
        self.pause_label.setText(f"{self.pause_slider.value() / 10:.1f} s")

    # ---------------------------------------------------------- analyse
    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text().strip()
        if text:
            self.url_input.setText(text)

    def _analyze(self) -> None:
        if self._worker is not None:
            return
        url = self.url_input.text().strip()
        existing = services.existing_project(url)
        if existing is not None and not self.force_check.isChecked():
            answer = QMessageBox.question(
                self, "Transcription déjà disponible",
                f"Une transcription existe déjà pour « {existing.title or existing.youtube_video_id} ».\n\n"
                "Ouvrir celle-ci, ou refaire l'analyse ?",
                QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Retry,
                QMessageBox.StandardButton.Open,
            )
            if answer == QMessageBox.StandardButton.Open:
                self._show_project(existing, notes=[])
                return

        request = services.AnalysisRequest(
            url=url,
            model=self.model_combo.currentData(),
            language=self.language_combo.currentData(),
            device=settings_store.get("default_device") or "auto",
            allow_audio_download=self.audio_check.isChecked(),
            force_whisper=self.force_check.isChecked(),
        )
        self._cancel_token = CancelToken()
        self._worker = AnalysisWorker(request, self._cancel_token)
        self._worker.step.connect(self._on_step)
        self._worker.detail.connect(self._on_detail)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_thread_finished)

        self.analyze_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("Analyse en cours...")
        self._worker.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Annulation en cours...")

    def _on_step(self, index: int, label: str) -> None:
        self.status_label.setText(f"{index}/{len(services.STEPS)}  {label}")

    def _on_detail(self, fraction, label: str) -> None:
        if fraction is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.status_label.setText(label)

    def _on_done(self, report) -> None:
        self._show_project(report.project, notes=report.notes)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("")
        QMessageBox.warning(self, "Analyse impossible", message)

    def _on_cancelled(self) -> None:
        self.status_label.setText("Analyse annulée. Rien n'a été enregistré.")

    def _on_thread_finished(self) -> None:
        self._worker = None
        self._cancel_token = None
        self.analyze_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._refresh_state()

    def _show_project(self, project, notes) -> None:
        self.project = project
        self.transcript_view.set_transcript(project.transcript, view=self.transcript_view.view)
        pieces = [project.title or project.youtube_video_id]
        if project.channel:
            pieces.append(project.channel)
        if project.duration_s:
            pieces.append(format_timestamp(project.duration_s))
        self.video_label.setText("  •  ".join(p for p in pieces if p))
        self.source_label.setText(SOURCE_LABELS.get(project.transcription_source, ""))

        self.rewrite_panel.set_transcript(
            self.transcript_view.plain_text(with_timestamps=False))
        self.rewrite_card.setVisible(self.rewrite_panel.isVisible())

        covered = coverage(project.transcript, project.duration_s or 0.0)
        segments = len(project.transcript.segments) if project.transcript else 0
        text = (f"{segments} segments  •  parole transcrite : "
                f"{format_timestamp(covered.spoken_s)}  •  dernier mot à "
                f"{format_timestamp(covered.last_end_s)}")
        if project.duration_s:
            text += f"  •  durée de la vidéo : {format_timestamp(project.duration_s)}"
        if notes:
            text += "\n⚠️ " + "\n⚠️ ".join(notes)
        self.coverage_label.setText(text)
        self.status_label.setText("")
        self._refresh_state()

    # -------------------------------------------------------- transcription
    def _set_view(self, view: str) -> None:
        self.raw_btn.setChecked(view == VIEW_RAW)
        self.clean_btn.setChecked(view == VIEW_CLEAN)
        self.transcript_view.set_view(view)
        if view == VIEW_CLEAN:
            self.matches_label.setText("recherche disponible sur la version brute")
        else:
            self._search(self.search_input.text())

    def _search(self, query: str) -> None:
        count = self.transcript_view.find_all(query)
        if not query.strip():
            self.matches_label.setText("")
        elif count == 0:
            self.matches_label.setText("aucune occurrence")
        else:
            self.matches_label.setText(f"{count} occurrence" + ("s" if count > 1 else ""))

    def _next_match(self) -> None:
        self.transcript_view.next_match()
        self._update_match_label()

    def _previous_match(self) -> None:
        self.transcript_view.previous_match()
        self._update_match_label()

    def _update_match_label(self) -> None:
        total = self.transcript_view.match_count
        if total:
            self.matches_label.setText(
                f"{self.transcript_view.current_match_index + 1} / {total}")

    def _copy(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication

        if not text.strip():
            self.status_label.setText("Rien à copier : sélectionne d'abord un passage.")
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("Copié dans le presse-papiers.")

    def _export(self, fmt: str) -> None:
        if not (self.project and self.project.has_transcript):
            return
        suggested = exporters.suggested_filename(self.project, fmt)
        path, _ = QFileDialog.getSaveFileName(self, f"Exporter en {fmt.upper()}",
                                              str(Path.home() / suggested),
                                              f"Fichier {fmt.upper()} (*.{fmt})")
        if not path:
            return
        try:
            written = exporters.write(self.project.transcript, path, fmt=fmt,
                                      view=self.transcript_view.view)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Export impossible", str(error))
            return
        self.status_label.setText(f"Exporté : {written}")

    # --------------------------------------------------------------- voix
    def _take_selection(self) -> None:
        selection = self.transcript_view.selected_text()
        if not selection:
            self.voice_status.setText("Sélectionne d'abord un passage dans la transcription.")
            return
        self.voice_text.setPlainText(selection)
        self._refresh_state()

    def _rate(self) -> float:
        value = self.rate_combo.currentData()
        return float(value) if value else DEFAULT_RATE

    def _preview_voice(self) -> None:
        """Apercu : un EXTRAIT du texte, avec le moteur, la voix et la vitesse
        reellement choisis. Generer plusieurs minutes de parole pour verifier
        une voix serait une perte de temps."""
        if self._voice_worker is not None:
            return
        source = self.voice_text.toPlainText().strip() or self.transcript_view.selected_text()
        if not source:
            source = "Bonjour, voici un aperçu de la voix sélectionnée."
        extract = tts.preview_text(source)
        self._start_voice_worker(extract, str(store.audio_dir() / "apercu.wav"),
                                 "Génération de l'aperçu...")

    def _start_voice_worker(self, text: str, out_path: str, message: str) -> None:
        self._voice_worker = VoiceWorker(
            text, out_path, self._current_voice(),
            rate=self._rate(),
            volume=self.volume_slider.value() / 100.0,
            sentence_pause_s=self.pause_slider.value() / 10.0,
        )
        self._voice_worker.done.connect(self._on_voice_done)
        self._voice_worker.failed.connect(self._on_voice_failed)
        self._voice_worker.finished.connect(self._on_voice_finished)
        self.generate_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.voice_status.setText(message)
        self._voice_worker.start()

    def _current_voice(self):
        index = self.voice_combo.currentIndex()
        return self._voices[index] if 0 <= index < len(self._voices) else None

    def _generate_voice(self) -> None:
        if self._voice_worker is not None:
            return
        text = self.voice_text.toPlainText().strip()
        if not text:
            self.voice_status.setText("Écris ou reprends un texte avant de générer la voix.")
            return
        self._start_voice_worker(text, str(store.audio_dir() / "voix.wav"),
                                 "Génération de la voix...")

    def _on_voice_done(self, path: str) -> None:
        self._last_audio = path
        self.voice_status.setText(f"Voix générée : {path}")
        if self.project is not None:
            settings = TtsSettings(
                voice_id=self._current_voice().id if self._current_voice() else "",
                voice_label=self._current_voice().label if self._current_voice() else "",
                rate=self._rate(),
                volume=self.volume_slider.value() / 100.0,
                sentence_pause_s=self.pause_slider.value() / 10.0,
            )
            self.project.tts_settings = settings
            # Le choix devient le defaut : rouvrir Voice Studio retrouve le
            # moteur, la voix et la vitesse utilises la derniere fois.
            settings_store.save({
                **settings_store.load(),
                "tts_engine": self.engine_combo.currentData() or "",
                "tts_voice": settings.voice_id,
                "tts_rate": settings.rate,
            })
            if path not in self.project.generated_audio:
                self.project.generated_audio.append(path)
            store.save(self.project)

    def _on_voice_failed(self, message: str) -> None:
        self.voice_status.setText("")
        QMessageBox.warning(self, "Génération impossible", message)

    def _on_voice_finished(self) -> None:
        self._voice_worker = None
        self.generate_btn.setEnabled(bool(self._voices))
        self.preview_btn.setEnabled(bool(self._voices))
        self._refresh_state()

    def _play_audio(self) -> None:
        if not (self._last_audio and Path(self._last_audio).is_file()):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_audio))

    def _export_audio(self, fmt: str) -> None:
        if not (self._last_audio and Path(self._last_audio).is_file()):
            return
        base = exporters.suggested_filename(self.project, fmt) if self.project else f"voix.{fmt}"
        path, _ = QFileDialog.getSaveFileName(self, f"Exporter en {fmt.upper()}",
                                              str(Path.home() / base),
                                              f"Fichier {fmt.upper()} (*.{fmt})")
        if not path:
            return
        try:
            if fmt == "mp3":
                tts.to_mp3(self._last_audio, path)
            else:
                import shutil

                shutil.copyfile(self._last_audio, path)
        except (tts.TtsError, OSError) as error:
            QMessageBox.warning(self, "Export impossible", str(error))
            return
        self.voice_status.setText(f"Exporté : {path}")

    # ------------------------------------------------------------ fermeture
    def cleanup(self) -> None:
        """Arrete proprement les fils avant la destruction de la fenetre."""
        if getattr(self, "rewrite_panel", None) is not None:
            self.rewrite_panel.cleanup()
        if getattr(self, "video_panel", None) is not None:
            self.video_panel.cleanup()
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        for worker in (self._worker, self._voice_worker):
            if worker is not None and worker.isRunning():
                worker.wait(4000)
