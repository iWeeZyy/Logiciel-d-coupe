"""Liste d'etapes avec coche/fleche/cercle -- reflete les 5 VRAIES etapes du
pipeline (pas une liste plus longue inventee pour l'affichage : le cahier des
charges liste 9 lignes dans sa maquette, mais le moteur reel n'a que 5 etapes
macro -- inventer des coches qui ne correspondent a rien de reellement termine
serait mentir a l'utilisateur sur ce qui s'est passe). Le detail fin (segment
en cours, clip en cours) s'affiche a cote, dans AnalysisPage, via sub_label.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

STEP_LABELS = [
    "Extraction audio",
    "Transcription",
    "Analyse des hooks",
    "Sélection des meilleurs passages",
    "Génération des clips",
]


class StepProgressList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._rows: list[QLabel] = []
        for label in STEP_LABELS:
            row = QLabel(f"○  {label}")
            row.setStyleSheet("font-size: 13px;")
            layout.addWidget(row)
            self._rows.append(row)

        self.reset()

    def reset(self) -> None:
        self.set_current_step(0)

    def set_current_step(self, step_index: int) -> None:
        """step_index : 1..len(STEP_LABELS), ou 0 avant le debut (ex: pendant
        un telechargement YouTube, avant que le pipeline lui-meme ne demarre)."""
        for i, (row, label) in enumerate(zip(self._rows, STEP_LABELS), start=1):
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
        self.set_current_step(len(STEP_LABELS) + 1)
