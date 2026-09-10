"""Bloc « Creer une video » de Voice Studio.

Une video source (telechargee depuis Recherche ou ouverte a la main), un script
de narration, et les quatre decisions que le rendu demande : le format, le son,
la duree, les sous-titres. Rien de plus -- et surtout aucun reglage de voix :
le moteur, la voix, la vitesse, le volume et la pause sont ceux du bloc
« Generer une voix » juste au-dessus, lus au moment du rendu. Deux jeux de
reglages de voix sur le meme ecran finiraient par ne plus dire la meme chose.

CE QUI EST RECALCULE, ET CE QUI NE L'EST PAS. Changer le style de sous-titres,
le cadrage, le son ou la duree ne re-synthetise PAS la voix et ne relance PAS
son analyse : le panneau garde le fichier audio et les minutages obtenus, et ne
les jette que si le script, la voix ou ses reglages changent vraiment
(`_narration_signature`). C'est ce qui rend acceptable d'essayer trois styles
de sous-titres a la suite.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gui.voice_studio.video_workers import VideoCreationWorker
from voice_studio import narration as narration_module
from voice_studio import video_edit, video_service
from voice_studio.models import VideoSettings

ASPECT_CHOICES = [("16:9", "16:9 — horizontal (YouTube)"),
                  ("9:16", "9:16 — vertical (Reels, Shorts, TikTok)")]
FILL_CHOICES = [("flou", "Bords remplis par un flou de l'image"),
                ("noir", "Bandes noires")]


def _format_seconds(value) -> str:
    seconds = max(0.0, float(value or 0.0))
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}:{rest:02d}"


class VideoPanel(QWidget):
    """`voice_provider` renvoie (voice, rate, volume, pause) : c'est le bloc de
    voix de la page qui les detient, ce panneau ne fait que les lire."""

    status_changed = Signal(str)

    def __init__(self, voice_provider, script_provider=None, parent=None):
        super().__init__(parent)
        self._voice_provider = voice_provider
        self._script_provider = script_provider
        self._worker = None
        self._cancel_token = None
        self._source = ""
        self._source_title = ""
        self._source_duration_s = 0.0
        self._narration_wav = ""
        self._narration_transcript = None
        self._narration_signature = None
        self._last_output = ""
        self._whisper_model = "small"
        self._language = None
        self._device = "auto"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("🎬  CRÉER UNE VIDÉO")
        heading.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(heading)

        intro = QLabel(
            "Pose ta narration sur une vidéo téléchargée depuis Recherche. "
            "Les sous-titres sont calés sur les mots réellement prononcés par "
            "la voix générée, pas sur une durée estimée.")
        intro.setProperty("role", "muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        source_row = QHBoxLayout()
        self.source_label = QLabel("Aucune vidéo sélectionnée.")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source_row.addWidget(self.source_label, stretch=1)
        open_btn = QPushButton("Ouvrir un fichier vidéo…")
        open_btn.clicked.connect(self._choose_source)
        source_row.addWidget(open_btn)
        layout.addLayout(source_row)

        self.script_edit = QTextEdit()
        self.script_edit.setPlaceholderText("Script de narration : colle ton texte ici…")
        self.script_edit.setMaximumHeight(120)
        self.script_edit.textChanged.connect(self._on_script_changed)
        layout.addWidget(self.script_edit)

        script_row = QHBoxLayout()
        import_btn = QPushButton("Importer un fichier texte…")
        import_btn.clicked.connect(self._import_script)
        script_row.addWidget(import_btn)

        self.take_voice_btn = QPushButton("Reprendre le texte du bloc voix")
        self.take_voice_btn.clicked.connect(self._take_voice_text)
        script_row.addWidget(self.take_voice_btn)
        script_row.addStretch(1)
        self.stats_label = QLabel("")
        self.stats_label.setProperty("role", "muted")
        script_row.addWidget(self.stats_label)
        layout.addLayout(script_row)

        # --- options ------------------------------------------------------
        formats = QHBoxLayout()
        formats.addWidget(QLabel("Format"))
        self.aspect_combo = QComboBox()
        for value, label in ASPECT_CHOICES:
            self.aspect_combo.addItem(label, value)
        self.aspect_combo.currentIndexChanged.connect(self._update_enabled)
        formats.addWidget(self.aspect_combo)

        formats.addWidget(QLabel("Cadrage"))
        self.framing_combo = QComboBox()
        # Les deux premiers recadrent, le troisieme garde toute l'image. Une
        # seule liste plutot que deux commandes : ce sont trois reponses a la
        # meme question, « que fait-on de ce qui ne rentre pas dans le cadre ».
        for value in (video_edit.FRAMING_CENTER, video_edit.FRAMING_SUBJECT):
            self.framing_combo.addItem(video_edit.FRAMING_LABELS[value], value)
        self.framing_combo.addItem(video_edit.FIT_LABELS[video_edit.FIT_WHOLE],
                                   video_edit.FIT_WHOLE)
        self.framing_combo.currentIndexChanged.connect(self._update_enabled)
        formats.addWidget(self.framing_combo)

        formats.addWidget(QLabel("Bords"))
        self.fill_combo = QComboBox()
        for value, label in FILL_CHOICES:
            self.fill_combo.addItem(label, value)
        formats.addWidget(self.fill_combo)
        formats.addStretch(1)
        layout.addLayout(formats)

        audio = QHBoxLayout()
        audio.addWidget(QLabel("Son"))
        self.audio_combo = QComboBox()
        for value in video_edit.AUDIO_MODES:
            self.audio_combo.addItem(video_edit.AUDIO_LABELS[value], value)
        self.audio_combo.currentIndexChanged.connect(self._update_enabled)
        audio.addWidget(self.audio_combo, stretch=1)

        audio.addWidget(QLabel("Son d'origine"))
        self.original_slider = QSlider(Qt.Orientation.Horizontal)
        self.original_slider.setRange(0, 100)
        self.original_slider.setValue(int(video_edit.DEFAULT_ORIGINAL_VOLUME * 100))
        self.original_slider.setFixedWidth(120)
        self.original_slider.valueChanged.connect(self._update_stats)
        audio.addWidget(self.original_slider)
        self.original_label = QLabel("")
        audio.addWidget(self.original_label)
        layout.addLayout(audio)

        timing = QHBoxLayout()
        timing.addWidget(QLabel("Durée"))
        self.duration_combo = QComboBox()
        for value in video_edit.DURATION_POLICIES:
            self.duration_combo.addItem(video_edit.DURATION_LABELS[value], value)
        timing.addWidget(self.duration_combo, stretch=1)
        layout.addLayout(timing)

        subs = QHBoxLayout()
        self.subtitles_check = QCheckBox("Sous-titres incrustés")
        self.subtitles_check.setChecked(True)
        self.subtitles_check.toggled.connect(self._update_enabled)
        subs.addWidget(self.subtitles_check)

        self.style_combo = QComboBox()
        self._load_styles()
        subs.addWidget(self.style_combo)

        self.watermark_check = QCheckBox("Poser le filigrane")
        self.watermark_check.setChecked(True)
        subs.addWidget(self.watermark_check)
        subs.addStretch(1)
        layout.addLayout(subs)

        # --- actions ------------------------------------------------------
        actions = QHBoxLayout()
        self.preview_btn = QPushButton(f"▶  Aperçu ({int(video_service.PREVIEW_S)} s)")
        self.preview_btn.clicked.connect(lambda: self._start(preview=True))
        actions.addWidget(self.preview_btn)

        self.create_btn = QPushButton("🎬  Créer la vidéo")
        self.create_btn.setProperty("variant", "primary")
        self.create_btn.clicked.connect(lambda: self._start(preview=False))
        actions.addWidget(self.create_btn)

        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_btn)

        self.open_btn = QPushButton("Ouvrir le résultat")
        self.open_btn.clicked.connect(self._open_result)
        actions.addWidget(self.open_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self._update_stats()
        self._update_enabled()

    # -------------------------------------------------------------- source
    def _load_styles(self) -> None:
        from core.config_loader import load_subtitles_config

        config = load_subtitles_config()
        styles = config.get("styles", {}) or {}
        default_key = config.get("default_style") or ""
        for key, style in styles.items():
            self.style_combo.addItem(key, key)
            description = style.get("description", "")
            if description:
                self.style_combo.setItemData(self.style_combo.count() - 1,
                                             description, Qt.ItemDataRole.ToolTipRole)
        index = self.style_combo.findData(default_key)
        if index >= 0:
            self.style_combo.setCurrentIndex(index)

    def set_transcription_options(self, model: str, language, device: str) -> None:
        """Modele/langue/peripherique choisis en haut de la page : la voix
        generee est analysee avec les MEMES reglages, pas avec des valeurs
        cachees ici."""
        self._whisper_model = model or "small"
        self._language = language
        self._device = device or "auto"

    def set_source(self, path: str, title: str = "", duration_s=None) -> None:
        self._source = str(path or "")
        self._source_title = title or Path(self._source).name
        self._source_duration_s = float(duration_s or 0.0)
        if self._source and not self._source_duration_s:
            self._source_duration_s = video_service.measure_duration(self._source)
        exists = bool(self._source) and Path(self._source).is_file()
        if not exists:
            self.source_label.setText(
                f"⚠️ Fichier introuvable : {self._source or '—'}")
        else:
            duration = (f"  •  {_format_seconds(self._source_duration_s)}"
                        if self._source_duration_s else "")
            self.source_label.setText(f"🎥  {self._source_title}{duration}\n{self._source}")
        self._update_stats()
        self._update_enabled()

    def source_path(self) -> str:
        return self._source

    def _choose_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir une vidéo", str(Path.home()),
            "Vidéos (*.mp4 *.mkv *.webm *.mov *.avi);;Tous les fichiers (*)")
        if path:
            self.set_source(path)

    # -------------------------------------------------------------- script
    def _import_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer un script", str(Path.home()),
            "Fichiers texte (*.txt *.md);;Tous les fichiers (*)")
        if not path:
            return
        try:
            text = narration_module.read_script_file(path)
        except narration_module.ScriptError as error:
            QMessageBox.warning(self, "Import impossible", str(error))
            return
        self.script_edit.setPlainText(text)

    def _take_voice_text(self) -> None:
        text = self._script_provider() if self._script_provider else ""
        if not (text or "").strip():
            self.status_label.setText(
                "Le bloc « Générer une voix » est vide : écris un texte, ou "
                "reprends une réécriture, puis reviens ici.")
            return
        self.script_edit.setPlainText(text.strip())

    def script(self) -> str:
        return narration_module.clean_script(self.script_edit.toPlainText())

    def _on_script_changed(self) -> None:
        """Le compte de mots ET l'etat des boutons : un script colle dans le
        champ doit rendre « Creer la video » cliquable tout de suite, sans
        avoir a toucher a un autre reglage."""
        self._update_stats()
        self._update_enabled()

    # --------------------------------------------------------------- etats
    def _rate(self) -> float:
        try:
            return float(self._voice_provider()[1])
        except Exception:
            return 1.0

    def _update_stats(self) -> None:
        self.original_label.setText(f"{self.original_slider.value()} %")
        stats = narration_module.stats(self.script(), self._rate())
        pieces = []
        if stats.words:
            pieces.append(f"{stats.words} mots")
            pieces.append(f"narration estimée ≈ {_format_seconds(stats.estimated_s)}")
        if self._source_duration_s:
            pieces.append(f"vidéo {_format_seconds(self._source_duration_s)}")
        if stats.words and self._source_duration_s:
            gap = stats.estimated_s - self._source_duration_s
            if abs(gap) > 2:
                pieces.append("écart ≈ "
                              f"{'+' if gap > 0 else '−'}{_format_seconds(abs(gap))}")
        self.stats_label.setText("  •  ".join(pieces))

    def _keeps_whole_image(self) -> bool:
        """Vrai quand rien n'est rogne : en 16:9, ou en 9:16 « image entiere »."""
        return (self.aspect_combo.currentData() != "9:16"
                or self.framing_combo.currentData() == video_edit.FIT_WHOLE)

    def _update_enabled(self) -> None:
        vertical = self.aspect_combo.currentData() == "9:16"
        self.framing_combo.setEnabled(vertical)
        # Le remplissage des bords ne sert que si des bords restent a remplir :
        # en recadrant, l'image couvre deja tout le cadre.
        self.fill_combo.setEnabled(self._keeps_whole_image())
        self.original_slider.setEnabled(self.audio_combo.currentData() == video_edit.AUDIO_MIX)
        self.style_combo.setEnabled(self.subtitles_check.isChecked())

        running = self._worker is not None
        has_source = bool(self._source) and Path(self._source).is_file()
        needs_script = self.audio_combo.currentData() != video_edit.AUDIO_KEEP
        ready = has_source and (bool(self.script()) or not needs_script) and not running
        self.create_btn.setEnabled(ready)
        self.preview_btn.setEnabled(ready)
        self.take_voice_btn.setEnabled(not running)
        self.open_btn.setEnabled(bool(self._last_output)
                                 and Path(self._last_output).is_file())

    def settings(self) -> VideoSettings:
        # « Image entiere » est presente dans la liste des cadrages, mais c'est
        # un autre reglage : on le traduit ici plutot que de laisser un cadrage
        # porter deux sens.
        choice = self.framing_combo.currentData() or video_edit.FRAMING_CENTER
        whole = choice == video_edit.FIT_WHOLE
        return VideoSettings(
            aspect_ratio=self.aspect_combo.currentData() or "16:9",
            fill=self.fill_combo.currentData() or "flou",
            fit=video_edit.FIT_WHOLE if whole else video_edit.FIT_CROP,
            framing=video_edit.FRAMING_CENTER if whole else choice,
            audio_mode=self.audio_combo.currentData() or video_edit.AUDIO_REPLACE,
            original_volume=self.original_slider.value() / 100.0,
            narration_volume=video_edit.DEFAULT_NARRATION_VOLUME,
            duration_policy=self.duration_combo.currentData() or video_edit.DURATION_CUT,
            subtitles_enabled=self.subtitles_check.isChecked(),
            subtitle_style=self.style_combo.currentData() or "",
            watermark_enabled=self.watermark_check.isChecked(),
        )

    # --------------------------------------------------------------- rendu
    def _signature(self) -> tuple:
        voice, rate, volume, pause = self._voice_provider()
        return (self.script(), getattr(voice, "id", ""), round(float(rate), 3),
                round(float(volume), 3), round(float(pause), 2))

    def _start(self, preview: bool) -> None:
        if self._worker is not None:
            return
        if not (self._source and Path(self._source).is_file()):
            QMessageBox.information(
                self, "Aucune vidéo",
                "Choisis d'abord une vidéo : télécharge-la depuis l'onglet "
                "Recherche, ou ouvre un fichier déjà présent sur ton disque.")
            return

        signature = self._signature()
        if signature != self._narration_signature:
            # Le script ou la voix a change : la narration et ses minutages ne
            # correspondent plus a rien, on les oublie plutot que de risquer des
            # sous-titres cales sur une voix qui n'existe plus.
            self._narration_wav = ""
            self._narration_transcript = None

        voice, rate, volume, pause = self._voice_provider()
        settings = self.settings()

        if preview:
            out_path = str(video_service.work_dir() / "apercu.mp4")
        else:
            suggested = Path(self._source).stem + "-narre.mp4"
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer la vidéo", str(Path.home() / suggested),
                "Vidéo MP4 (*.mp4)")
            if not out_path:
                return

        request = video_service.VideoRequest(
            source_video=self._source,
            script=self.script(),
            out_path=out_path,
            settings=settings,
            voice=voice, rate=rate, volume=volume, sentence_pause_s=pause,
            whisper_model=self._whisper_model,
            language=self._language,
            device=self._device,
            preview=preview,
            narration_wav=self._narration_wav,
            narration_transcript=self._narration_transcript,
        )
        self._pending_signature = signature
        self._cancel_token = CancelToken()
        self._worker = VideoCreationWorker(request, self._cancel_token)
        self._worker.step.connect(self._on_step)
        self._worker.detail.connect(self._on_detail)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)

        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("Aperçu en préparation…" if preview
                                  else "Création de la vidéo…")
        self._update_enabled()
        self._worker.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Annulation en cours…")

    def _on_step(self, index: int, label: str) -> None:
        self.status_label.setText(f"{index}/{len(video_service.STEPS)}  {label}")

    def _on_detail(self, fraction, label: str) -> None:
        if fraction is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.status_label.setText(label)

    def _on_done(self, report) -> None:
        # La voix et ses minutages sont gardes : changer de style, de cadrage
        # ou de son ne les refera pas (voir l'entete du module).
        self._narration_wav = report.narration_wav or self._narration_wav
        if report.narration_transcript is not None:
            self._narration_transcript = report.narration_transcript
        self._narration_signature = getattr(self, "_pending_signature", None)
        self._last_output = report.output_path

        pieces = [f"vidéo {_format_seconds(report.video_s)}"]
        if report.narration_s:
            pieces.append(f"narration {_format_seconds(report.narration_s)}")
        pieces.append(f"sortie {_format_seconds(report.output_s)}")
        if report.caption_count:
            pieces.append(f"{report.caption_count} blocs de sous-titres")
        summary = "  •  ".join(pieces)
        prefix = "Aperçu prêt" if report.is_preview else "Vidéo créée"
        text = f"{prefix} : {report.output_path}\n{summary}"
        if report.notes:
            text += "\n⚠️ " + "\n⚠️ ".join(report.notes)
        self.status_label.setText(text)
        self.status_changed.emit(f"{prefix} : {report.output_path}")

        if report.is_preview:
            self._play(report.output_path)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("")
        QMessageBox.warning(self, "Création impossible", message)

    def _on_cancelled(self) -> None:
        self.status_label.setText("Création annulée. Aucun fichier n'a été produit.")

    def _on_finished(self) -> None:
        self._worker = None
        self._cancel_token = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._update_enabled()

    def _open_result(self) -> None:
        if self._last_output and Path(self._last_output).is_file():
            self._play(self._last_output)

    def _play(self, path: str) -> None:
        """Lecture dans le lecteur deja utilise pour les clips, avec un repli
        sur le lecteur du systeme : sur une machine sans codecs Qt Multimedia,
        une fenetre noire ne dirait rien de la video produite."""
        try:
            from gui.widgets.video_player import VideoPlayerDialog

            dialog = VideoPlayerDialog([(path, Path(path).name)], parent=self)
            dialog.exec()
        except Exception:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def cleanup(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(4000)
