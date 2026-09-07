"""Page Analyse en cours -- jamais l'impression que l'app est bloquee :
barre de progression globale, etapes reelles, detail courant, temps ecoule/
estimation restante, et un bouton Annuler qui fonctionne vraiment (le
pipeline tourne dans un QThread, voir gui/controller.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from gui.controller import AppController
from gui.widgets.step_progress import StepProgressList
from core.models import ProgressEvent


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class AnalysisPage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        controller.progress_updated.connect(self._on_progress)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 36, 40, 36)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Analyse de la vidéo")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)
        outer.addSpacing(20)

        card = QFrame()
        card.setProperty("role", "card")
        card.setMaximumWidth(620)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(16)
        outer.addWidget(card)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        card_layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("Préparation…")
        self.detail_label.setProperty("role", "muted")
        self.detail_label.setWordWrap(True)
        card_layout.addWidget(self.detail_label)

        self.steps = StepProgressList()
        card_layout.addWidget(self.steps)

        stats_row = QHBoxLayout()
        self.clips_found_label = self._stat("Clips trouvés", "—")
        self.elapsed_label = self._stat("Temps écoulé", "00:00")
        self.remaining_label = self._stat("Temps restant estimé", "—")
        for stat in (self.clips_found_label, self.elapsed_label, self.remaining_label):
            stats_row.addWidget(stat)
        card_layout.addLayout(stats_row)

        cancel_btn = QPushButton("Annuler l'analyse")
        cancel_btn.setProperty("variant", "danger")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(controller.cancel_analysis)
        card_layout.addWidget(cancel_btn)

    def _stat(self, label: str, value: str) -> QWidget:
        box = QFrame()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title = QLabel(label)
        title.setProperty("role", "muted")
        value_label = QLabel(value)
        value_label.setProperty("role", "mono")
        value_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)
        layout.addWidget(value_label)
        box.value_label = value_label  # type: ignore[attr-defined]
        return box

    def on_shown(self) -> None:
        self.steps.reset()
        self.progress_bar.setValue(0)
        self.detail_label.setText("Préparation…")
        self.clips_found_label.value_label.setText("—")
        self.elapsed_label.value_label.setText("00:00")
        self.remaining_label.value_label.setText("—")

    def _on_progress(self, event: ProgressEvent) -> None:
        # Les etapes viennent du moteur (core/steps.py) : elles varient selon
        # les modules d'edition actives, voir gui/widgets/step_progress.py.
        if event.step_labels:
            self.steps.set_steps(event.step_labels)
        self.steps.set_current_step(event.step_index)

        fraction = event.step_fraction or 0.0
        overall = (max(0, event.step_index - 1) + fraction) / event.total_steps
        self.progress_bar.setValue(int(round(overall * 100)))

        detail = event.sub_label or f"{event.label}…"
        self.detail_label.setText(detail)

        self.elapsed_label.value_label.setText(_format_duration(event.elapsed_s))

        if event.clips_found is not None:
            self.clips_found_label.value_label.setText(str(event.clips_found))

        if 0 < overall < 1 and event.elapsed_s > 3:
            remaining = event.elapsed_s * (1 - overall) / overall
            self.remaining_label.value_label.setText(f"~{_format_duration(remaining)}")
