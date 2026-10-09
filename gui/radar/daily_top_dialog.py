"""Fenetre « Top news ciné du jour » : choisir plusieurs news cinema et les
exporter d'un coup -- une couverture + une image 9:16 par news + la legende,
dans un seul dossier, prets pour un carrousel TikTok / Instagram
(news_story/daily_top.py)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.cancellation import CancelToken

_SETTINGS_ORG, _SETTINGS_APP = "ClipFarming", "NewsVisuals"
MAX_ITEMS = 10          # au-dela, un carrousel se lit mal


class _TopThread(QThread):
    progress = Signal(str)
    ready = Signal(str, str, str)  # dossier, legende, avertissement IA
    failed = Signal(str)

    def __init__(self, articles, out_dir: Path, logo_path, cancel_token: CancelToken):
        super().__init__()
        self.articles, self.out_dir = articles, out_dir
        self.logo_path, self.cancel_token = logo_path, cancel_token

    def run(self) -> None:
        from news_story.daily_top import compose_daily_top, prepare_item

        try:
            items = []
            for i, article in enumerate(self.articles, 1):
                if self.cancel_token.is_cancelled:
                    return
                self.progress.emit(f"Lecture des articles… {i}/{len(self.articles)}")
                items.append(prepare_item(article))
            if not any(item.image_path for item in items):
                self.failed.emit("Aucune image n'a pu être récupérée pour ces news "
                                 "(connexion internet ?).")
                return
            compose_daily_top(
                items, self.out_dir, logo_path=Path(self.logo_path) if self.logo_path else None,
                cta=_cinema_cta(),
                on_progress=lambda i, n: self.progress.emit(f"Création des images… {i}/{n}"))
            caption = (self.out_dir / "legende.txt").read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
        else:
            errors = [item.ai_error for item in items if item.ai_error]
            note = f"⚠ IA Claude : {errors[0]} Texte tiré des articles à la place." if errors else ""
            self.ready.emit(str(self.out_dir), caption, note)


def _cinema_cta() -> str:
    """La phrase sous le logo, telle que reglee dans « Créer un visuel » pour
    le compte cinema (sinon celle par defaut, avec le clap)."""
    from news_story.video_composer import DEFAULT_CTA

    value = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("cta/cinema", None)
    return str(value) if value is not None else DEFAULT_CTA["cinema"]


def _cinema_logo() -> str | None:
    value = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("logo/cinema", "")
    if value and Path(value).is_file():
        return str(value)
    from core.paths import app_base_dir

    path = app_base_dir() / "assets" / "branding" / "logo-cinema.png"
    return str(path) if path.is_file() else None


class DailyTopDialog(QDialog):
    """Liste des news cinema (unes des sites en tete), les premieres cochees."""

    def __init__(self, articles, parent=None, preselect: int = 5):
        super().__init__(parent)
        self.setWindowTitle("Top news ciné du jour")
        self.setMinimumSize(640, 520)
        self._articles = list(articles)
        self._thread: _TopThread | None = None
        self._cancel_token = CancelToken()
        self._caption = ""

        layout = QVBoxLayout(self)
        intro = QLabel("Coche les news à mettre dans le post du jour (10 au maximum). "
                       "L'export crée une couverture « TOP NEWS CINÉ » puis une image 9:16 par "
                       "news, et la légende, dans un seul dossier : à publier en carrousel "
                       "TikTok (mode photo) ou Instagram.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list = QListWidget()
        for i, article in enumerate(self._articles):
            badge = "⭐ " if getattr(article, "rank", None) is not None else ""
            item = QListWidgetItem(f"{badge}{article.title}  —  {article.source_label}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if i < preselect else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._update_count)
        layout.addWidget(self.list, stretch=1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.export_btn = QPushButton("💾 Créer le top du jour")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.export_btn)
        self.copy_btn = QPushButton("📋 Copier la légende")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_caption)
        buttons.addWidget(self.copy_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self._update_count()

    def selected_articles(self) -> list:
        return [self._articles[i] for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _update_count(self, *_):
        count = len(self.selected_articles())
        self.export_btn.setEnabled(0 < count <= MAX_ITEMS and self._thread is None)
        if count > MAX_ITEMS:
            self.status_label.setText(f"{count} news cochées : {MAX_ITEMS} au maximum.")
        else:
            self.status_label.setText(f"{count} news cochée(s).")

    def _export(self) -> None:
        articles = self.selected_articles()
        if not articles:
            return
        from news_story.daily_top import folder_name

        parent = QFileDialog.getExistingDirectory(self, "Dossier où créer le top du jour")
        if not parent:
            return
        out_dir = Path(parent) / folder_name(date.today())
        self.export_btn.setEnabled(False)
        self._thread = _TopThread(articles, out_dir, _cinema_logo(), self._cancel_token)
        self._thread.progress.connect(self.status_label.setText)
        self._thread.ready.connect(self._on_ready)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_ready(self, folder: str, caption: str, note: str = "") -> None:
        self._thread = None
        self._caption = caption
        self.copy_btn.setEnabled(True)
        self._update_count()
        self.status_label.setText(f"Top du jour créé : {folder}" + (f"\n{note}" if note else ""))
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_failed(self, message: str) -> None:
        self._thread = None
        self._update_count()
        QMessageBox.warning(self, "Top du jour impossible", message)

    def _copy_caption(self) -> None:
        QGuiApplication.clipboard().setText(self._caption)
        self.status_label.setText("Légende copiée dans le presse-papiers.")

    def cleanup(self) -> None:
        self._cancel_token.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(5000)

    def reject(self) -> None:
        self.cleanup()
        super().reject()
