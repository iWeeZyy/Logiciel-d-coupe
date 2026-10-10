"""Fenetre « Devine le film » : choisir un film (films cultes, box-office ou
sorties de la semaine, listes AlloCine), le logiciel prend une photo du film
sur sa page AlloCine et cree le carrousel : photo tres pixelisee, puis moins
avec un indice, puis la reponse (news_story/film_carousels.compose_guess).
"""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gui.radar.daily_top_dialog import _cinema_cta, _cinema_logo

SOURCES = (("best", "Films cultes (les mieux notés)"), ("box_office", "Box-office de la semaine"),
           ("releases", "Sorties de la semaine"))


class _LoadThread(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, source: str):
        super().__init__()
        self.source = source

    def run(self) -> None:
        from news_story import cinema_lists

        try:
            if self.source == "best":
                films = cinema_lists.fetch_best_films(pages=5)
            elif self.source == "box_office":
                films = [e.film for e in cinema_lists.fetch_box_office()[1]]
            else:
                films = cinema_lists.fetch_releases()
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))
            return
        if films:
            self.loaded.emit(films)
        else:
            self.failed.emit("La page AlloCiné n'a rien donné (connexion internet ? page modifiée ?).")


class _ExportThread(QThread):
    progress = Signal(str)
    ready = Signal(str, str)
    failed = Signal(str)

    def __init__(self, film, out_dir: Path):
        super().__init__()
        self.film, self.out_dir = film, out_dir

    def run(self) -> None:
        from dataclasses import replace

        from news_story import cinema_lists
        from news_story import film_carousels as fc

        try:
            self.progress.emit("Recherche d'une photo du film…")
            stills = cinema_lists.fetch_film_stills(self.film)
            still = fc.pick_still(stills, fc.download_image)
            if still is None:
                self.failed.emit("Aucune photo de scène utilisable pour ce film sur AlloCiné : "
                                 "choisis-en un autre.")
                return
            self.progress.emit("Création des images…")
            logo = _cinema_logo()
            film = replace(self.film, title=fc.clean_title_for_answer(self.film.title))
            fc.compose_guess(film, still, self.out_dir, logo_path=Path(logo) if logo else None,
                             cta=_cinema_cta())
            caption = (self.out_dir / "legende.txt").read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))
        else:
            self.ready.emit(str(self.out_dir), caption)


class GuessFilmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Devine le film")
        self.setMinimumSize(600, 520)
        self._films: list = []
        self._caption = ""
        self._thread: QThread | None = None

        layout = QVBoxLayout(self)
        intro = QLabel("Choisis un film : le carrousel montre une photo du film très "
                       "pixelisée, puis moins avec un indice (genre, durée, réalisateur), puis "
                       "la réponse. Les films cultes sont les plus faciles à deviner.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Films :"))
        self.source_combo = QComboBox()
        for key, label in SOURCES:
            self.source_combo.addItem(label, key)
        self.source_combo.currentIndexChanged.connect(self._load)
        row.addWidget(self.source_combo, stretch=1)
        self.random_btn = QPushButton("🎲 Au hasard")
        self.random_btn.clicked.connect(self._pick_random)
        row.addWidget(self.random_btn)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.list, stretch=1)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.export_btn = QPushButton("💾 Créer le carrousel")
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
        self._load()

    def _start(self, thread: QThread, **slots) -> None:
        self._thread = thread
        for name, slot in slots.items():
            getattr(thread, name).connect(slot)
        thread.start()
        self._update_buttons()

    def _release_thread(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.wait()

    def _update_buttons(self) -> None:
        idle = self._thread is None
        self.export_btn.setEnabled(idle and self.list.currentRow() >= 0)
        self.random_btn.setEnabled(idle and bool(self._films))
        self.source_combo.setEnabled(idle)

    def _load(self, *_) -> None:
        if self._thread is not None:
            return
        self.list.clear()
        self._films = []
        self.status_label.setText("Récupération de la liste AlloCiné…")
        self._start(_LoadThread(self.source_combo.currentData()), loaded=self._on_loaded,
                    failed=self._on_failed)

    def _on_loaded(self, films: list) -> None:
        self._release_thread()
        self._films = list(films)
        for film in self._films:
            self.list.addItem(film.title)
        self.status_label.setText(f"{len(self._films)} films. Choisis-en un, ou « Au hasard ».")
        self._update_buttons()

    def _on_failed(self, message: str) -> None:
        self._release_thread()
        self.status_label.setText("")
        self._update_buttons()
        QMessageBox.warning(self, "Devine le film", message)

    def _pick_random(self) -> None:
        if self._films:
            self.list.setCurrentRow(random.randrange(len(self._films)))

    def _export(self) -> None:
        from news_story.film_carousels import guess_folder_name

        row = self.list.currentRow()
        if row < 0:
            return
        parent = QFileDialog.getExistingDirectory(self, "Dossier où créer le carrousel")
        if not parent:
            return
        film = self._films[row]
        self._start(_ExportThread(film, Path(parent) / guess_folder_name(film.title)),
                    progress=self.status_label.setText, ready=self._on_ready,
                    failed=self._on_failed)

    def _on_ready(self, folder: str, caption: str) -> None:
        self._release_thread()
        self._caption = caption
        self.copy_btn.setEnabled(True)
        self._update_buttons()
        self.status_label.setText(f"Carrousel créé : {folder}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_caption(self) -> None:
        QGuiApplication.clipboard().setText(self._caption)
        self.status_label.setText("Légende copiée dans le presse-papiers.")

    def reject(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        super().reject()
