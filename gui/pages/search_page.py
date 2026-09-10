"""Page Recherche YouTube (section 9/10). Necessite internet -- seule
fonctionnalite de l'app dans ce cas, le traitement lui-meme reste local
(section 15)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QMessageBox,
    QProgressBar,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.controller import AppController
from gui.thumbnails import RemoteThumbnailThread
from gui.widgets.search_result_card import SearchResultCard
from gui.widgets.youtube_analyze_dialog import YoutubeAnalyzeDialog
from gui.widgets.youtube_download_dialog import YoutubeDownloadDialog
from youtube.models import SearchFilters

_SORT_CHOICES = [("potential", "Potentiel"), ("relevance", "Pertinence"), ("views", "Vues"), ("date", "Date")]
_DURATION_CHOICES = [(None, "Toutes durées"), ("short", "Courte (<4 min)"), ("medium", "Moyenne (4-20 min)"), ("long", "Longue (>20 min)")]
_LANGUAGE_CHOICES = [(None, "Toutes langues"), ("fr", "Français"), ("en", "Anglais"), ("es", "Espagnol"), ("de", "Allemand")]


class SearchPage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self._thumb_thread: RemoteThumbnailThread | None = None
        self._cards: dict[str, SearchResultCard] = {}
        self._download_thread = None
        self._download_video = None
        self._open_in_voice_studio = False

        controller.search_finished.connect(self._on_search_finished)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(14)

        title = QLabel("Recherche")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)

        subtitle = QLabel("Recherche de vidéos YouTube — nécessite une connexion internet. Le traitement reste local.")
        subtitle.setProperty("role", "subtitle")
        outer.addWidget(subtitle)

        search_row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("podcast entrepreneuriat français")
        self.query_edit.returnPressed.connect(self._run_search)
        search_row.addWidget(self.query_edit, stretch=1)

        search_btn = QPushButton("🔍 Rechercher")
        search_btn.setProperty("variant", "primary")
        search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_btn.clicked.connect(self._run_search)
        search_row.addWidget(search_btn)
        outer.addLayout(search_row)

        filters_row = QHBoxLayout()
        filters_row.setSpacing(10)

        self.language_combo = QComboBox()
        for value, label in _LANGUAGE_CHOICES:
            self.language_combo.addItem(label, value)
        filters_row.addWidget(self.language_combo)

        self.duration_combo = QComboBox()
        for value, label in _DURATION_CHOICES:
            self.duration_combo.addItem(label, value)
        filters_row.addWidget(self.duration_combo)

        self.sort_combo = QComboBox()
        for value, label in _SORT_CHOICES:
            self.sort_combo.addItem(f"Trier par : {label}", value)
        filters_row.addWidget(self.sort_combo)

        filters_row.addWidget(QLabel("Vues min."))
        self.min_views_spin = QSpinBox()
        self.min_views_spin.setRange(0, 1_000_000_000)
        self.min_views_spin.setSingleStep(1000)
        self.min_views_spin.setSpecialValueText("Aucun minimum")
        filters_row.addWidget(self.min_views_spin)

        self.cc_checkbox = QCheckBox("Licence CC")
        filters_row.addWidget(self.cc_checkbox)
        filters_row.addStretch(1)
        outer.addLayout(filters_row)

        filters_row2 = QHBoxLayout()
        filters_row2.addWidget(QLabel("Publié après"))
        self.published_after_edit = QDateEdit()
        self.published_after_edit.setCalendarPopup(True)
        self.published_after_edit.setDate(self.published_after_edit.minimumDate())
        self.published_after_edit.setSpecialValueText("Aucune date")
        filters_row2.addWidget(self.published_after_edit)

        filters_row2.addWidget(QLabel("Chaîne (ID)"))
        self.channel_edit = QLineEdit()
        self.channel_edit.setFixedWidth(140)
        filters_row2.addWidget(self.channel_edit)

        filters_row2.addWidget(QLabel("Catégorie (ID)"))
        self.category_edit = QLineEdit()
        self.category_edit.setFixedWidth(60)
        filters_row2.addWidget(self.category_edit)
        filters_row2.addStretch(1)
        outer.addLayout(filters_row2)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        outer.addWidget(self.status_label)

        # Bande de telechargement : cachee tant qu'aucun transfert n'est en
        # cours. Placee ici, au-dessus des resultats, pour rester visible quand
        # la liste defile -- un transfert de plusieurs centaines de megaoctets
        # ne doit pas disparaitre de l'ecran parce qu'on a scrolle.
        self.download_row = QWidget()
        download_layout = QHBoxLayout(self.download_row)
        download_layout.setContentsMargins(0, 0, 0, 0)
        self.download_label = QLabel("")
        self.download_label.setProperty("role", "muted")
        self.download_label.setWordWrap(True)
        download_layout.addWidget(self.download_label, stretch=1)
        self.download_progress = QProgressBar()
        self.download_progress.setFixedWidth(220)
        download_layout.addWidget(self.download_progress)
        self.download_cancel_btn = QPushButton("Annuler")
        self.download_cancel_btn.clicked.connect(self._cancel_download)
        download_layout.addWidget(self.download_cancel_btn)
        self.download_row.setVisible(False)
        outer.addWidget(self.download_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setSpacing(12)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.results_container)

    def _build_filters(self) -> SearchFilters:
        published_after = None
        if self.published_after_edit.date() != self.published_after_edit.minimumDate():
            published_after = self.published_after_edit.date().toString("yyyy-MM-dd") + "T00:00:00Z"

        return SearchFilters(
            language=self.language_combo.currentData(),
            order="relevance",
            video_duration_bucket=self.duration_combo.currentData(),
            min_view_count=self.min_views_spin.value() or None,
            published_after=published_after,
            channel_id=self.channel_edit.text().strip() or None,
            category_id=self.category_edit.text().strip() or None,
            creative_commons_only=self.cc_checkbox.isChecked(),
        )

    def _run_search(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            return
        self._clear_results()
        self.status_label.setText("Recherche en cours…")
        self.controller.start_search(query, self._build_filters(), max_results=20, sort_by=self.sort_combo.currentData())

    def _clear_results(self) -> None:
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

    def _on_search_finished(self, ranked_videos: list) -> None:
        self.status_label.setText(f"{len(ranked_videos)} résultat(s)")
        thumb_urls = {}
        for ranked in ranked_videos:
            card = SearchResultCard(ranked)
            card.analyze_requested.connect(self._on_analyze_requested)
            card.download_requested.connect(self._on_download_requested)
            self.results_layout.addWidget(card)
            self._cards[ranked.video.video_id] = card
            thumb_urls[ranked.video.video_id] = ranked.video.thumbnail_url

        if thumb_urls:
            self._thumb_thread = RemoteThumbnailThread(thumb_urls)
            self._thumb_thread.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._thumb_thread.start()

    # ------------------------------------------------- telechargement complet
    def _on_download_requested(self, ranked_video) -> None:
        """Recuperer la video ENTIERE, sans analyse ni decoupage.

        Aucune etape du pipeline n'est lancee : ni transcription, ni scoring,
        ni rendu. C'est tout l'interet de ce bouton -- une video complete sert
        a autre chose qu'a produire des clips (montage, narration dans Voice
        Studio, archive), et lui faire traverser l'analyse serait payer
        plusieurs minutes de calcul pour un resultat dont on ne veut pas.
        """
        if self._download_thread is not None and self._download_thread.isRunning():
            QMessageBox.information(
                self, "Téléchargement en cours",
                "Un téléchargement est déjà en cours. Attends qu'il finisse, "
                "ou annule-le avant d'en lancer un autre.")
            return

        video = ranked_video.video
        dialog = YoutubeDownloadDialog(video.title, video.duration_seconds, parent=self)
        if dialog.exec() != YoutubeDownloadDialog.DialogCode.Accepted:
            return

        self._download_video = video
        self._open_in_voice_studio = dialog.open_in_voice_studio()
        self._download_thread = self.controller.start_video_download(
            video.url, dialog.destination(), dialog.max_height())
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.finished_ok.connect(self._on_download_finished)
        self._download_thread.failed.connect(self._on_download_failed)
        self._download_thread.cancelled.connect(self._on_download_cancelled)
        self._download_thread.finished.connect(self._on_download_thread_finished)

        self.download_row.setVisible(True)
        self.download_cancel_btn.setEnabled(True)
        self.download_progress.setRange(0, 0)
        self.download_label.setText(f"Téléchargement : {video.title}")
        self._download_thread.start()

    def _on_download_progress(self, fraction, done_mb: float, total_mb) -> None:
        if fraction is None:
            self.download_progress.setRange(0, 0)
        else:
            self.download_progress.setRange(0, 100)
            self.download_progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        size = (f"{done_mb:.0f} / {total_mb:.0f} Mo" if total_mb else f"{done_mb:.0f} Mo")
        title = self._download_video.title if self._download_video else ""
        self.download_label.setText(f"Téléchargement : {title}  —  {size}")

    def _cancel_download(self) -> None:
        self.controller.cancel_video_download()
        self.download_cancel_btn.setEnabled(False)
        self.download_label.setText("Annulation en cours…")

    def _on_download_finished(self, path: str) -> None:
        video = self._download_video
        self.download_label.setText(f"Vidéo téléchargée : {path}")
        if video is not None and self._open_in_voice_studio:
            self._send_to_voice_studio(path, video)
            return

        box = QMessageBox(self)
        box.setWindowTitle("Téléchargement terminé")
        box.setText(f"La vidéo est enregistrée ici :\n{path}")
        voice_btn = box.addButton("Ouvrir dans Voice Studio", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("Ouvrir le dossier", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Fermer", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() is voice_btn and video is not None:
            self._send_to_voice_studio(path, video)
        elif box.clickedButton() is folder_btn:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _send_to_voice_studio(self, path: str, video) -> None:
        self.controller.open_in_voice_studio.emit({
            "path": path,
            "title": video.title,
            "video_id": video.video_id,
            "url": video.url,
            "duration_s": video.duration_seconds,
        })

    def _on_download_failed(self, message: str) -> None:
        self.download_row.setVisible(False)
        QMessageBox.warning(self, "Téléchargement impossible", message)

    def _on_download_cancelled(self) -> None:
        self.download_row.setVisible(False)
        self.status_label.setText("Téléchargement annulé.")

    def _on_download_thread_finished(self) -> None:
        self._download_thread = None
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(100)

    def cleanup(self) -> None:
        """A la fermeture de l'appli -- voir AppController.shutdown()."""
        if self._thumb_thread is not None and self._thumb_thread.isRunning():
            self._thumb_thread.wait(2000)
            if self._thumb_thread.isRunning():
                self._thumb_thread.terminate()
                self._thumb_thread.wait()

    def _on_thumbnail_ready(self, video_id: str, jpeg_bytes: bytes) -> None:
        card = self._cards.get(video_id)
        if card:
            card.set_thumbnail(jpeg_bytes)

    def _on_analyze_requested(self, ranked_video) -> None:
        video = ranked_video.video
        dialog = YoutubeAnalyzeDialog(video.title, parent=self)
        if dialog.exec() != YoutubeAnalyzeDialog.DialogCode.Accepted:
            return

        cli_args = SimpleNamespace(
            input="",  # rempli par AnalysisThread apres telechargement (youtube_source est fourni)
            clip_duration=dialog.clip_duration(),
            nb_clips=dialog.nb_clips(),
            model=dialog.model(),
            language=None,
            pre_roll=None,
            post_roll=None,
            min_gap=None,
            subtitle_style=None,
            device=None,
            no_cache=False,
            debug_scores=False,
        )
        self.controller.start_analysis(
            cli_args,
            name=video.title[:60],
            source_label=video.title,
            source_kind="youtube",
            source_url=video.url,
            youtube_source=video.video_id,
        )
