"""Page ⚡ Voice Studio ZeroGPU -- BANC D'ESSAI, separe du Voice Studio.

POURQUOI UNE PAGE ET PAS UN ONGLET DANS LA PAGE EXISTANTE. La consigne etait
« isole autant que possible » et « supprimable entierement ». Une entree de
menu coute trois lignes dans gui/main_window.py ; un onglet aurait demande de
reorganiser la mise en page de gui/voice_studio/page.py, donc de modifier le
Voice Studio pour ajouter quelque chose qui doit pouvoir disparaitre.
Supprimer ce banc d'essai = effacer les fichiers zerogpu_* et retirer ces
trois lignes.

CE QUE CETTE PAGE NE FAIT PAS. Elle n'ecrit dans aucun projet Voice Studio,
n'utilise pas le cache de voix (tts.py), ne s'inscrit pas comme moteur. Le WAV
produit vit a part. La seule chose qu'elle emprunte est la transcription
Faster-Whisper locale -- justement ce qu'on veut comparer.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gui import settings_store
from gui.voice_studio.zerogpu_workers import TranscribeWorker, ZeroGpuWorker
from voice_studio import exporters, store
from voice_studio import zerogpu_bench as bench
from voice_studio import zerogpu_catalogue as catalogue
from voice_studio import zerogpu_token
from voice_studio.voice_progress import format_duration

BADGE = "GPU distant : Hugging Face ZeroGPU"


class ZeroGpuPage(QWidget):
    # (chemin du WAV, script, langue). La page NE CONNAIT PAS la creation
    # video : elle annonce qu'une narration est prete, et la fenetre principale
    # decide ou l'envoyer. C'est ce qui permet de supprimer ce banc d'essai
    # sans toucher a la creation video.
    narration_ready = Signal(str, str, str)

    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller
        self._worker = None
        self._transcriber = None
        self._cancel = None
        self._outcome = None
        self._transcript = None
        self._started = 0.0
        self._phase = ""

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 28, 40, 20)
        outer.setSpacing(10)

        title = QLabel("⚡  VOICE STUDIO ZEROGPU")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)

        subtitle = QLabel(
            "Banc d'essai : Chatterbox V3 exécuté sur un GPU distant, pour le "
            "comparer au Chatterbox local et à Piper. Aucune vitesse n'a été "
            "mesurée avant ta première génération : les chiffres affichés "
            "ci-dessous viennent de la documentation de Hugging Face, pas d'un "
            "essai.")
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self.body.addWidget(self._build_state_card())
        self.body.addWidget(self._build_script_card(), stretch=1)
        self.body.addWidget(self._build_result_card())
        self.body.addWidget(self._build_bench_card())
        self.body.addStretch(1)

        self._refresh_state()

    # ------------------------------------------------------------- cartes
    def _card(self, heading: str) -> tuple:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        label = QLabel(heading)
        label.setProperty("role", "cardTitle")
        layout.addWidget(label)
        return frame, layout

    def _build_state_card(self) -> QFrame:
        frame, layout = self._card(BADGE)

        self.space_label = QLabel("")
        self.space_label.setWordWrap(True)
        self.space_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.space_label)

        self.token_label = QLabel("")
        self.token_label.setProperty("role", "muted")
        self.token_label.setWordWrap(True)
        layout.addWidget(self.token_label)

        self.quota_label = QLabel("")
        self.quota_label.setProperty("role", "muted")
        self.quota_label.setWordWrap(True)
        layout.addWidget(self.quota_label)
        return frame

    def _build_script_card(self) -> QFrame:
        frame, layout = self._card("Script à lire")

        self.script_edit = QTextEdit()
        self.script_edit.setPlaceholderText(
            "Colle ici le texte de la narration…")
        self.script_edit.setMinimumHeight(150)
        self.script_edit.textChanged.connect(self._refresh_plan)
        layout.addWidget(self.script_edit)

        self.plan_label = QLabel("")
        self.plan_label.setProperty("role", "muted")
        self.plan_label.setWordWrap(True)
        layout.addWidget(self.plan_label)

        settings = QHBoxLayout()
        settings.addWidget(QLabel("Langue"))
        self.language_combo = QComboBox()
        for code, label in (("fr", "Français"), ("en", "Anglais"), ("es", "Espagnol"),
                            ("de", "Allemand"), ("it", "Italien"), ("pt", "Portugais")):
            self.language_combo.addItem(label, code)
        settings.addWidget(self.language_combo)

        defaults = catalogue.defaults()
        settings.addSpacing(12)
        settings.addWidget(QLabel("Expressivité"))
        self.exaggeration_spin = self._spin(defaults["exaggeration"], 0.25, 2.0)
        settings.addWidget(self.exaggeration_spin)

        settings.addWidget(QLabel("Rythme"))
        self.cfg_spin = self._spin(defaults["cfg_weight"], 0.0, 1.0)
        settings.addWidget(self.cfg_spin)

        settings.addWidget(QLabel("Variation"))
        self.temperature_spin = self._spin(defaults["temperature"], 0.05, 5.0)
        settings.addWidget(self.temperature_spin)
        settings.addStretch(1)
        layout.addLayout(settings)

        row = QHBoxLayout()
        self.generate_btn = QPushButton("⚡  Générer sur ZeroGPU")
        self.generate_btn.setProperty("variant", "primary")
        self.generate_btn.clicked.connect(self._generate)
        row.addWidget(self.generate_btn)

        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_run)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return frame

    def _spin(self, value: float, low: float, high: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(float(value))
        spin.setFixedWidth(80)
        return spin

    def _build_result_card(self) -> QFrame:
        frame, layout = self._card("Résultat")

        self.measure_label = QLabel("Aucune génération pour l'instant.")
        self.measure_label.setWordWrap(True)
        self.measure_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.measure_label)

        self.detail_label = QLabel("")
        self.detail_label.setProperty("role", "muted")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        row = QHBoxLayout()
        self.play_btn = QPushButton("🔊  Écouter")
        self.play_btn.clicked.connect(self._play)
        row.addWidget(self.play_btn)

        self.save_btn = QPushButton("Enregistrer le WAV")
        self.save_btn.clicked.connect(self._save_wav)
        row.addWidget(self.save_btn)

        self.transcribe_btn = QPushButton("Transcrire (Faster-Whisper local)")
        self.transcribe_btn.clicked.connect(self._transcribe)
        row.addWidget(self.transcribe_btn)

        self.srt_btn = QPushButton("Exporter SRT")
        self.srt_btn.clicked.connect(self._export_srt)
        row.addWidget(self.srt_btn)

        self.to_video_btn = QPushButton("🎬  Utiliser dans la création vidéo")
        self.to_video_btn.setProperty("variant", "primary")
        self.to_video_btn.clicked.connect(self._send_to_video)
        row.addWidget(self.to_video_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.transcript_label = QLabel("")
        self.transcript_label.setProperty("role", "muted")
        self.transcript_label.setWordWrap(True)
        layout.addWidget(self.transcript_label)
        return frame

    def _build_bench_card(self) -> QFrame:
        frame, layout = self._card("Banc d'essai")

        self.bench_label = QLabel("")
        self.bench_label.setWordWrap(True)
        self.bench_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.bench_label)

        row = QHBoxLayout()
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self._refresh_bench)
        row.addWidget(refresh)

        reveal = QPushButton("Ouvrir le journal")
        reveal.clicked.connect(self._open_bench)
        row.addWidget(reveal)
        row.addStretch(1)
        layout.addLayout(row)
        self._refresh_bench()
        return frame

    # -------------------------------------------------------------- etat
    def _refresh_state(self) -> None:
        space = catalogue.space_id()
        self.space_label.setText(
            f"Space : {space}" if space
            else "Aucun Space configuré (voir config/zerogpu.json).")

        source = zerogpu_token.describe()
        self.token_label.setText(
            f"Jeton Hugging Face : {source}." if source
            else "Aucun jeton Hugging Face : le quota anonyme s'applique.")

        note = catalogue.quota_note()
        daily = note.get("daily_seconds") or {}
        if daily:
            self.quota_label.setText(
                f"Quota GPU journalier annoncé par Hugging Face le "
                f"{note.get('checked_on', '?')} : "
                f"{int(daily.get('anonymous', 0)) // 60} min sans compte, "
                f"{int(daily.get('free', 0)) // 60} min avec un compte gratuit, "
                f"{int(daily.get('pro', 0)) // 60} min avec PRO. "
                f"Matériel : {note.get('hardware', '?')}. "
                "Ces chiffres changent : ils sont affichés à titre de rappel, "
                "l'application ne s'en sert pour aucun calcul.")
        self._refresh_plan()
        self._refresh_buttons()

    def _refresh_plan(self) -> None:
        script = self.script_edit.toPlainText().strip()
        if not script:
            self.plan_label.setText("")
            self._refresh_buttons()
            return
        pieces = catalogue.chunks(script)
        limit = catalogue.limits()["max_chars_per_chunk"]
        self.plan_label.setText(
            f"{catalogue.word_count(script)} mots, {len(script)} caractères → "
            f"{len(pieces)} morceau(x) de {limit} caractères maximum, "
            "recollés en un seul fichier. Le découpage ne coupe jamais un mot "
            "ni une phrase quand il peut l'éviter.")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        busy = self._worker is not None or self._transcriber is not None
        has_script = bool(self.script_edit.toPlainText().strip())
        has_audio = bool(self._outcome and Path(self._outcome.wav_path).is_file())
        self.generate_btn.setEnabled(has_script and not busy and bool(catalogue.space_id()))
        self.play_btn.setEnabled(has_audio)
        self.save_btn.setEnabled(has_audio)
        self.transcribe_btn.setEnabled(has_audio and not busy)
        self.srt_btn.setEnabled(self._transcript is not None)
        self.to_video_btn.setEnabled(has_audio and not busy)

    # --------------------------------------------------------- generation
    def _params(self) -> catalogue.Params:
        return catalogue.params_for(
            language=self.language_combo.currentData() or "fr",
            exaggeration=self.exaggeration_spin.value(),
            cfg_weight=self.cfg_spin.value(),
            temperature=self.temperature_spin.value(),
        )

    def _generate(self) -> None:
        script = self.script_edit.toPlainText().strip()
        if not script or self._worker is not None:
            return
        self._outcome = None
        self._transcript = None
        self.transcript_label.setText("")
        self._cancel = CancelToken()
        self._started = time.monotonic()
        self._phase = "connexion"

        out_wav = str(Path(store.audio_dir()) / "zerogpu" / "narration.wav")
        self._worker = ZeroGpuWorker(script, out_wav, self._params(), self._cancel)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.progress.connect(self._on_progress)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)

        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("Connexion au Space…")
        self._timer.start()
        self._refresh_buttons()
        self._worker.start()

    def _cancel_run(self) -> None:
        if self._cancel is not None:
            self._cancel.cancel()
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Annulation en cours…")

    def _tick(self) -> None:
        if self._worker is None:
            return
        elapsed = format_duration(time.monotonic() - self._started)
        self.status_label.setText(f"{self._phase} · {elapsed}")

    def _on_progress(self, event: dict) -> None:
        kind = (event or {}).get("event")
        if kind == "connected":
            self._phase = f"connecté à {event.get('label', '')}"
        elif kind == "chunk":
            index, total = event.get("index"), event.get("total")
            self.progress.setRange(0, int(total or 1))
            self.progress.setValue(int(index or 1) - 1)
            self._phase = f"morceau {index}/{total}"
        elif kind == "status":
            if event.get("in_queue"):
                rank, size = event.get("rank"), event.get("queue_size")
                position = f" (position {rank + 1}/{size})" if rank is not None and size else ""
                self._phase = (f"file d'attente{position} · morceau "
                               f"{event.get('index')}/{event.get('total')}")
            else:
                self._phase = (f"génération GPU · morceau "
                               f"{event.get('index')}/{event.get('total')}")
        elif kind == "chunk_done":
            self.progress.setValue(int(event.get("index") or 0))
            self._phase = (f"morceau {event.get('index')}/{event.get('total')} terminé "
                           f"(file {event.get('queue_s')} s, GPU {event.get('gpu_s')} s)")
        elif kind == "retry":
            self._phase = (f"nouvelle tentative du morceau {event.get('index')} "
                           f"dans {int(event.get('pause_s') or 0)} s")
        self._tick()

    def _on_done(self, outcome) -> None:
        self._outcome = outcome
        measure = outcome.measure
        self.measure_label.setText(catalogue.summarize(measure))

        rtf_total = measure.rtf_total
        self.detail_label.setText(
            f"{measure.chunks} morceau(x) · "
            f"attente {format_duration(measure.queue_s)} · "
            f"GPU {format_duration(measure.gpu_s)} · "
            f"total {format_duration(measure.total_s)}"
            + (f" · RTF total {rtf_total:.2f}".replace(".", ",") if rtf_total else "")
            + f"\nFichier : {outcome.wav_path}")

        entry = bench.entry_from(
            measure, engine="zerogpu",
            space=outcome.connection.space if outcome.connection else "",
            label=(self.script_edit.toPlainText().strip()[:40] or "sans titre"),
            note=" ".join(outcome.notes))
        bench.append(entry)
        self._refresh_bench()

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("")
        QMessageBox.warning(self, "Génération impossible", message)

    def _on_cancelled(self) -> None:
        self.status_label.setText("Génération annulée.")

    def _on_finished(self) -> None:
        self._timer.stop()
        self._worker = None
        self._cancel = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._refresh_buttons()

    # ------------------------------------------------------------ apres
    def _play(self) -> None:
        if not (self._outcome and Path(self._outcome.wav_path).is_file()):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._outcome.wav_path))

    def _save_wav(self) -> None:
        if not (self._outcome and Path(self._outcome.wav_path).is_file()):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le WAV", "narration_zerogpu.wav", "Audio (*.wav)")
        if not path:
            return
        import shutil

        shutil.copyfile(self._outcome.wav_path, path)
        self.detail_label.setText(f"Enregistré : {path}")

    def _send_to_video(self) -> None:
        """Envoie la narration vers la creation video de Voice Studio.

        La transcription N'EST PAS faite ici, meme si le bouton « Transcrire »
        existe juste a cote : la creation video la refait de toute facon, avec
        son propre cache, et la lui imposer depuis ce banc d'essai creerait
        deux chemins pour la meme chose.
        """
        if not (self._outcome and Path(self._outcome.wav_path).is_file()):
            return
        self.narration_ready.emit(
            self._outcome.wav_path,
            self.script_edit.toPlainText().strip(),
            self.language_combo.currentData() or "fr")

    def _transcribe(self) -> None:
        if not (self._outcome and Path(self._outcome.wav_path).is_file()):
            return
        if self._transcriber is not None:
            return
        self._cancel = CancelToken()
        self._transcriber = TranscribeWorker(
            self._outcome.wav_path,
            settings_store.get("default_model") or "small",
            self.language_combo.currentData() or "fr",
            settings_store.get("default_device") or "auto",
            self._cancel)
        self._transcriber.done.connect(self._on_transcribed)
        self._transcriber.failed.connect(self._on_failed)
        self._transcriber.detail.connect(
            lambda fraction, label: self.transcript_label.setText(label))
        self._transcriber.finished.connect(self._on_transcribe_finished)
        self.transcript_label.setText("Transcription locale en cours…")
        self._refresh_buttons()
        self._transcriber.start()

    def _on_transcribed(self, transcript) -> None:
        self._transcript = transcript
        segments = list(getattr(transcript, "segments", []) or [])
        words = sum(len((getattr(segment, "text", "") or "").split())
                    for segment in segments)
        self.transcript_label.setText(
            f"Transcription locale : {len(segments)} segments, {words} mots. "
            "C'est elle qui fournira les horodatages des sous-titres.")

    def _on_transcribe_finished(self) -> None:
        self._transcriber = None
        self._cancel = None
        self._refresh_buttons()

    def _export_srt(self) -> None:
        if self._transcript is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter en SRT", "narration_zerogpu.srt", "Sous-titres (*.srt)")
        if not path:
            return
        written = exporters.write(self._transcript, path, fmt="srt")
        self.transcript_label.setText(f"Exporté : {written}")

    # ------------------------------------------------------- banc d'essai
    def _refresh_bench(self) -> None:
        entries = bench.load()
        if not entries:
            self.bench_label.setText(
                "Aucun essai enregistré. Chaque génération ajoute une ligne : "
                "mots, durée d'audio, attente, génération, total et RTF.")
            return
        summary = bench.compare(entries)
        lines = [f"{len(entries)} essai(s) enregistré(s)."]
        for engine, stats in sorted(summary.items()):
            average = f"{stats['rtf']:.2f}".replace(".", ",")
            lines.append(f"• {engine} : RTF moyen {average} sur {stats['runs']} essai(s)")
        if len(entries) == 1:
            lines.append("Une seule mesure ne fait pas une moyenne : la file "
                         "d'attente varie selon l'heure et le réveil du Space.")
        self.bench_label.setText("\n".join(lines))

    def _open_bench(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        target = bench.path()
        if not target.is_file():
            self.bench_label.setText("Le journal n'existe pas encore.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
