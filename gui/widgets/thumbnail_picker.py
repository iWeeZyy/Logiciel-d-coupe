"""Choix de la miniature parmi les trois variantes generees.

Les trois sont montrees cote a cote, en grand : une miniature se juge a l'oeil,
pas sur un nom de fichier. Le choix retenu est simplement exporte -- les trois
fichiers restent dans le projet, rien n'est supprime.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

_PREVIEW_HEIGHT = 420

_VARIANT_LABELS = {
    "a": "Visage + phrase forte",
    "b": "Plan de contexte",
    "c": "Meilleure expression",
}


class ThumbnailPicker(QDialog):
    def __init__(self, clip_index: int, project_folder: Path, thumbnails: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Miniatures — clip {clip_index:02d}")
        self._paths = [project_folder / relative for relative in thumbnails]
        self._selected: Path | None = self._paths[0] if self._paths else None
        self._frames: list[tuple[Path, QFrame]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        if not self._paths:
            layout.addWidget(QLabel(
                "Aucune miniature n'a pu être générée pour ce clip.\n"
                "Aucune image n'était assez nette, ou le module est désactivé."
            ))
        else:
            row = QHBoxLayout()
            row.setSpacing(14)
            for path in self._paths:
                row.addWidget(self._variant_widget(path))
            layout.addLayout(row)
            self._highlight(self._selected)

        buttons = QDialogButtonBox()
        if self._paths:
            export = buttons.addButton("Exporter cette miniature", QDialogButtonBox.ButtonRole.AcceptRole)
            export.clicked.connect(self._export)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(lambda b: self.reject() if b.text() == "Close" else None)
        layout.addWidget(buttons)

    def _variant_widget(self, path: Path) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            image.setPixmap(pixmap.scaledToHeight(_PREVIEW_HEIGHT, Qt.TransformationMode.SmoothTransformation))
        else:
            image.setText("Aperçu indisponible")
        layout.addWidget(image)

        variant = path.stem.rsplit("_", 1)[-1]
        caption = QLabel(_VARIANT_LABELS.get(variant, variant.upper()))
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setProperty("role", "muted")
        layout.addWidget(caption)

        frame.mousePressEvent = lambda _event, p=path: self._select(p)
        self._frames.append((path, frame))
        return frame

    def _select(self, path: Path) -> None:
        self._selected = path
        self._highlight(path)

    def _highlight(self, selected: Path | None) -> None:
        for path, frame in self._frames:
            frame.setStyleSheet(
                "border: 3px solid #E0A24C; border-radius: 10px;" if path == selected else ""
            )

    def _export(self) -> None:
        if self._selected is None:
            return
        suggested = str(Path.home() / self._selected.name)
        dest, _ = QFileDialog.getSaveFileName(self, "Exporter la miniature", suggested, "Image (*.jpg)")
        if dest:
            import shutil

            shutil.copyfile(self._selected, dest)
            self.accept()
