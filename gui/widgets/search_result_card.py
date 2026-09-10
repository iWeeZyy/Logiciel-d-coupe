"""Carte resultat de recherche YouTube (section 9). N'affiche que des
metadonnees publiques deja renvoyees par l'API -- aucun contenu telecharge
tant que l'utilisateur n'a pas explicitement clique Analyser (et confirme
les droits, voir search_page.py)."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

THUMB_WIDTH = 160
THUMB_HEIGHT = 90


def _format_views(count) -> str:
    if count is None:
        return "vues inconnues"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f} M vues"
    if count >= 1_000:
        return f"{count / 1_000:.0f} k vues"
    return f"{count} vues"


def _format_duration(seconds) -> str:
    if not seconds:
        return "durée inconnue"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


class SearchResultCard(QFrame):
    analyze_requested = Signal(object)  # RankedVideo
    download_requested = Signal(object)  # RankedVideo -- video complete, sans decoupage

    def __init__(self, ranked_video, parent=None):
        super().__init__(parent)
        self.ranked_video = ranked_video
        video = ranked_video.video
        self.setProperty("role", "card")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        self.thumb_label = QLabel("🎬")
        self.thumb_label.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet("background: #EDEFF3; border-radius: 6px; color: #9498A2;")
        layout.addWidget(self.thumb_label)

        info = QVBoxLayout()
        info.setSpacing(4)

        title = QLabel(video.title)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 13.5px; font-weight: 700;")
        info.addWidget(title)

        channel = QLabel(video.channel_title)
        channel.setProperty("role", "muted")
        info.addWidget(channel)

        cc_suffix = "  •  Creative Commons" if video.license == "creativeCommon" else ""
        meta = QLabel(f"{_format_views(video.view_count)}  •  {_format_duration(video.duration_seconds)}{cc_suffix}")
        meta.setProperty("role", "muted")
        info.addWidget(meta)

        potential = QLabel(f"Potentiel : {ranked_video.score.total:.0f}/100")
        potential.setStyleSheet("font-weight: 700; color: #E0A24C; font-size: 12.5px;")
        info.addWidget(potential)

        layout.addLayout(info, stretch=1)

        actions = QVBoxLayout()
        analyze_btn = QPushButton("Analyser")
        analyze_btn.setProperty("variant", "primary")
        analyze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        analyze_btn.clicked.connect(lambda: self.analyze_requested.emit(self.ranked_video))
        actions.addWidget(analyze_btn)

        download_btn = QPushButton("⬇  Télécharger")
        download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        download_btn.setToolTip("Récupérer la vidéo entière, sans la découper en clips.")
        download_btn.clicked.connect(lambda: self.download_requested.emit(self.ranked_video))
        actions.addWidget(download_btn)

        open_btn = QPushButton("Ouvrir sur YouTube")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(video.url)))
        actions.addWidget(open_btn)
        actions.addStretch(1)

        layout.addLayout(actions)

    def set_thumbnail(self, jpeg_bytes: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(jpeg_bytes):
            scaled = pixmap.scaled(
                THUMB_WIDTH, THUMB_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.thumb_label.setPixmap(scaled)
