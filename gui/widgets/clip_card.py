"""Carte resultat -- score, apercu, timestamps, transcription, detail du
score, actions (Lire / Ouvrir / Exporter). Une carte = un ClipResult, jamais
plus (pas de logique de scoring ici, seulement de l'affichage -- section 14)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gui.widgets.score_breakdown import ScoreBreakdown

THUMB_HEIGHT = 160

RIGHTS_NOTICE = (
    "Cette vidéo provient de YouTube. Trouver une vidéo ne signifie pas "
    "disposer des droits nécessaires pour republier ou monétiser un extrait.\n\n"
    "Continuer l'export ?"
)


def _format_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _score_emoji(score: float) -> str:
    return "🔥" if score >= 85 else ("⭐" if score >= 70 else "")


class ClipCard(QFrame):
    play_requested = Signal(str)  # chemin du clip

    def __init__(self, clip_path: str, clip_dict: dict, source_kind: str = "local", parent=None):
        super().__init__(parent)
        self.clip_path = clip_path
        self.clip_dict = clip_dict
        self.source_kind = source_kind
        self.setProperty("role", "card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        score = clip_dict.get("score", 0.0)
        header = QLabel(f"{_score_emoji(score)}  {score:.0f}/100".strip())
        header.setStyleSheet("font-size: 17px; font-weight: 800;")
        layout.addWidget(header)

        self.thumb_label = QLabel("🎬  Aperçu indisponible")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setFixedHeight(THUMB_HEIGHT)
        self.thumb_label.setStyleSheet(
            "background: #EDEFF3; border-radius: 8px; color: #9498A2; font-size: 12.5px;"
        )
        layout.addWidget(self.thumb_label)

        start = clip_dict.get("start", 0.0)
        end = clip_dict.get("end", 0.0)
        duration = clip_dict.get("duration", end - start)
        timing = QLabel(f"{_format_timestamp(start)} → {_format_timestamp(end)}  •  {duration:.0f}s")
        timing.setProperty("role", "mono")
        timing.setStyleSheet("font-size: 12.5px;")
        layout.addWidget(timing)

        transcript = clip_dict.get("transcript", "")
        excerpt = (transcript[:160] + "…") if len(transcript) > 160 else transcript
        transcript_label = QLabel(f"« {excerpt} »" if excerpt else "")
        transcript_label.setWordWrap(True)
        transcript_label.setStyleSheet("font-size: 12.5px; font-style: italic;")
        layout.addWidget(transcript_label)

        layout.addWidget(ScoreBreakdown(clip_dict.get("scores", {})))

        actions = QHBoxLayout()
        play_btn = QPushButton("▶ Lire")
        play_btn.setProperty("variant", "primary")
        play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_btn.clicked.connect(lambda: self.play_requested.emit(self.clip_path))
        actions.addWidget(play_btn)

        open_btn = QPushButton("Ouvrir")
        open_btn.clicked.connect(self._open_externally)
        actions.addWidget(open_btn)

        export_btn = QPushButton("📁 Exporter")
        export_btn.clicked.connect(self._export)
        actions.addWidget(export_btn)

        layout.addLayout(actions)

    def set_thumbnail(self, thumb_path: str) -> None:
        pixmap = QPixmap(thumb_path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaledToHeight(THUMB_HEIGHT, Qt.TransformationMode.SmoothTransformation)
        self.thumb_label.setPixmap(scaled)
        self.thumb_label.setStyleSheet("border-radius: 8px;")

    def _open_externally(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.clip_path))

    def _export(self) -> None:
        if self.source_kind == "youtube":
            reply = QMessageBox.warning(
                self, "Vérification des droits", RIGHTS_NOTICE,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                return

        suggested = str(Path.home() / Path(self.clip_path).name)
        dest, _ = QFileDialog.getSaveFileName(self, "Exporter ce clip", suggested, "Vidéo (*.mp4)")
        if dest:
            import shutil

            shutil.copyfile(self.clip_path, dest)
