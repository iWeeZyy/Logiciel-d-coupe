"""Fenetres « Sorties de la semaine » et « Box-office » : la liste AlloCine
est recuperee, l'utilisateur coche les films, l'export cree le carrousel
(couverture + une fiche par film + legende) dans un dossier
(news_story/film_carousels.py). Meme deroule que le Top news cine du jour.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
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
from gui.radar.daily_top_dialog import _cinema_cta, _cinema_logo

RELEASES, BOX_OFFICE, CRITICS, STREAMING = "releases", "box_office", "critics", "streaming"
_TITLES = {RELEASES: "Sorties ciné de la semaine", BOX_OFFICE: "Box-office France",
           CRITICS: "Presse vs public", STREAMING: "Nouveautés streaming de la semaine"}
_PRESELECT = {RELEASES: 6, BOX_OFFICE: 5, CRITICS: 5, STREAMING: 6}
_INTROS = {
    RELEASES: "Les films qui sortent en salle cette semaine (AlloCiné). ",
    BOX_OFFICE: "Le classement des entrées de la semaine en France (AlloCiné). ",
    CRITICS: "Les sorties des 4 dernières semaines notées par la presse ET les spectateurs "
             "(AlloCiné), du plus grand écart au plus petit. ",
    STREAMING: "Les nouveautés de la semaine des plateformes dont AlloCiné publie l'agenda "
               "(Netflix aujourd'hui ; les autres apparaissent dès qu'AlloCiné les publie). ",
}
MAX_ITEMS = 10


class _LoadThread(QThread):
    loaded = Signal(object, str)        # elements, libelle de semaine
    failed = Signal(str)

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind

    def run(self) -> None:
        from news_story import cinema_lists

        from news_story import film_carousels as fc

        try:
            week = ""
            if self.kind == RELEASES:
                items = cinema_lists.fetch_releases()
            elif self.kind == CRITICS:
                items = fc.critics_films(cinema_lists.fetch_recent_releases(weeks=4))
            elif self.kind == STREAMING:
                items = cinema_lists.fetch_streaming()
            else:
                week, items = cinema_lists.fetch_box_office()
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
            return
        if not items:
            self.failed.emit("La page AlloCiné n'a rien donné (connexion internet ? page modifiée ? "
                             "agenda de la semaine pas encore publié ?).")
            return
        self.loaded.emit(items, week)


class _ExportThread(QThread):
    progress = Signal(str)
    ready = Signal(str, str)            # dossier, legende
    failed = Signal(str)

    def __init__(self, kind: str, items: list, week: str, out_dir: Path, cancel_token: CancelToken):
        super().__init__()
        self.kind, self.items, self.week = kind, items, week
        self.out_dir, self.cancel_token = out_dir, cancel_token

    def run(self) -> None:
        from news_story import film_carousels as fc

        try:
            images = []
            for i, item in enumerate(self.items, 1):
                if self.cancel_token.is_cancelled:
                    return
                self.progress.emit(f"Téléchargement des affiches… {i}/{len(self.items)}")
                if self.kind == STREAMING:
                    from news_story.cinema_lists import fetch_poster

                    images.append(fc.download_image(fetch_poster(item.url)))
                    continue
                film = item.film if self.kind == BOX_OFFICE else item
                images.append(fc.download_image(film.poster_url))
            logo = _cinema_logo()
            common = dict(day=date.today(), logo_path=Path(logo) if logo else None, cta=_cinema_cta(),
                          on_progress=lambda i, n: self.progress.emit(f"Création des images… {i}/{n}"))
            if self.kind == RELEASES:
                fc.compose_releases(self.items, images, self.out_dir, **common)
            elif self.kind == CRITICS:
                fc.compose_critics(self.items, images, self.out_dir, **common)
            elif self.kind == STREAMING:
                fc.compose_streaming(self.items, images, self.out_dir, **common)
            else:
                fc.compose_box_office(self.items, images, self.out_dir, week_label=self.week, **common)
            caption = (self.out_dir / "legende.txt").read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
        else:
            self.ready.emit(str(self.out_dir), caption)


def _release_line(film) -> str:
    detail = " · ".join(p for p in (", ".join(film.genres[:2]), film.duration) if p)
    return f"{film.title}  —  {detail}" if detail else film.title


def _box_office_line(entry) -> str:
    return f"N°{entry.rank}  {entry.film.title}  —  {entry.entries} entrées"


def _critics_line(film) -> str:
    from news_story.cinema_lists import rating_gap

    gap = rating_gap(film) or 0.0
    return (f"{film.title}  —  presse {film.press_rating} · spectateurs {film.spectator_rating}"
            f"  (écart {abs(gap):.1f})".replace(".", ","))


def _streaming_line(item) -> str:
    return f"{item.platform} · {item.day}  —  {item.title}" + (f" ({item.kind})" if item.kind else "")


_LINES = {RELEASES: _release_line, BOX_OFFICE: _box_office_line, CRITICS: _critics_line,
          STREAMING: _streaming_line}


class FilmCarouselDialog(QDialog):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(_TITLES[kind])
        self.setMinimumSize(640, 520)
        self._items: list = []
        self._week = ""
        self._caption = ""
        self._thread: QThread | None = None
        self._cancel_token = CancelToken()

        layout = QVBoxLayout(self)
        intro = QLabel(
            _INTROS[kind]
            + "Coche ceux à mettre dans le carrousel (10 au maximum) : l'export crée une "
            "couverture, une fiche 9:16 par titre et la légende, dans un seul dossier.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._update_count)
        layout.addWidget(self.list, stretch=1)
        self.status_label = QLabel("Récupération de la liste AlloCiné…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.export_btn = QPushButton("💾 Créer le carrousel")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setEnabled(False)
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

        self._start(_LoadThread(kind), loaded=self._on_loaded, failed=self._on_failed)

    # Une seule tache de fond a la fois ; liberee seulement une fois VRAIMENT
    # terminee (un QThread detruit encore actif arrete toute l'application).
    def _start(self, thread: QThread, **slots) -> None:
        self._thread = thread
        for name, slot in slots.items():
            getattr(thread, name).connect(slot)
        thread.start()

    def _release_thread(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.wait()

    def _on_loaded(self, items: list, week: str) -> None:
        self._release_thread()
        self._items, self._week = list(items), week
        line = _LINES[self.kind]
        self.list.blockSignals(True)
        for i, item in enumerate(self._items):
            row = QListWidgetItem(line(item))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if i < _PRESELECT[self.kind]
                              else Qt.CheckState.Unchecked)
            self.list.addItem(row)
        self.list.blockSignals(False)
        self._update_count()

    def _on_failed(self, message: str) -> None:
        self._release_thread()
        self._update_count()
        QMessageBox.warning(self, _TITLES[self.kind], message)

    def selected(self) -> list:
        return [self._items[i] for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _update_count(self, *_) -> None:
        count = len(self.selected())
        self.export_btn.setEnabled(0 < count <= MAX_ITEMS and self._thread is None)
        if not self._items:
            return
        prefix = f"Box-office de la {self._week}. " if self._week else ""
        self.status_label.setText(prefix + (f"{count} titres cochés : {MAX_ITEMS} au maximum."
                                            if count > MAX_ITEMS else f"{count} titre(s) coché(s)."))

    def _export(self) -> None:
        from news_story import film_carousels as fc

        items = self.selected()
        if not items:
            return
        parent = QFileDialog.getExistingDirectory(self, "Dossier où créer le carrousel")
        if not parent:
            return
        name = {RELEASES: fc.releases_folder_name, BOX_OFFICE: fc.box_office_folder_name,
                CRITICS: fc.critics_folder_name,
                STREAMING: fc.streaming_folder_name}[self.kind](date.today())
        self.export_btn.setEnabled(False)
        self._start(_ExportThread(self.kind, items, self._week, Path(parent) / name,
                                  self._cancel_token),
                    progress=self.status_label.setText, ready=self._on_ready,
                    failed=self._on_failed)

    def _on_ready(self, folder: str, caption: str) -> None:
        self._release_thread()
        self._caption = caption
        self.copy_btn.setEnabled(True)
        self._update_count()
        self.status_label.setText(f"Carrousel créé : {folder}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_caption(self) -> None:
        QGuiApplication.clipboard().setText(self._caption)
        self.status_label.setText("Légende copiée dans le presse-papiers.")

    def cleanup(self) -> None:
        self._cancel_token.cancel()
        # Attente complete (requetes bornees par leur delai) : fermer la fenetre
        # sur une tache encore active arreterait toute l'application.
        if self._thread is not None:
            self._thread.wait()

    def reject(self) -> None:
        self.cleanup()
        super().reject()
