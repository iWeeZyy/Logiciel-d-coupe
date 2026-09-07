"""Fenetre d'edition des titres et de la description d'un clip.

Les trois propositions sont affichees telles qu'elles ont ete EXTRAITES du clip
(editing/metadata.py n'invente rien), et l'utilisateur peut les reecrire
librement : c'est lui qui publie, il doit avoir le dernier mot.

Le bouton Copier est la parce que ces textes finissent dans un champ
Instagram/TikTok : les selectionner a la souris dans une liste serait le
detail qui rend l'outil penible au quotidien.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

_KIND_LABELS = {
    "direct": "Direct",
    "curiosite": "Curiosité",
    "punchy": "Punchy",
}


class MetadataDialog(QDialog):
    def __init__(self, clip_index: int, metadata: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Titres et description — clip {clip_index:02d}")
        self.setMinimumWidth(620)
        self._metadata = dict(metadata or {})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        title = QLabel("TITRES")
        title.setProperty("role", "sectionLabel")
        layout.addWidget(title)

        self._title_edits: list[tuple[str, QLineEdit]] = []
        titles = self._metadata.get("titles", [])
        if not titles:
            empty = QLabel(
                "Aucun titre n'a pu être extrait de ce clip.\n"
                "Rien n'est inventé : écrivez le vôtre ci-dessous."
            )
            empty.setProperty("role", "muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            titles = [{"kind": "direct", "text": ""}]

        for proposal in titles:
            kind = proposal.get("kind", "direct")
            layout.addWidget(self._field_row(kind, proposal.get("text", "")))

        description_label = QLabel("DESCRIPTION")
        description_label.setProperty("role", "sectionLabel")
        layout.addWidget(description_label)

        self.description_edit = QPlainTextEdit(self._metadata.get("description", ""))
        self.description_edit.setFixedHeight(90)
        layout.addWidget(self.description_edit)

        hashtags = self._metadata.get("hashtags", [])
        if hashtags:
            tags = QLabel(" ".join(hashtags))
            tags.setProperty("role", "muted")
            tags.setWordWrap(True)
            layout.addWidget(tags)

        copy_row = QHBoxLayout()
        copy_row.addStretch(1)
        copy_all = QDialogButtonBox()
        copy_button = copy_all.addButton("Copier titre + description", QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(self._copy_all)
        copy_row.addWidget(copy_all)
        layout.addLayout(copy_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _field_row(self, kind: str, text: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label = QLabel(_KIND_LABELS.get(kind, kind.capitalize()))
        label.setFixedWidth(84)
        label.setProperty("role", "muted")
        layout.addWidget(label)

        edit = QLineEdit(text)
        layout.addWidget(edit, stretch=1)
        self._title_edits.append((kind, edit))

        copy = QLabel("⧉")
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.setToolTip("Copier ce titre")
        copy.mousePressEvent = lambda _event, e=edit: QGuiApplication.clipboard().setText(e.text())
        layout.addWidget(copy)
        return row

    def _copy_all(self) -> None:
        first = next((edit.text() for _kind, edit in self._title_edits if edit.text().strip()), "")
        parts = [first, self.description_edit.toPlainText().strip()]
        hashtags = " ".join(self._metadata.get("hashtags", []))
        if hashtags:
            parts.append(hashtags)
        QGuiApplication.clipboard().setText("\n\n".join(p for p in parts if p))

    def edited_metadata(self) -> dict:
        """Metadonnees telles que l'utilisateur les a laissees. Un titre vide
        est retire : mieux vaut deux propositions que trois dont une blanche."""
        data = dict(self._metadata)
        data["titles"] = [
            {"kind": kind, "text": edit.text().strip()}
            for kind, edit in self._title_edits
            if edit.text().strip()
        ]
        data["description"] = self.description_edit.toPlainText().strip()
        return data
