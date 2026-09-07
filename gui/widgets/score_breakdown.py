"""Detail du score par critere -- expose tel quel les valeurs de
analysis/scoring.py (jamais recalcule ici, jamais invente : la GUI affiche
ce que le moteur a produit, section 14 du cahier des charges)."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar, QWidget

_LABELS = [
    ("audio", "Audio"),
    ("keywords", "Mots forts"),
    ("questions", "Questions"),
    ("speech_density", "Densité"),
    ("silence_build_up", "Silence / buildup"),
    ("intensity", "Intensité"),
]


class ScoreBreakdown(QWidget):
    def __init__(self, scores: dict, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        for row, (key, label) in enumerate(_LABELS):
            value = scores.get(key, 0.0)

            name = QLabel(label)
            name.setStyleSheet("font-size: 12px;")
            grid.addWidget(name, row, 0)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(round(value)))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            grid.addWidget(bar, row, 1)

            value_label = QLabel(f"{value:.0f}")
            value_label.setProperty("role", "mono")
            value_label.setStyleSheet("font-size: 12px;")
            value_label.setFixedWidth(28)
            grid.addWidget(value_label, row, 2)

        grid.setColumnStretch(1, 1)
