"""Page Analyse en cours -- jamais l'impression que l'app est bloquee :
barre de progression globale, etapes reelles, detail courant, temps ecoule/
estimation restante, et un bouton Annuler qui fonctionne vraiment (le
pipeline tourne dans un QThread, voir gui/controller.py)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
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

        # Le moteur n'emet un evenement qu'a chaque segment transcrit : sur une
        # longue video en processeur, cela fait des mises a jour espacees et
        # irregulieres, et les compteurs semblent bloques alors que tout va
        # bien. L'horloge de la page tourne donc a la seconde, calee sur le
        # dernier temps donne par le moteur -- affiche, pas invente : entre deux
        # evenements on ajoute simplement le temps reellement ecoule.
        self._elapsed_ref = 0.0          # temps ecoule annonce par le moteur
        self._elapsed_ref_at = time.monotonic()
        self._overall = 0.0              # avancement global connu (0..1)
        self._remaining_ref: float | None = None   # estimation au dernier evenement
        self._remaining_ref_at = 0.0
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)

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
        self._elapsed_ref = 0.0
        self._elapsed_ref_at = time.monotonic()
        self._overall = 0.0
        self._remaining_ref = None
        self._ticker.start()

    def hideEvent(self, event) -> None:
        # Quitter la page (fin, echec ou annulation) arrete l'horloge : elle n'a
        # plus rien a compter et continuerait a tourner dans le vide.
        self._ticker.stop()
        super().hideEvent(event)

    def _current_elapsed(self) -> float:
        return self._elapsed_ref + max(0.0, time.monotonic() - self._elapsed_ref_at)

    def _tick(self) -> None:
        self.elapsed_label.value_label.setText(_format_duration(self._current_elapsed()))
        if self._remaining_ref is None:
            return
        # Entre deux evenements, l'estimation decompte simplement le temps qui
        # passe. Recalculer la formule ici la ferait AUGMENTER (le temps monte
        # pendant que l'avancement, lui, ne bouge pas) : un temps restant qui
        # grimpe puis retombe a chaque evenement est illisible. Elle est
        # recalculee, elle, a chaque evenement du moteur.
        left = self._remaining_ref - (time.monotonic() - self._remaining_ref_at)
        self.remaining_label.value_label.setText(f"~{_format_duration(max(0.0, left))}")

    def _refresh_remaining(self, elapsed: float) -> None:
        # Meme regle qu'avant : rien n'est annonce tant que l'avancement ou le
        # temps ecoule sont trop faibles pour qu'une estimation ait un sens.
        if not (0 < self._overall < 1) or elapsed <= 3:
            return
        self._remaining_ref = elapsed * (1 - self._overall) / self._overall
        self._remaining_ref_at = time.monotonic()
        self.remaining_label.value_label.setText(f"~{_format_duration(self._remaining_ref)}")

    def _on_progress(self, event: ProgressEvent) -> None:
        # Les etapes viennent du moteur (core/steps.py) : elles varient selon
        # les modules d'edition actives, voir gui/widgets/step_progress.py.
        if event.step_labels:
            self.steps.set_steps(event.step_labels)
        self.steps.set_current_step(event.step_index)

        fraction = event.step_fraction or 0.0
        self._overall = (max(0, event.step_index - 1) + fraction) / event.total_steps
        self.progress_bar.setValue(int(round(self._overall * 100)))

        detail = event.sub_label or f"{event.label}…"
        self.detail_label.setText(detail)

        # Le moteur reste la reference du temps ecoule ; l'horloge de la page se
        # recale dessus a chaque evenement et ne fait qu'interpoler entre deux.
        self._elapsed_ref = event.elapsed_s
        self._elapsed_ref_at = time.monotonic()
        self.elapsed_label.value_label.setText(_format_duration(event.elapsed_s))

        if event.clips_found is not None:
            self.clips_found_label.value_label.setText(str(event.clips_found))

        self._refresh_remaining(event.elapsed_s)
