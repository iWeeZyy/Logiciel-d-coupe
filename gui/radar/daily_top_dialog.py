"""Fenetre « Top news ciné du jour » : choisir plusieurs news cinema et les
exporter d'un coup -- une couverture + une image 9:16 par news + la legende,
dans un seul dossier, prets pour un carrousel TikTok / Instagram
(news_story/daily_top.py)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTime, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
)

from core.cancellation import CancelToken

_SETTINGS_ORG, _SETTINGS_APP = "ClipFarming", "NewsVisuals"
MAX_ITEMS = 10          # au-dela, un carrousel se lit mal


class _TopThread(QThread):
    progress = Signal(str)
    ready = Signal(str, str, str)  # dossier, legende, avertissement IA
    failed = Signal(str)

    def __init__(self, articles, out_dir: Path, logo_path, cancel_token: CancelToken,
                 voice_id: str | None = None):
        super().__init__()
        self.articles, self.out_dir = articles, out_dir
        self.logo_path, self.cancel_token = logo_path, cancel_token
        self.voice_id = voice_id          # None : pas de video narree

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
            written = compose_daily_top(
                items, self.out_dir, logo_path=Path(self.logo_path) if self.logo_path else None,
                cta=_cinema_cta(),
                on_progress=lambda i, n: self.progress.emit(f"Création des images… {i}/{n}"))
            if self.voice_id is not None:
                self._video(items, written)
            caption = (self.out_dir / "legende.txt").read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
        else:
            errors = [item.ai_error for item in items if item.ai_error]
            note = f"⚠ IA Claude : {errors[0]} Texte tiré des articles à la place." if errors else ""
            self.ready.emit(str(self.out_dir), caption, note)


    def _video(self, items, written) -> None:
        """La meme selection en video 9:16 avec voix off (top_video.py)."""
        from datetime import date

        from news_story import top_video

        usable = [item for item in items if item.image_path and Path(item.image_path).is_file()]
        shots = top_video.shots_for(written[0], written[1:], usable, date.today(), _cinema_cta())
        top_video.compose_top_video(
            shots, self.out_dir / "top_video.mp4", voice=top_video.pick_voice(self.voice_id),
            cancel_token=self.cancel_token,
            on_progress=lambda i, n: self.progress.emit(f"Vidéo avec voix off… plan {i}/{n}"))


def ask_export_folder(parent, title: str) -> str:
    """Choix du dossier d'export, ouvert sur le dernier dossier utilise (et
    memorise). Un dossier OneDrive / Google Drive synchronise avec le
    telephone y fait arriver les images toutes seules."""
    store = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    folder = QFileDialog.getExistingDirectory(parent, title, str(store.value("export/folder", "")))
    if folder:
        store.setValue("export/folder", folder)
    return folder


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

        from news_story import published

        self.list = QListWidget()
        done = published.load()
        checked = 0
        for article in self._articles:
            # Une news deja publiee est signalee et n'est plus pre-cochee
            # (demande de l'utilisateur : pas deux fois la meme news).
            already = published.badge(article.url, done)
            badge = "⭐ " if getattr(article, "rank", None) is not None else ""
            text = f"{badge}{article.title}  —  {article.source_label}"
            item = QListWidgetItem(f"{text}   ({already})" if already else text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check = not already and checked < preselect
            checked += check
            item.setCheckState(Qt.CheckState.Checked if check else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._update_count)
        layout.addWidget(self.list, stretch=1)

        # Video narree : les memes images, lues par une voix de synthese
        # (Voice Studio, local). Proposee seulement si une voix est installee.
        video_row = QHBoxLayout()
        self.video_check = QCheckBox("🎙 Aussi en vidéo avec voix off")
        self.voice_combo = QComboBox()
        try:
            from voice_studio.tts import available_voices

            voices = available_voices()
        except Exception:  # noqa: BLE001 -- pas de moteur de voix : option grisee
            voices = []
        for voice in voices:
            self.voice_combo.addItem(voice.display_label, voice.id)
        saved = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("top/voice", "")
        if saved and self.voice_combo.findData(saved) >= 0:
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(saved))
        self.video_check.setEnabled(bool(voices))
        self.voice_combo.setEnabled(bool(voices))
        self.video_check.setChecked(bool(voices) and
                                    QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(
                                        "top/video", False, type=bool))
        if not voices:
            self.video_check.setToolTip("Aucune voix de synthèse trouvée (voir Voice Studio).")
        video_row.addWidget(self.video_check)
        video_row.addWidget(self.voice_combo, stretch=1)
        layout.addLayout(video_row)

        # Top prepare automatiquement chaque matin (Planificateur de taches
        # de Windows, news_story/auto_top.py).
        from news_story import auto_top

        store = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        auto_row = QHBoxLayout()
        self.auto_check = QCheckBox("⏰ Préparer le Top automatiquement chaque matin à")
        self.auto_time = QTimeEdit(QTime.fromString(str(store.value("auto_top/time", "07:30")),
                                                    "HH:mm"))
        self.auto_time.setDisplayFormat("HH:mm")
        self.auto_folder_btn = QPushButton("📁 Dossier…")
        self.auto_folder_btn.clicked.connect(self._choose_auto_folder)
        self.auto_check.setChecked(store.value("auto_top/enabled", False, type=bool))
        if not auto_top.scheduling_available():
            for widget in (self.auto_check, self.auto_time, self.auto_folder_btn):
                widget.setEnabled(False)
            self.auto_check.setToolTip("Utilise le Planificateur de tâches de Windows.")
        self.auto_check.toggled.connect(self._on_auto_toggled)
        self.auto_time.timeChanged.connect(lambda *_: self._apply_auto())
        auto_row.addWidget(self.auto_check)
        auto_row.addWidget(self.auto_time)
        auto_row.addWidget(self.auto_folder_btn)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)
        self.auto_label = QLabel(self._auto_description())
        self.auto_label.setWordWrap(True)
        self.auto_label.setProperty("role", "muted")
        layout.addWidget(self.auto_label)

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

    # -------------------------------------------------- Top automatique
    def _auto_description(self) -> str:
        store = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        folder = store.value("auto_top/folder", "")
        if not store.value("auto_top/enabled", False, type=bool):
            return ("Le logiciel peut préparer le Top tout seul chaque matin (PC allumé), dans "
                    "un dossier — un dossier OneDrive ou Google Drive le fait arriver sur ton "
                    "téléphone.")
        return (f"Top préparé chaque jour à {store.value('auto_top/time', '07:30')} dans : "
                f"{folder}" + (" (+ vidéo voix off)" if store.value("auto_top/video", False,
                                                                     type=bool) else ""))

    def _choose_auto_folder(self) -> bool:
        folder = QFileDialog.getExistingDirectory(
            self, "Dossier du Top automatique",
            str(QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("auto_top/folder", "")))
        if folder:
            QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("auto_top/folder", folder)
            self._apply_auto()
        return bool(folder)

    def _on_auto_toggled(self, checked: bool) -> None:
        store = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        if checked and not store.value("auto_top/folder", "") and not self._choose_auto_folder():
            self.auto_check.blockSignals(True)
            self.auto_check.setChecked(False)
            self.auto_check.blockSignals(False)
            return
        self._apply_auto()

    def _apply_auto(self) -> None:
        """Cree, met a jour ou supprime la tache planifiee selon la case."""
        from news_story import auto_top

        store = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        at = self.auto_time.time().toString("HH:mm")
        store.setValue("auto_top/time", at)
        store.setValue("auto_top/video", self.video_check.isChecked())
        if self.video_check.isChecked():
            store.setValue("top/voice", self.voice_combo.currentData() or "")
        if self.auto_check.isChecked():
            ok, message = auto_top.install(at)
            if not ok:
                self.auto_check.blockSignals(True)
                self.auto_check.setChecked(False)
                self.auto_check.blockSignals(False)
                QMessageBox.warning(self, "Top automatique", message)
            store.setValue("auto_top/enabled", ok)
        else:
            auto_top.uninstall()
            store.setValue("auto_top/enabled", False)
        self.auto_label.setText(self._auto_description())

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

        parent = ask_export_folder(self, "Dossier où créer le top du jour")
        if not parent:
            return
        out_dir = Path(parent) / folder_name(date.today())
        self.export_btn.setEnabled(False)
        self._exported = articles
        voice_id = None
        if self.video_check.isChecked():
            voice_id = self.voice_combo.currentData() or ""
            QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("top/voice", voice_id)
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("top/video", self.video_check.isChecked())
        self._thread = _TopThread(articles, out_dir, _cinema_logo(), self._cancel_token, voice_id)
        self._thread.progress.connect(self.status_label.setText)
        self._thread.ready.connect(self._on_ready)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _release_thread(self) -> None:
        """Lache la tache de fond une fois VRAIMENT terminee. Le signal de fin
        part de run() juste avant qu'elle se termine : liberer l'objet a ce
        moment detruit un QThread encore actif, et Qt arrete toute
        l'application (« QThread: Destroyed while thread is still running »)."""
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.wait()

    def _on_ready(self, folder: str, caption: str, note: str = "") -> None:
        from news_story import published

        self._release_thread()
        published.mark([a.url for a in getattr(self, "_exported", [])])
        self._caption = caption
        self.copy_btn.setEnabled(True)
        self._update_count()
        self.status_label.setText(f"Top du jour créé : {folder}" + (f"\n{note}" if note else ""))
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_failed(self, message: str) -> None:
        self._release_thread()
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
