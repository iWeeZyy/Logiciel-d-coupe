"""Carte resultat -- score, apercu, timestamps, transcription, detail du
score, actions (Lire / Ouvrir / Exporter). Une carte = un ClipResult, jamais
plus (pas de logique de scoring ici, seulement de l'affichage -- section 14)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
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


_CATEGORY_LABELS = {
    "revelation": "🔥 Révélation",
    "histoire": "📖 Histoire",
    "conclusion": "🎯 Conclusion",
    "explication": "💡 Explication",
    "liste": "🔢 Liste",
    "conseil": "✅ Conseil",
}


class ClipCard(QFrame):
    play_requested = Signal(str)          # chemin du clip
    metadata_requested = Signal(int)      # index du clip
    thumbnails_requested = Signal(int)
    performance_requested = Signal(int)  # index du clip -- saisie des stats reelles

    def __init__(self, clip_path: str, clip_dict: dict, source_kind: str = "local", parent=None):
        super().__init__(parent)
        self.clip_path = clip_path
        self.clip_dict = clip_dict
        self.source_kind = source_kind
        self.clip_index = clip_dict.get("index", 0)
        self.setProperty("role", "card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        scores = clip_dict.get("scores", {})
        score = clip_dict.get("score", 0.0)

        header_row = QHBoxLayout()
        header = QLabel(f"{_score_emoji(score)}  {score:.0f}/100".strip())
        header.setStyleSheet("font-size: 17px; font-weight: 800;")
        header_row.addWidget(header)
        header_row.addStretch(1)

        category = (clip_dict.get("context") or {}).get("category", "")
        if category:
            badge = QLabel(_CATEGORY_LABELS.get(category, category.capitalize()))
            badge.setStyleSheet(
                "background: #FBEEDA; color: #7A5010; border-radius: 10px;"
                " padding: 3px 10px; font-size: 11.5px; font-weight: 700;"
            )
            header_row.addWidget(badge)
        layout.addLayout(header_row)

        # Les trois scores demandes cote a cote : le total seul ne dit pas si un
        # clip accroche, se revoit, ou dit quelque chose.
        trio = QLabel(
            f"Viral {scores.get('viral', score):.0f}   "
            f"Hook {scores.get('total', 0):.0f}   "
            f"Rewatch {scores.get('rewatch', 0):.0f}"
        )
        trio.setProperty("role", "mono")
        trio.setStyleSheet("font-size: 12px; color: #6C707B;")
        layout.addWidget(trio)

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

        metadata = clip_dict.get("metadata") or {}
        titles = metadata.get("titles") or []
        self.title_label = QLabel()
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 13.5px; font-weight: 700;")
        layout.addWidget(self.title_label)

        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setProperty("role", "muted")
        layout.addWidget(self.description_label)
        self.set_metadata(metadata)

        if not titles:
            transcript = clip_dict.get("transcript", "")
            excerpt = (transcript[:140] + "…") if len(transcript) > 140 else transcript
            if excerpt:
                transcript_label = QLabel(f"« {excerpt} »")
                transcript_label.setWordWrap(True)
                transcript_label.setStyleSheet("font-size: 12.5px; font-style: italic;")
                layout.addWidget(transcript_label)

        layout.addWidget(ScoreBreakdown(clip_dict.get("scores", {})))

        actions = QHBoxLayout()
        actions.setSpacing(6)
        play_btn = QPushButton("▶ Lire")
        play_btn.setProperty("variant", "primary")
        play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_btn.clicked.connect(lambda: self.play_requested.emit(self.clip_path))
        actions.addWidget(play_btn)

        edit_btn = QPushButton("✏ Modifier")
        edit_btn.setToolTip("Modifier les titres et la description")
        edit_btn.clicked.connect(lambda: self.metadata_requested.emit(self.clip_index))
        actions.addWidget(edit_btn)

        thumbs_btn = QPushButton("🖼 Miniatures")
        thumbs_btn.setEnabled(bool(clip_dict.get("thumbnails")))
        thumbs_btn.setToolTip(
            "Choisir parmi les miniatures generees" if clip_dict.get("thumbnails")
            else "Aucune miniature générée pour ce clip"
        )
        thumbs_btn.clicked.connect(lambda: self.thumbnails_requested.emit(self.clip_index))
        actions.addWidget(thumbs_btn)

        export_btn = QPushButton("📤 Exporter")
        export_btn.clicked.connect(self._export)
        actions.addWidget(export_btn)

        # Saisie des performances reelles (section 12). Sur une deuxieme ligne :
        # cette action ne s'utilise pas au moment de la production mais en
        # revenant dans le projet, une fois le clip publie.
        layout.addLayout(actions)

        self.performance_btn = QPushButton("📊 Ajouter les performances")
        self.performance_btn.setToolTip(
            "Saisir les statistiques obtenues sur la plateforme où ce clip a été publié"
        )
        self.performance_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.performance_btn.clicked.connect(
            lambda: self.performance_requested.emit(self.clip_index)
        )
        layout.addWidget(self.performance_btn)

    def set_performance_recorded(self, recorded: bool, summary: str = "") -> None:
        """Le bouton dit si des performances existent deja, pour qu'on n'ait pas
        a ouvrir la fenetre pour le savoir."""
        self.performance_btn.setText(
            f"📊 Performances — {summary}" if recorded and summary
            else ("📊 Modifier les performances" if recorded else "📊 Ajouter les performances")
        )

    def set_metadata(self, metadata: dict) -> None:
        """Rafraichit titre et description apres une modification manuelle."""
        self.clip_dict["metadata"] = metadata
        titles = metadata.get("titles") or []
        self.title_label.setText(titles[0]["text"] if titles else "")
        self.title_label.setVisible(bool(titles))
        description = metadata.get("description", "")
        excerpt = (description[:150] + "…") if len(description) > 150 else description
        self.description_label.setText(excerpt)
        self.description_label.setVisible(bool(excerpt))

    def set_thumbnail(self, thumb_path: str) -> None:
        pixmap = QPixmap(thumb_path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaledToHeight(THUMB_HEIGHT, Qt.TransformationMode.SmoothTransformation)
        self.thumb_label.setPixmap(scaled)
        self.thumb_label.setStyleSheet("border-radius: 8px;")

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
