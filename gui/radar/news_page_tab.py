"""Onglet "📰 News" du Radar : agrege les flux RSS/Atom configures
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
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import load_gaming_news_config
from gaming_news.feed_fetcher import fetch_all_sources
from gaming_news.sources import THEME_LABELS, THEMES, load_sources


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


_CARD_SUMMARY_MAX_CHARS = 300


def _card_summary(text: str) -> str:
    """Resume affiche sur la carte : sans les lignes de liens/reseaux des
    descriptions YouTube, sur une seule ligne logique, coupe sur un mot --
    une description de chaine fait souvent plusieurs ecrans."""
    from news_story.caption import drop_promo_lines

    text = " ".join(drop_promo_lines(text or "").split())
    if len(text) > _CARD_SUMMARY_MAX_CHARS:
        text = text[:_CARD_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text


def _rank_badge(article) -> str:
    """« ⭐ À la une » pour un article mis en avant par son site, « 🔥 Top n°X »
    pour une bande-annonce du top AlloCine (gaming_news/featured.py)."""
    rank = getattr(article, "rank", None)
    if rank is None:
        return ""
    if article.source_key == "allocine_top_trailers":
        return f"🔥 Top n°{rank + 1}"
    return "⭐ À la une"


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
        self._theme_by_source = {s.key: s.theme for s in self.sources}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(10)

        header = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 Actualiser")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        # Un fil par theme (un compte gaming, un compte cinema...) : le scan
        # recupere toutes les sources, le filtre ne fait que choisir quoi
        # afficher -- changer de fil est instantane, sans nouveau scan.
        # Des boutons visibles cote a cote plutot qu'une liste deroulante : la
        # liste n'affichait que « 🎮 Gaming », sans fleche, et le fil Cinema
        # restait introuvable (retour utilisateur).
        self._current_theme = ""
        self.theme_buttons: dict[str, QPushButton] = {}
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        choices = [(t, THEME_LABELS.get(t, t)) for t in THEMES
                   if any(s.theme == t for s in self.sources)]
        choices.append(("", "Tout"))
        for theme, label in choices:
            # « && » : un « & » seul serait pris par Qt pour un raccourci
            # clavier et affiche « Cinéma _séries ».
            btn = QPushButton(label.replace("&", "&&"))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, t=theme: self.set_theme(t))
            self._theme_group.addButton(btn)
            self.theme_buttons[theme] = btn
            header.addWidget(btn)
        self.set_theme(choices[0][0], render=False)

        # Plusieurs news cinema en un seul export (carrousel du jour).
        self.daily_top_btn = QPushButton("🗂 Top news ciné du jour")
        self.daily_top_btn.setToolTip("Plusieurs news ciné en un seul téléchargement : une "
                                      "couverture + une image par news + la légende.")
        self.daily_top_btn.clicked.connect(self._open_daily_top)
        header.addWidget(self.daily_top_btn)
        # Cle API Claude : le logiciel lit les articles cine et redige l'info.
        self.ai_btn = QPushButton("🔑 IA Claude")
        self.ai_btn.setToolTip("Clé API Claude : texte sous le titre rédigé à partir de "
                               "l'article (info principale, réponse à la question du titre).")
        self.ai_btn.clicked.connect(self._open_ai_settings)
        header.addWidget(self.ai_btn)
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

    def _theme_of(self, article) -> str:
        return self._theme_by_source.get(article.source_key, "gaming")

    def set_theme(self, theme: str, render: bool = True) -> None:
        """Choisit le fil affiche ("" = tous). Le bouton actif passe en
        style principal pour qu'on voie d'un coup d'oeil quel fil est ouvert."""
        if theme not in self.theme_buttons:
            return
        self._current_theme = theme
        for key, btn in self.theme_buttons.items():
            btn.setChecked(key == theme)
            btn.setProperty("variant", "primary" if key == theme else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if render:
            self._render()

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def _visible_articles(self) -> list:
        theme = self._current_theme
        return [a for a in self._articles if not theme or self._theme_of(a) == theme]

    def _render(self) -> None:
        self._clear()
        if not self._articles:
            self.status_label.setText(
                "Aucune actualité récupérée. Vérifiez la connexion internet, ou les adresses de "
                "flux dans config/gaming_news.json (un site a pu changer son adresse).")
            return
        visible = self._visible_articles()
        self.status_label.setText(f"{len(visible)} actualité(s) récupérée(s).")
        for article in visible:
            self.list_layout.addWidget(self._article_card(article))

    def _article_card(self, article) -> QFrame:
        frame, layout = _card()
        header = QHBoxLayout()
        header.addWidget(QLabel(article.source_label))
        badge = _rank_badge(article)
        if badge:
            badge_label = QLabel(badge)
            badge_label.setStyleSheet("color: #E0A24C; font-weight: 700;")
            header.addWidget(badge_label)
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

        summary_text = _card_summary(article.summary)
        if summary_text:
            summary = QLabel(summary_text)
            summary.setWordWrap(True)
            summary.setProperty("role", "muted")
            layout.addWidget(summary)

        actions = QHBoxLayout()
        is_trailer = self._theme_of(article) == "trailers"
        open_btn = QPushButton("▶ Voir la bande-annonce" if is_trailer else "📖 Lire l'article")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(article.url)))
        actions.addWidget(open_btn)

        story_btn = QPushButton("📸 Créer un visuel")
        story_btn.clicked.connect(lambda _=False, a=article: self._open_story_dialog(a))
        actions.addWidget(story_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        return frame

    def cinema_articles(self) -> list:
        """News du fil Cine et series, dans l'ordre du fil (unes en tete)."""
        return [a for a in self._articles if self._theme_of(a) == "cinema"]

    def _open_ai_settings(self) -> None:
        from gui.radar.claude_key_dialog import ClaudeKeyDialog

        ClaudeKeyDialog(self).exec()

    def _open_daily_top(self) -> None:
        from gui.radar.daily_top_dialog import DailyTopDialog

        articles = self.cinema_articles()
        if not articles:
            QMessageBox.information(self, "Top news ciné du jour",
                                    "Aucune news ciné récupérée pour l'instant : clique sur "
                                    "« Actualiser » puis réessaie.")
            return
        dialog = DailyTopDialog(articles, parent=self)
        dialog.exec()

    def _open_story_dialog(self, article) -> None:
        from gui.radar.story_dialog import StoryDialog

        dialog = StoryDialog(article, parent=self, theme=self._theme_of(article))
        dialog.exec()

    def cleanup(self) -> None:
        """Meme nom que les autres pages/onglets du Radar (voir
        RadarPage.cleanup) -- un QThread encore actif a la fermeture fait
        planter Qt."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(3000)
