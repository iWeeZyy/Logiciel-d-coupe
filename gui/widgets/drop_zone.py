"""Zone de glisser-deposer reutilisable -- accepte un fichier video, ou un
clic pour ouvrir le selecteur de fichiers."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QPushButton, QVBoxLayout

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")


class DropZone(QFrame):
    file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        icon = QLabel("🎬")
        icon.setStyleSheet("font-size: 40px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        self.main_label = QLabel("Glissez-déposez votre vidéo ici")
        self.main_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self.main_label)

        or_label = QLabel("ou")
        or_label.setProperty("role", "muted")
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(or_label)

        browse_btn = QPushButton("Sélectionner une vidéo")
        browse_btn.setProperty("variant", "primary")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.setFixedWidth(220)
        browse_btn.clicked.connect(self._open_dialog)
        layout.addWidget(browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.selected_label = QLabel("")
        self.selected_label.setProperty("role", "muted")
        self.selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_label.setWordWrap(True)
        layout.addWidget(self.selected_label)

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner une vidéo", "",
            "Vidéos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v);;Tous les fichiers (*)",
        )
        if path:
            self._select(path)

    def _select(self, path: str) -> None:
        from pathlib import Path

        self.selected_label.setText(f"Sélectionné : {Path(path).name}")
        self.file_selected.emit(path)

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(_VIDEO_EXTENSIONS):
            self.setProperty("active", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event) -> None:
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if urls:
            local_path = urls[0].toLocalFile()
            if local_path.lower().endswith(_VIDEO_EXTENSIONS):
                self._select(local_path)
        event.acceptProposedAction()
