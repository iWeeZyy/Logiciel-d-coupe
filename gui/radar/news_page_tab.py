"""Onglet "📰 Gaming News" du Radar : agrege les flux RSS/Atom configures
(config/gaming_news.json) et propose, pour chaque article, de l'ouvrir ou d'en
tirer une Story verticale (news_story/, gui/radar/story_dialog.py).

Concept volontairement separe du reste du Radar (radar/models.py :
Creator/Opportunity) -- une actualite de presse n'est pas un contenu produit
par un createur surveille, rien du scoring/tendance existant ne s'applique
ici. Le scan tourne dans un QThread, meme convention que ScanThread dans
radar_page.py : le reseau ne doit jamais figer l'interface.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import load_gaming_news_config
from gaming_news.feed_fetcher import fetch_all_sources
from gaming_news.sources import load_sources


class NewsScanThread(QThread):
    """Recupere tous les flux configures, hors du fil de l'interface."""

    finished_ok = Signal(list)  # list[Article]
    failed = Signal(str)

    def __init__(self, sources, timeout_s, max_articles):
        super().__init__()
        self.sources = sources
        self.timeout_s = timeout_s
        self.max_articles = max_articles

    def run(self) -> None:
        try:
            articles = fetch_all_sources(self.sources, timeout_s=self.timeout_s,
                                         max_articles=self.max_articles)
        except Exception as error:  # noqa: BLE001 -- filet de securite, fetch_all_sources ne leve pas
            self.failed.emit(f"Erreur inattendue pendant la récupération des actualités : {error}")
        else:
            self.finished_ok.emit(articles)


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(8)
    return frame, layout


class GamingNewsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_gaming_news_config()
        self.sources = load_sources(self.config)
        scan_cfg = self.config.get("scan", {}) or {}
        self.timeout_s = scan_cfg.get("timeout_s", 10)
        self.max_articles = scan_cfg.get("max_articles_per_source", 20)
        self._thread: NewsScanThread | None = None
        self._articles: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(10)

        header = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 Actualiser")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        header.addStretch(1)
        outer.addLayout(header)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "muted")
        outer.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.list_layout = QVBoxLayout(content)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(12)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------ rendu
    def on_shown(self) -> None:
        # Le premier affichage declenche un scan automatique : une page vide
        # au premier onglet ouvert ne dirait rien de plus qu'un scan raterait
        # a decouvrir tout seul. Les affichages suivants ne rescannent pas --
        # c'est le bouton qui decide, comme le reste du Radar.
        if not self._articles and (self._thread is None or not self._thread.isRunning()):
            self.refresh()

    def _clear(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def refresh(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Récupération des actualités…")
        self._thread = NewsScanThread(self.sources, self.timeout_s, self.max_articles)
        self._thread.finished_ok.connect(self._on_finished)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_finished(self, articles) -> None:
        self.refresh_btn.setEnabled(True)
        self._articles = articles
        self._render()

    def _on_failed(self, message: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(message)

    def _render(self) -> None:
        self._clear()
        if not self._articles:
            self.status_label.setText(
                "Aucune actualité récupérée. Vérifiez la connexion internet, ou les adresses de "
                "flux dans config/gaming_news.json (un site a pu changer son adresse).")
            return
        self.status_label.setText(f"{len(self._articles)} actualité(s) récupérée(s).")
        for article in self._articles:
            self.list_layout.addWidget(self._article_card(article))

    def _article_card(self, article) -> QFrame:
        frame, layout = _card()
        header = QHBoxLayout()
        header.addWidget(QLabel(article.source_label))
        header.addStretch(1)
        if article.published_at:
            date_label = QLabel(article.published_at[:16])
            date_label.setProperty("role", "muted")
            header.addWidget(date_label)
        layout.addLayout(header)

        title = QLabel(article.title)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        if article.summary:
            summary = QLabel(article.summary)
            summary.setWordWrap(True)
            summary.setProperty("role", "muted")
            layout.addWidget(summary)

        actions = QHBoxLayout()
        open_btn = QPushButton("📖 Lire l'article")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(article.url)))
        actions.addWidget(open_btn)

        story_btn = QPushButton("📸 Créer une Story")
        story_btn.clicked.connect(lambda _=False, a=article: self._open_story_dialog(a))
        actions.addWidget(story_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        return frame

    def _open_story_dialog(self, article) -> None:
        from gui.radar.story_dialog import StoryDialog

        dialog = StoryDialog(article, parent=self)
        dialog.exec()

    def cleanup(self) -> None:
        """Meme nom que les autres pages/onglets du Radar (voir
        RadarPage.cleanup) -- un QThread encore actif a la fermeture fait
        planter Qt."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(3000)
