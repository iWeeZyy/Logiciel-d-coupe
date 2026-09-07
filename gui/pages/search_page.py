"""Page Recherche YouTube (section 9/10). Necessite internet -- seule
fonctionnalite de l'app dans ce cas, le traitement lui-meme reste local
(section 15)."""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
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
            self.results_layout.addWidget(card)
            self._cards[ranked.video.video_id] = card
            thumb_urls[ranked.video.video_id] = ranked.video.thumbnail_url

        if thumb_urls:
            self._thumb_thread = RemoteThumbnailThread(thumb_urls)
            self._thumb_thread.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._thumb_thread.start()

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
