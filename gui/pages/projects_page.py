"""Page Projets -- historique des analyses passees (section 11)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.controller import AppController

_MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _format_date(iso_date: str) -> str:
    if not iso_date:
        return ""
    try:
        dt = datetime.fromisoformat(iso_date)
        return f"{dt.day} {_MONTHS_FR[dt.month - 1]} {dt.year}"
    except ValueError:
        return iso_date


class ProjectsPage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Mes projets")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)
        outer.addSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setSpacing(10)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.list_container)

        self.empty_label = QLabel("Aucun projet pour l'instant — lancez une analyse depuis Accueil.")
        self.empty_label.setProperty("role", "muted")

    def on_shown(self) -> None:
        self._reload()

    def _reload(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        summaries = self.controller.list_projects()
        if not summaries:
            self.list_layout.addWidget(self.empty_label)
            return

        for summary in summaries:
            self.list_layout.addWidget(self._build_row(summary))

    def _build_row(self, summary) -> QFrame:
        row = QFrame()
        row.setProperty("role", "card")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(18, 14, 18, 14)

        info = QVBoxLayout()
        icon = "🔎" if summary.source_kind == "youtube" else "🎬"
        name_label = QLabel(f"{icon}  {summary.name}")
        name_label.setStyleSheet("font-size: 14.5px; font-weight: 700;")
        info.addWidget(name_label)

        meta = QLabel(f"{summary.clip_count} clip(s)  •  {_format_date(summary.created_at)}")
        meta.setProperty("role", "muted")
        info.addWidget(meta)
        layout.addLayout(info, stretch=1)

        open_btn = QPushButton("Ouvrir")
        open_btn.setProperty("variant", "primary")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: self.controller.open_project(summary.folder))
        layout.addWidget(open_btn)

        delete_btn = QPushButton("Supprimer")
        delete_btn.setProperty("variant", "danger")
        delete_btn.clicked.connect(lambda: self._delete(summary))
        layout.addWidget(delete_btn)

        return row

    def _delete(self, summary) -> None:
        reply = QMessageBox.question(
            self, "Supprimer ce projet",
            f"Supprimer définitivement « {summary.name} » et ses {summary.clip_count} clip(s) ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.controller.delete_project(summary.folder)
            self._reload()
