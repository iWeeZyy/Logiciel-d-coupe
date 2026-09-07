"""Liste d'etapes avec coche/fleche/cercle.

Les libelles ne sont PAS ecrits ici : ils arrivent du moteur via
ProgressEvent.step_labels (voir core/steps.py). Le pipeline n'execute pas
toujours les memes etapes -- elles dependent des modules d'edition actives --
et une copie figee cote interface afficherait tot ou tard des etapes qui ne
correspondent a rien de reellement en cours.

Le detail fin (segment en cours, clip en cours) s'affiche a cote, dans
AnalysisPage, via sub_label.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

# Affiche avant le premier evenement de progression (et pendant un
# telechargement YouTube, qui precede le pipeline lui-meme).
_PLACEHOLDER_LABELS = ["Préparation…"]


class StepProgressList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

        self._labels: list[str] = []
        self._rows: list[QLabel] = []
        self._current_step = 0

        self.set_steps(_PLACEHOLDER_LABELS)

    def set_steps(self, labels: list[str]) -> None:
        """Reconstruit la liste si (et seulement si) les etapes ont change."""
        labels = list(labels) or list(_PLACEHOLDER_LABELS)
        if labels == self._labels:
            return

        self._labels = labels
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._rows = []
        for label in self._labels:
            row = QLabel(f"○  {label}")
            row.setStyleSheet("font-size: 13px;")
            self._layout.addWidget(row)
            self._rows.append(row)

        self.set_current_step(self._current_step)

    def reset(self) -> None:
        self._current_step = 0
        self.set_steps(_PLACEHOLDER_LABELS)
        self.set_current_step(0)

    def set_current_step(self, step_index: int) -> None:
        """step_index : 1..len(labels), ou 0 avant le debut (ex: pendant un
        telechargement YouTube, avant que le pipeline ne demarre)."""
        self._current_step = step_index
        for i, (row, label) in enumerate(zip(self._rows, self._labels), start=1):
            if i < step_index:
                row.setText(f"✓  {label}")
                row.setStyleSheet("font-size: 13px; color: #3FAF77; font-weight: 600;")
            elif i == step_index:
                row.setText(f"→  {label}")
                row.setStyleSheet("font-size: 13px; color: #E0A24C; font-weight: 700;")
            else:
                row.setText(f"○  {label}")
                row.setStyleSheet("font-size: 13px; color: #9498A2;")

    def mark_all_done(self) -> None:
        self.set_current_step(len(self._labels) + 1)
