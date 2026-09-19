"""Dialogue "Créer une Story" : transforme une actualité gaming selectionnee en
visuel 9:16 pret a publier (news_story/).

Trois etapes dans une seule fenetre, meme convention que
gui/radar/analysis_dialog.py (ClipAnalysisDialog) : recuperation de l'image
(reseau, hors du fil de l'interface via _FetchImagesThread), edition/apercu
(gabarit, titre, source, marque -- recompose en tache de fond a chaque
changement via _ComposeThread, jamais sur le fil de l'interface), export.

La mention "Image provenant de l'article source. Vérifiez les droits de
réutilisation avant publication." est affichee UNIQUEMENT dans cette fenetre
(self.rights_label) -- jamais dans l'image composee par
news_story.story_composer.compose_story(), qui l'ignore totalement. C'est la
seule maniere de respecter a la fois "l'app ne doit jamais presenter une image
comme libre de droits" et "cette mention ne doit jamais apparaitre sur
l'export final".
"""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gaming_news.models import Article
from news_story.image_cache import ImageFetchError, download as cache_download
from news_story.image_fetcher import candidates_for_article
from news_story.story_composer import StoryOptions, compose_story
from news_story.story_templates import TEMPLATE_BREAKING, TEMPLATE_IMAGE, TEMPLATE_NEWS, TEMPLATES, get_template
from news_story.title_shortener import build_display_title

RIGHTS_NOTICE = ("Image provenant de l'article source. Vérifiez les droits de "
                 "réutilisation avant publication.")

_TEMPLATE_DISPLAY_LABELS = {
    TEMPLATE_IMAGE: "Image seule",
    TEMPLATE_NEWS: "Actualité (titre + source)",
    TEMPLATE_BREAKING: "Alerte BREAKING",
}
_POSITION_LABELS = {"auto": "Automatique", "top": "Haut", "center": "Centre", "bottom": "Bas"}
_SIZE_LABELS = {0.8: "Petit", 1.0: "Normal", 1.3: "Grand"}

_MAX_CANDIDATES = 4
_PREVIEW_DEBOUNCE_MS = 250
_PREVIEW_DISPLAY_SIZE = QSize(270, 480)


class _FetchImagesThread(QThread):
    """Recupere les images candidates de l'article (og:image/twitter:image/
    contenu/flux, voir news_story.image_fetcher) puis met en cache celles qui
    reussissent -- hors du fil de l'interface, le tout est reseau."""

    candidate_ready = Signal(object, object)  # ImageCandidate, CachedImage
    candidate_failed = Signal(str, str)       # url, message
    finished_all = Signal(int)                # nombre d'images recuperees avec succes

    def __init__(self, article: Article, cancel_token: CancelToken):
        super().__init__()
        self.article = article
        self.cancel_token = cancel_token

    def run(self) -> None:
        try:
            candidates = candidates_for_article(self.article.url, self.article.feed_image_url)
        except Exception as error:  # noqa: BLE001 -- filet de securite, ne devrait pas arriver
            self.candidate_failed.emit("", f"Erreur inattendue : {error}")
            self.finished_all.emit(0)
            return

        succeeded = 0
        for candidate in candidates[:_MAX_CANDIDATES]:
            if self.cancel_token.is_cancelled:
                break
            try:
                cached = cache_download(candidate.url)
            except ImageFetchError as error:
                self.candidate_failed.emit(candidate.url, str(error))
                continue
            self.candidate_ready.emit(candidate, cached)
            succeeded += 1
        self.finished_all.emit(succeeded)


class _ComposeThread(QThread):
    """Compose une Story hors du fil de l'interface -- utilisee aussi bien
    pour rafraichir l'apercu que pour l'export final, seul le chemin de
    sortie change."""

    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, image_path, out_path, options: StoryOptions):
        super().__init__()
        self.image_path = image_path
        self.out_path = out_path
        self.options = options

    def run(self) -> None:
        try:
            compose_story(self.image_path, self.out_path, self.options)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(f"Erreur inattendue : {error}")
        else:
            self.ready.emit(str(self.out_path))


class StoryDialog(QDialog):
    """Transforme `article` en Story verticale prete a exporter."""

    def __init__(self, article: Article, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Créer une Story")
        self.setMinimumSize(780, 640)

        self.article = article
        self._candidates: list[tuple] = []  # [(ImageCandidate, CachedImage), ...]
        self._thumbnail_buttons: list[QPushButton] = []
        self._selected_index: int | None = None
        self._cancel_token = CancelToken()
        self._fetch_thread: _FetchImagesThread | None = None
        self._compose_thread: _ComposeThread | None = None
        self._export_thread: _ComposeThread | None = None
        self._compose_pending = False
        self._auto_title = ""
        self._preview_path = Path(tempfile.gettempdir()) / f"clipfarming_story_preview_{id(self)}.png"

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)

        outer = QVBoxLayout(self)
        outer.setSpacing(12)

        header = QLabel("📰 " + article.title)
        header.setWordWrap(True)
        header.setStyleSheet("font-weight: 700; font-size: 15px;")
        outer.addWidget(header)

        self.rights_label = QLabel(RIGHTS_NOTICE)
        self.rights_label.setWordWrap(True)
        self.rights_label.setProperty("role", "muted")
        outer.addWidget(self.rights_label)

        self.status_label = QLabel("Récupération de l'image de l'article…")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        image_row_label = QLabel("Image :")
        image_row_label.setProperty("role", "muted")
        outer.addWidget(image_row_label)
        self.candidates_row = QHBoxLayout()
        self.candidates_row.setSpacing(8)
        self.candidates_row.addStretch(1)
        outer.addLayout(self.candidates_row)

        self.low_res_label = QLabel("⚠️ Image de résolution faible : la Story sera agrandie, "
                                    "elle peut paraître un peu floue.")
        self.low_res_label.setWordWrap(True)
        self.low_res_label.setProperty("role", "muted")
        self.low_res_label.setVisible(False)
        outer.addWidget(self.low_res_label)

        body = QHBoxLayout()
        body.setSpacing(20)

        self.preview_label = QLabel("Aperçu indisponible")
        self.preview_label.setFixedSize(_PREVIEW_DISPLAY_SIZE)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid #E3E5EA; background: #16181D; color: white;")
        body.addWidget(self.preview_label)

        options_panel = QVBoxLayout()
        options_panel.setSpacing(10)

        options_panel.addWidget(QLabel("Modèle"))
        self.template_combo = QComboBox()
        for spec in TEMPLATES:
            self.template_combo.addItem(_TEMPLATE_DISPLAY_LABELS.get(spec.key, spec.label), spec.key)
        self.template_combo.setCurrentIndex([t.key for t in TEMPLATES].index(TEMPLATE_NEWS))
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        options_panel.addWidget(self.template_combo)

        options_panel.addWidget(QLabel("Titre"))
        self.title_edit = QLineEdit()
        self.title_edit.textEdited.connect(self._schedule_preview)
        options_panel.addWidget(self.title_edit)

        options_panel.addWidget(QLabel("Source"))
        self.source_edit = QLineEdit(article.source_label)
        self.source_edit.textEdited.connect(self._schedule_preview)
        options_panel.addWidget(self.source_edit)

        options_panel.addWidget(QLabel("Position du titre"))
        self.position_combo = QComboBox()
        for key, label in _POSITION_LABELS.items():
            self.position_combo.addItem(label, key)
        self.position_combo.currentIndexChanged.connect(self._schedule_preview)
        options_panel.addWidget(self.position_combo)

        options_panel.addWidget(QLabel("Taille du titre"))
        self.size_combo = QComboBox()
        for key, label in _SIZE_LABELS.items():
            self.size_combo.addItem(label, key)
        self.size_combo.setCurrentIndex(list(_SIZE_LABELS).index(1.0))
        self.size_combo.currentIndexChanged.connect(self._schedule_preview)
        options_panel.addWidget(self.size_combo)

        self.branding_check = QCheckBox("Ajouter le logo ClipsOfStreams")
        self.branding_check.setChecked(True)
        self.branding_check.toggled.connect(self._schedule_preview)
        options_panel.addWidget(self.branding_check)

        options_panel.addStretch(1)
        body.addLayout(options_panel, stretch=1)
        outer.addLayout(body, stretch=1)

        buttons = QHBoxLayout()
        self.export_btn = QPushButton("💾 Exporter")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.export_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        outer.addLayout(buttons)

        self._on_template_changed()  # pose le titre initial avant tout fetch
        self._start_fetch()

    # ------------------------------------------------------------ fetch
    def _start_fetch(self) -> None:
        self._fetch_thread = _FetchImagesThread(self.article, self._cancel_token)
        self._fetch_thread.candidate_ready.connect(self._on_candidate_ready)
        self._fetch_thread.candidate_failed.connect(self._on_candidate_failed)
        self._fetch_thread.finished_all.connect(self._on_fetch_finished)
        self._fetch_thread.start()

    def _on_candidate_ready(self, candidate, cached) -> None:
        self._candidates.append((candidate, cached))
        self._add_thumbnail(candidate, cached)
        if self._selected_index is None:
            self._select_candidate(0)

    def _on_candidate_failed(self, url: str, message: str) -> None:
        # Une candidate en echec n'est jamais bloquante : voir _on_fetch_finished
        # pour le seul cas qui compte, "aucune image du tout".
        pass

    def _on_fetch_finished(self, succeeded: int) -> None:
        if succeeded == 0:
            self.status_label.setText(
                "Aucune image récupérable pour cet article. Vérifiez la connexion internet, "
                "ou lisez l'article directement pour voir s'il illustre son propos.")
        else:
            self.status_label.setText(f"{succeeded} image(s) proposée(s) — cliquez pour changer.")

    def _add_thumbnail(self, candidate, cached) -> None:
        index = len(self._candidates) - 1
        btn = QPushButton()
        pixmap = QPixmap(str(cached.path))
        if not pixmap.isNull():
            btn.setIcon(QIcon(pixmap))
            btn.setIconSize(QSize(86, 86))
        btn.setFixedSize(QSize(96, 96))
        btn.setToolTip(candidate.source)
        btn.clicked.connect(lambda _=False, i=index: self._select_candidate(i))
        self._thumbnail_buttons.append(btn)
        self.candidates_row.insertWidget(self.candidates_row.count() - 1, btn)

    def _select_candidate(self, index: int) -> None:
        self._selected_index = index
        for i, btn in enumerate(self._thumbnail_buttons):
            btn.setStyleSheet("border: 2px solid #E0A24C;" if i == index else "border: 1px solid #E3E5EA;")
        _, cached = self._candidates[index]
        self.low_res_label.setVisible(cached.is_low_resolution)
        self._schedule_preview()

    # --------------------------------------------------------- edition
    def _on_template_changed(self) -> None:
        template = get_template(self.template_combo.currentData())
        self.title_edit.setEnabled(template.show_title)
        if template.show_title:
            # Ne remplace le titre que s'il vaut encore la valeur auto-generee
            # precedente -- une modification manuelle de l'utilisateur ne doit
            # jamais etre effacee par un simple changement de modele.
            if self.title_edit.text() in ("", self._auto_title):
                self._auto_title = build_display_title(
                    self.article.title, self.article.summary, template.title_max_chars).text
                self.title_edit.setText(self._auto_title)
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    def _current_options(self) -> StoryOptions:
        return StoryOptions(
            template=self.template_combo.currentData(),
            title=self.article.title,
            summary=self.article.summary,
            source_label=self.article.source_label,
            branding_enabled=self.branding_check.isChecked(),
            title_override=self.title_edit.text(),
            source_override=self.source_edit.text(),
            title_position=self.position_combo.currentData(),
            title_scale=self.size_combo.currentData(),
            output_format="PNG",
        )

    # ------------------------------------------------------------ apercu
    def _render_preview(self) -> None:
        if self._selected_index is None:
            return
        if self._compose_thread is not None and self._compose_thread.isRunning():
            self._compose_pending = True
            return
        self._compose_pending = False
        _, cached = self._candidates[self._selected_index]
        self._compose_thread = _ComposeThread(cached.path, self._preview_path, self._current_options())
        self._compose_thread.ready.connect(self._on_preview_ready)
        self._compose_thread.failed.connect(self._on_preview_failed)
        self._compose_thread.start()

    def _on_preview_ready(self, path: str) -> None:
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.preview_label.setPixmap(pixmap.scaled(
                _PREVIEW_DISPLAY_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self.export_btn.setEnabled(True)
        if self._compose_pending:
            self._render_preview()

    def _on_preview_failed(self, message: str) -> None:
        self.status_label.setText(f"Aperçu impossible : {message}")
        if self._compose_pending:
            self._render_preview()

    # ------------------------------------------------------------ export
    def _export(self) -> None:
        if self._selected_index is None:
            return
        default_name = f"story_{self.article.article_id}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter la Story", default_name,
            "Image PNG (*.png);;Image JPEG (*.jpg *.jpeg)")
        if not path:
            return

        output_format = "JPEG" if path.lower().endswith((".jpg", ".jpeg")) else "PNG"
        options = replace(self._current_options(), output_format=output_format)
        _, cached = self._candidates[self._selected_index]

        self.export_btn.setEnabled(False)
        self.status_label.setText("Export en cours…")
        self._export_thread = _ComposeThread(cached.path, path, options)
        self._export_thread.ready.connect(self._on_export_ready)
        self._export_thread.failed.connect(self._on_export_failed)
        self._export_thread.start()

    def _on_export_ready(self, path: str) -> None:
        self.export_btn.setEnabled(True)
        self.status_label.setText("")
        QMessageBox.information(self, "Story exportée", f"Story enregistrée :\n{path}")

    def _on_export_failed(self, message: str) -> None:
        self.export_btn.setEnabled(True)
        QMessageBox.warning(self, "Export impossible", message)

    # ----------------------------------------------------------- fermeture
    def reject(self) -> None:
        self.cleanup()
        super().reject()

    def cleanup(self) -> None:
        """Meme convention que ClipAnalysisDialog.cleanup() : un QThread encore
        actif a la destruction de la fenetre fait planter Qt."""
        self._cancel_token.cancel()
        for attribute in ("_fetch_thread", "_compose_thread", "_export_thread"):
            thread = getattr(self, attribute, None)
            if thread is not None and thread.isRunning():
                thread.wait(3000)
        self._preview_path.unlink(missing_ok=True)
