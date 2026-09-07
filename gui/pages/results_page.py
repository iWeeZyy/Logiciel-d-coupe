"""Page Resultats -- grille de clips classes par score, previsualisation
integree, export. Section 6/7/8/10 du cahier des charges."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.controller import AppController
from gui.thumbnails import ThumbnailThread
from gui.widgets.clip_card import RIGHTS_NOTICE, ClipCard
from gui.widgets.video_player import VideoPlayerDialog

_COLUMNS = 2


class ResultsPage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self._thumb_thread: ThumbnailThread | None = None
        self._cards: dict[str, ClipCard] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(4)

        header_row = QHBoxLayout()
        self.title_label = QLabel("Résultats")
        self.title_label.setProperty("role", "pageTitle")
        header_row.addWidget(self.title_label)
        header_row.addStretch(1)

        self.export_all_btn = QPushButton("Exporter tous les clips")
        self.export_all_btn.clicked.connect(self._export_all)
        header_row.addWidget(self.export_all_btn)

        self.open_folder_btn = QPushButton("Ouvrir le dossier")
        self.open_folder_btn.clicked.connect(self._open_folder)
        header_row.addWidget(self.open_folder_btn)
        outer.addLayout(header_row)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "subtitle")
        outer.addWidget(self.summary_label)

        self.rights_banner = QLabel(
            "⚠️ Cette vidéo provient de YouTube. Trouver une vidéo ne signifie pas disposer "
            "des droits nécessaires pour republier ou monétiser un extrait."
        )
        self.rights_banner.setWordWrap(True)
        self.rights_banner.setStyleSheet(
            "background: #FBEEDA; color: #7A5010; border-radius: 8px; padding: 10px 14px; font-size: 12.5px;"
        )
        self.rights_banner.setVisible(False)
        outer.addWidget(self.rights_banner)
        outer.addSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        self.grid_container = QWidget()
        self.grid = QGridLayout(self.grid_container)
        self.grid.setSpacing(18)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.grid_container)

    def on_shown(self) -> None:
        self._clear_grid()

        results = self.controller.last_results
        name = self.controller.current_project_name or ""
        source_kind = self.controller.last_source_kind

        self.summary_label.setText(f"{len(results)} clip(s) générés — {name}")
        self.rights_banner.setVisible(source_kind == "youtube")

        clip_paths: list[str] = []
        for i, clip in enumerate(results):
            clip_dict = clip.to_dict()
            clip_path = str(Path(self.controller.current_project_folder or ".") / clip_dict["clip"])
            clip_paths.append(clip_path)

            card = ClipCard(clip_path, clip_dict, source_kind=source_kind)
            card.play_requested.connect(lambda path=clip_path: self._play(path))
            self._cards[clip_path] = card
            self.grid.addWidget(card, i // _COLUMNS, i % _COLUMNS)

        if clip_paths:
            self._thumb_thread = ThumbnailThread(clip_paths)
            self._thumb_thread.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._thumb_thread.start()

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._cards.clear()

    def cleanup(self) -> None:
        """A la fermeture de l'appli -- voir AppController.shutdown() pour le
        pourquoi (un QThread encore actif a la destruction plante l'appli)."""
        if self._thumb_thread is not None and self._thumb_thread.isRunning():
            self._thumb_thread.wait(2000)
            if self._thumb_thread.isRunning():
                self._thumb_thread.terminate()
                self._thumb_thread.wait()

    def _on_thumbnail_ready(self, clip_path: str, thumb_path: str) -> None:
        card = self._cards.get(clip_path)
        if card:
            card.set_thumbnail(thumb_path)

    def _play(self, clip_path: str) -> None:
        clips = [(Path(p).name, p) for p in self._cards.keys()]
        start_index = list(self._cards.keys()).index(clip_path)
        dialog = VideoPlayerDialog(clips, start_index=start_index, parent=self)
        dialog.exec()

    def _confirm_rights_if_needed(self) -> bool:
        if not self.rights_banner.isVisible():
            return True
        reply = QMessageBox.warning(
            self, "Vérification des droits", RIGHTS_NOTICE,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Ok

    def _export_all(self) -> None:
        if not self._confirm_rights_if_needed():
            return
        if not self.controller.current_project_folder:
            return
        dest_dir = QFileDialog.getExistingDirectory(self, "Exporter tous les clips")
        if not dest_dir:
            return
        import shutil

        for clip_path in self._cards.keys():
            shutil.copy(clip_path, Path(dest_dir) / Path(clip_path).name)

    def _open_folder(self) -> None:
        if self.controller.current_project_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.current_project_folder)))
