"""Dialogue "Créer un visuel" : transforme une actualité selectionnee en
visuel pret a publier (news_story/) -- Story 9:16, ou post de fil 4:5 avec sa
legende a copier (gabarit POST, format des comptes d'actualite cinema).

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

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gaming_news.models import Article
from news_story.image_cache import ImageFetchError, download as cache_download
from news_story.image_fetcher import candidates_for_article
from news_story.video_source import fetch_video_url
from news_story.story_composer import StoryOptions, compose_story, default_subtitle
from news_story.caption import build_caption
from news_story.story_templates import (
    TEMPLATE_BREAKING,
    TEMPLATE_IMAGE,
    TEMPLATE_NEWS,
    LABELLED_TEMPLATES,
    POST_TEMPLATES,
    TEMPLATE_POST,
    TEMPLATE_POST_VERTICAL,
    TEMPLATE_VIDEO,
    TEMPLATES,
    get_template,
)
from news_story.title_shortener import build_display_title, is_rumor

RIGHTS_NOTICE = ("Image provenant de l'article source. Vérifiez les droits de "
                 "réutilisation avant publication.")

_TEMPLATE_DISPLAY_LABELS = {
    TEMPLATE_IMAGE: "Image seule",
    TEMPLATE_NEWS: "Actualité (titre + source)",
    TEMPLATE_BREAKING: "Alerte BREAKING",
    TEMPLATE_POST: "Post du fil 4:5 (titre + légende)",
    TEMPLATE_POST_VERTICAL: "Post 9:16 TikTok / Reels / Story (titre + légende)",
    TEMPLATE_VIDEO: "🎬 Vidéo 9:16 de l'article (texte sur la vidéo)",
}
# Etiquettes proposees au-dessus du titre du post ; la liste est editable.
_POST_LABELS = ("ACTUALITÉ", "BANDE-ANNONCE", "EXCLU", "RUMEUR", "CASTING", "BOX-OFFICE", "ANECDOTE")
_SETTINGS_ORG, _SETTINGS_APP = "ClipFarming", "NewsVisuals"
# Logo livre avec l'appli pour un fil, utilise tant que l'utilisateur n'en a
# pas choisi un autre pour ce fil (bouton « Choisir le logo… »).
_THEME_DEFAULT_LOGOS = {"cinema": "branding/logo-cinema.png", "trailers": "branding/logo-cinema.png"}
_POSITION_LABELS = {"auto": "Automatique", "top": "Haut", "center": "Centre", "bottom": "Bas"}
_SIZE_LABELS = {0.8: "Petit", 1.0: "Normal", 1.3: "Grand"}

_MAX_CANDIDATES = 4
# 4:5 retire du menu (« que du 9:16 pour TikTok et Instagram ») ; le modele
# video n'y entre que quand l'article a une video.
_MENU_HIDDEN_TEMPLATES = frozenset({TEMPLATE_POST, TEMPLATE_VIDEO})
_PREVIEW_DEBOUNCE_MS = 250
_PREVIEW_DISPLAY_SIZE = QSize(270, 480)
_PREVIEW_POST_SIZE = QSize(384, 480)


class _FetchImagesThread(QThread):
    """Recupere les images candidates de l'article (og:image/twitter:image/
    contenu/flux, voir news_story.image_fetcher) puis met en cache celles qui
    reussissent -- hors du fil de l'interface, le tout est reseau."""

    candidate_ready = Signal(object, object)  # ImageCandidate, CachedImage
    candidate_failed = Signal(str, str)       # url, message
    finished_all = Signal(int)                # nombre d'images recuperees avec succes
    video_found = Signal(str)                 # adresse de la video de l'article

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
        if self.cancel_token.is_cancelled:
            return
        video_url = fetch_video_url(self.article.url)
        if video_url:
            self.video_found.emit(video_url)


class _VideoExportThread(QThread):
    """Telecharge la video de l'article (yt-dlp, comme le reste de l'appli)
    puis monte le post video 9:16 (news_story/video_composer.py)."""

    status = Signal(str)
    ready = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, video_url: str, out_path: str, title: str, label: str,
                 logo_path, text_opacity: float, title_scale: float, cancel_token: CancelToken,
                 subtitle: str = ""):
        super().__init__()
        self.subtitle = subtitle
        self.video_url, self.out_path = video_url, out_path
        self.title, self.label, self.logo_path = title, label, logo_path
        self.text_opacity, self.title_scale = text_opacity, title_scale
        self.cancel_token = cancel_token

    def run(self) -> None:
        from news_story.video_composer import compose_video
        from utils.errors import CancelledError
        from youtube.downloader import download_video

        try:
            with tempfile.TemporaryDirectory(prefix="clipfarming_article_video_") as workdir:
                self.status.emit("Téléchargement de la vidéo…")

                def progress(fraction, done_mb, total_mb):
                    if fraction is not None:
                        self.status.emit(f"Téléchargement de la vidéo… {int(fraction * 100)} %")

                source = download_video(self.video_url, workdir, consent_confirmed=True,
                                        max_height=1080, on_progress=progress,
                                        cancel_token=self.cancel_token)
                self.status.emit("Montage de la vidéo 9:16… (cela peut prendre une minute)")
                compose_video(source, self.out_path, title=self.title, label=self.label,
                              logo_path=Path(self.logo_path) if self.logo_path else None,
                              text_opacity=self.text_opacity, title_scale=self.title_scale,
                              subtitle=self.subtitle, cancel_token=self.cancel_token)
        except CancelledError:
            self.cancelled.emit()
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
        else:
            self.ready.emit(str(self.out_path))


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


class _PreviewLabel(QLabel):
    """Apercu qui se reduit avec la fenetre au lieu d'imposer sa hauteur :
    a hauteur fixe (480 px), il debordait sur les boutons du bas sur un
    ecran peu haut. L'image est re-mise a l'echelle a chaque redimension."""

    _MIN_HEIGHT = 220

    def __init__(self, text: str):
        super().__init__(text)
        self._source: QPixmap | None = None
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored)

    def set_frame_size(self, size: QSize) -> None:
        self.setFixedWidth(size.width())
        self.setMinimumHeight(self._MIN_HEIGHT)
        self.setMaximumHeight(size.height())
        self._rescale()

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._rescale()

    def resizeEvent(self, event) -> None:  # noqa: N802 -- API Qt
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source is not None and not self._source.isNull():
            self.setPixmap(self._source.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))


class StoryDialog(QDialog):
    """Transforme `article` en Story verticale prete a exporter."""

    def __init__(self, article: Article, parent=None, theme: str = "gaming"):
        super().__init__(parent)
        self.setWindowTitle("Créer un visuel")
        # Hauteur minimale modeste : sur un ecran portable a 125-150 % le
        # panneau d'options ne tenait pas, et Qt empilait les widgets les uns
        # sur les autres (bouton « Copier la légende » par-dessus la legende).
        # Le panneau defile desormais (voir options_scroll).
        self.setMinimumSize(820, 520)

        self.article = article
        self.theme = theme
        self._logo_path = self._saved_logo_path()
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

        self.video_label = QLabel("")
        self.video_label.setWordWrap(True)
        self.video_label.setProperty("role", "muted")
        self.video_label.setVisible(False)
        outer.addWidget(self.video_label)

        body = QHBoxLayout()
        body.setSpacing(20)

        self.preview_label = _PreviewLabel("Aperçu indisponible")
        self.preview_label.set_frame_size(_PREVIEW_DISPLAY_SIZE)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid #E3E5EA; background: #16181D; color: white;")
        body.addWidget(self.preview_label)

        options_widget = QWidget()
        options_panel = QVBoxLayout(options_widget)
        options_panel.setContentsMargins(0, 0, 8, 0)
        options_panel.setSpacing(10)

        options_panel.addWidget(QLabel("Modèle"))
        self.template_combo = QComboBox()
        # Pas de 4:5 (« que du 9:16 pour TikTok et Instagram ») ; le modele
        # video n'apparait que si l'article a une video (voir _on_video_found).
        for spec in TEMPLATES:
            if spec.key in _MENU_HIDDEN_TEMPLATES:
                continue
            self.template_combo.addItem(_TEMPLATE_DISPLAY_LABELS.get(spec.key, spec.label), spec.key)
        # Les fils cinema / bandes-annonces alimentent un compte de posts 9:16 :
        # ce modele y est propose d'emblee ; le fil gaming garde la Story.
        self._video_url = ""
        default_template = (TEMPLATE_POST_VERTICAL if theme in ("cinema", "trailers")
                            else TEMPLATE_NEWS)
        self.template_combo.setCurrentIndex(self.template_combo.findData(default_template))
        self._template_touched = False
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        self.template_combo.activated.connect(self._on_template_chosen_by_user)
        options_panel.addWidget(self.template_combo)

        self.opacity_caption = QLabel("Opacité du texte")
        options_panel.addWidget(self.opacity_caption)
        self.opacity_combo = QComboBox()
        for value in (1.0, 0.8, 0.6, 0.45, 0.3):
            self.opacity_combo.addItem(f"{int(value * 100)} %", value)
        self.opacity_combo.setCurrentIndex(self.opacity_combo.findData(1.0))
        self.opacity_combo.currentIndexChanged.connect(self._schedule_preview)
        options_panel.addWidget(self.opacity_combo)

        self.label_caption = QLabel("Étiquette")
        options_panel.addWidget(self.label_caption)
        self.label_combo = QComboBox()
        self.label_combo.setEditable(True)
        self.label_combo.addItems(_POST_LABELS)
        if theme == "trailers":
            self.label_combo.setCurrentText("BANDE-ANNONCE")
        elif is_rumor(article.title, article.summary):
            self.label_combo.setCurrentText("RUMEUR")
        self.label_combo.currentTextChanged.connect(self._schedule_preview)
        options_panel.addWidget(self.label_combo)

        options_panel.addWidget(QLabel("Titre"))
        self.title_edit = QLineEdit()
        self.title_edit.textEdited.connect(self._schedule_preview)
        options_panel.addWidget(self.title_edit)

        # Le chapo porte souvent l'info que le titre tait (« cet acteur » ->
        # Jeremy Allen White) : affiche sous le titre, pre-rempli, retouchable.
        self.subtitle_caption = QLabel("Info sous le titre (chapô de l'article)")
        options_panel.addWidget(self.subtitle_caption)
        self.subtitle_edit = QPlainTextEdit(default_subtitle(article.title, article.summary))
        self.subtitle_edit.setMinimumHeight(70)
        self.subtitle_edit.setMaximumHeight(110)
        self.subtitle_edit.textChanged.connect(self._schedule_preview)
        options_panel.addWidget(self.subtitle_edit)

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

        logo_row = QHBoxLayout()
        self.branding_check = QCheckBox("Ajouter le logo")
        self.branding_check.setChecked(True)
        self.branding_check.toggled.connect(self._schedule_preview)
        logo_row.addWidget(self.branding_check)
        self.logo_btn = QPushButton("Choisir le logo…")
        self.logo_btn.setToolTip("Logo propre à ce fil (gaming, cinéma…), mémorisé pour les prochaines fois.")
        self.logo_btn.clicked.connect(self._choose_logo)
        logo_row.addWidget(self.logo_btn)
        logo_row.addStretch(1)
        options_panel.addLayout(logo_row)
        self.logo_label = QLabel(self._logo_description())
        self.logo_label.setProperty("role", "muted")
        options_panel.addWidget(self.logo_label)

        # Legende du post : titre + resume + source, tels que fournis par le
        # flux (news_story/caption.py), retouchables avant de copier.
        options_panel.addWidget(QLabel("Légende (description du post)"))
        self.caption_edit = QPlainTextEdit(build_caption(
            article.title, article.summary, article.source_label, theme))
        self.caption_edit.setMinimumHeight(110)
        options_panel.addWidget(self.caption_edit)

        options_panel.addStretch(1)
        self.options_scroll = QScrollArea()
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.options_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.options_scroll.setWidget(options_widget)
        body.addWidget(self.options_scroll, stretch=1)
        outer.addLayout(body, stretch=1)

        buttons = QHBoxLayout()
        self.export_btn = QPushButton("💾 Exporter")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.export_btn)
        # Dans la barre du bas, toujours visible, plutot que sous la legende
        # ou il pouvait sortir de l'ecran.
        self.copy_caption_btn = QPushButton("📋 Copier la légende")
        self.copy_caption_btn.clicked.connect(self._copy_caption)
        buttons.addWidget(self.copy_caption_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        outer.addLayout(buttons)

        self._fit_to_screen()
        self._on_template_changed()  # pose le titre initial avant tout fetch
        self._start_fetch()

    # ------------------------------------------------------------ fetch
    def _start_fetch(self) -> None:
        self._fetch_thread = _FetchImagesThread(self.article, self._cancel_token)
        self._fetch_thread.candidate_ready.connect(self._on_candidate_ready)
        self._fetch_thread.candidate_failed.connect(self._on_candidate_failed)
        self._fetch_thread.finished_all.connect(self._on_fetch_finished)
        self._fetch_thread.video_found.connect(self._on_video_found)
        self._fetch_thread.start()

    def _on_template_chosen_by_user(self, _index: int) -> None:
        self._template_touched = True

    def _on_video_found(self, url: str) -> None:
        """L'article a une video : le modele video est ajoute au menu, et
        choisi d'office sauf si l'utilisateur a deja choisi un modele."""
        self._video_url = url
        if self.template_combo.findData(TEMPLATE_VIDEO) < 0:
            self.template_combo.addItem(_TEMPLATE_DISPLAY_LABELS[TEMPLATE_VIDEO], TEMPLATE_VIDEO)
        if not self._template_touched:
            self.template_combo.setCurrentIndex(self.template_combo.findData(TEMPLATE_VIDEO))
        self.video_label.setText("🎬 Vidéo trouvée dans l'article : le post sera la vidéo, le texte "
                                 "au-dessus (l'aperçu utilise l'image, la vidéo est téléchargée "
                                 "à l'export).")
        self.video_label.setVisible(True)
        # Exportable meme si aucune image n'a pu etre recuperee pour l'apercu.
        self.export_btn.setEnabled(True)

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

    def _fit_to_screen(self) -> None:
        """Taille initiale bornee a l'ecran disponible (barre des taches
        comprise) : la fenetre ne deborde jamais, le panneau d'options defile."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        hint = self.sizeHint()
        self.resize(max(self.minimumWidth(), min(max(hint.width(), 980), avail.width() - 40)),
                    max(self.minimumHeight(), min(max(hint.height(), 760), avail.height() - 60)))

    # --------------------------------------------------------- edition
    def _on_template_changed(self) -> None:
        template = get_template(self.template_combo.currentData())
        is_post = template.key in LABELLED_TEMPLATES
        is_video = template.key == TEMPLATE_VIDEO
        self.title_edit.setEnabled(template.show_title)
        self.label_caption.setVisible(is_post)
        self.label_combo.setVisible(is_post)
        self.opacity_caption.setVisible(is_video)
        self.opacity_combo.setVisible(is_video)
        self.subtitle_caption.setVisible(is_post)
        self.subtitle_edit.setVisible(is_post)
        # Le post a une mise en page fixe (titre en bas) : pas de position.
        self.position_combo.setEnabled(not is_post)
        self.preview_label.set_frame_size(
            _PREVIEW_POST_SIZE if template.key == TEMPLATE_POST else _PREVIEW_DISPLAY_SIZE)
        if template.show_title:
            # Ne remplace le titre que s'il vaut encore la valeur auto-generee
            # precedente -- une modification manuelle de l'utilisateur ne doit
            # jamais etre effacee par un simple changement de modele.
            if self.title_edit.text() in ("", self._auto_title):
                summary = self.article.summary if template.title_uses_summary else ""
                self._auto_title = build_display_title(
                    self.article.title, summary, template.title_max_chars).text
                self.title_edit.setText(self._auto_title)
        self._schedule_preview()

    # ------------------------------------------------------------ logo
    @property
    def _logo_theme(self) -> str:
        # Les bandes-annonces alimentent le meme compte que le fil cinema :
        # un logo choisi pour l'un vaut pour l'autre.
        return "cinema" if self.theme == "trailers" else self.theme

    def _settings(self) -> QSettings:
        return QSettings(_SETTINGS_ORG, _SETTINGS_APP)

    def _saved_logo_path(self) -> str | None:
        value = self._settings().value(f"logo/{self._logo_theme}", "")
        if value and Path(value).is_file():
            return value
        default = _THEME_DEFAULT_LOGOS.get(self.theme)
        if default:
            from core.paths import app_base_dir

            path = app_base_dir() / "assets" / default
            if path.is_file():
                return str(path)
        return None

    def _logo_description(self) -> str:
        return f"Logo : {Path(self._logo_path).name}" if self._logo_path else "Logo : celui de l'application"

    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir le logo", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        self._logo_path = path
        self._settings().setValue(f"logo/{self._logo_theme}", path)
        self.logo_label.setText(self._logo_description())
        self._schedule_preview()

    def _copy_caption(self) -> None:
        QGuiApplication.clipboard().setText(self.caption_edit.toPlainText())
        self.status_label.setText("Légende copiée dans le presse-papiers.")

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
            label=self.label_combo.currentText(),
            branding_path=self._logo_path,
            text_opacity=self.opacity_combo.currentData() or 1.0,
            subtitle=self.subtitle_edit.toPlainText(),
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
            self.preview_label.set_source(pixmap)
        self.export_btn.setEnabled(True)
        if self._compose_pending:
            self._render_preview()

    def _on_preview_failed(self, message: str) -> None:
        self.status_label.setText(f"Aperçu impossible : {message}")
        if self._compose_pending:
            self._render_preview()

    # ------------------------------------------------------------ export
    def _export(self) -> None:
        if self.template_combo.currentData() == TEMPLATE_VIDEO and self._video_url:
            self._export_video()
            return
        if self._selected_index is None:
            return
        prefix = "post" if self.template_combo.currentData() in POST_TEMPLATES else "story"
        default_name = f"{prefix}_{self.article.article_id}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le visuel", default_name,
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

    def _export_video(self) -> None:
        from news_story.story_composer import post_subtitle, post_text

        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter la vidéo", f"video_{self.article.article_id}.mp4", "Vidéo MP4 (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        options = self._current_options()
        title, label = post_text(options, get_template(TEMPLATE_VIDEO))
        logo = self._logo_path if self.branding_check.isChecked() else None
        if self.branding_check.isChecked() and not logo:
            from news_story.story_composer import _branding_path
            logo = str(_branding_path(options))
        self.export_btn.setEnabled(False)
        self._export_thread = _VideoExportThread(
            self._video_url, path, title, label, logo, options.text_opacity,
            options.title_scale, self._cancel_token, subtitle=post_subtitle(options))
        self._export_thread.status.connect(self.status_label.setText)
        self._export_thread.ready.connect(self._on_export_ready)
        self._export_thread.failed.connect(self._on_export_failed)
        self._export_thread.cancelled.connect(lambda: self.export_btn.setEnabled(True))
        self._export_thread.start()

    def _on_export_ready(self, path: str) -> None:
        self.export_btn.setEnabled(True)
        self.status_label.setText("")
        QMessageBox.information(self, "Visuel exporté", f"Visuel enregistré :\n{path}")

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
