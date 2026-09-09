"""Affichage d'une analyse terminee (section 13).

Un widget, pas une fenetre : la meme vue sert dans la fenetre d'analyse et
pourra servir ailleurs sans etre reecrite. Elle ne calcule rien -- elle rend un
ClipAnalysis deja produit par radar/analysis/runner.py.

Un bloc vide n'est pas affiche du tout. Un titre "Description" suivi de rien
laisserait croire a un bug ; l'absence d'un bloc dit simplement que le clip ne
contenait pas de quoi le remplir, ce que les avertissements expliquent en haut.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from radar.analysis.models import (
    CONFIDENCE_LABELS,
    LEVEL_LABELS,
    ClipAnalysis,
    format_timestamp,
)

COPIED_LABEL = "✓ Copié"
COPY_LABEL = "📋 Copier"


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setProperty("role", "divider")
    return line


class CopyableBlock(QWidget):
    """Un titre, un texte, un bouton copier."""

    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("font-weight: 700;")
        header.addWidget(label)
        header.addStretch(1)
        self.copy_button = QPushButton(COPY_LABEL)
        self.copy_button.setObjectName("copyButton")
        self.copy_button.clicked.connect(self._copy)
        header.addWidget(self.copy_button)
        layout.addLayout(header)

        self.body = QLabel(text)
        self.body.setWordWrap(True)
        # Selectionnable a la souris : le bouton Copier prend tout le bloc, mais
        # on doit pouvoir n'en reprendre qu'un morceau.
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.body)

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.text)
        # Retour visuel immediat : sans lui, rien ne distingue un clic qui a
        # copie d'un clic qui n'a rien fait.
        self.copy_button.setText(COPIED_LABEL)


class ClipAnalysisView(QWidget):
    """Le resultat complet, de haut en bas."""

    def __init__(self, analysis: ClipAnalysis, parent=None):
        super().__init__(parent)
        self.analysis = analysis
        self.blocks: dict[str, CopyableBlock] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        title = QLabel(analysis.clip_title or analysis.content_id)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(title)

        facts = []
        if analysis.creator_label:
            facts.append(analysis.creator_label)
        if analysis.duration_s:
            facts.append(f"⏱️ {format_timestamp(analysis.duration_s)}")
        if analysis.view_count is not None:
            facts.append(f"👁️ {analysis.view_count:,}".replace(",", " ") + " vues")
        if analysis.radar_score is not None:
            facts.append(f"📡 {analysis.radar_score:.0f}/100")
        if facts:
            line = QLabel("  •  ".join(facts))
            line.setProperty("role", "muted")
            layout.addWidget(line)

        meta = QLabel(f"{LEVEL_LABELS.get(analysis.analysis_level, analysis.analysis_level)}"
                      f"  •  modèle {analysis.model_used or '—'}"
                      f"  •  {analysis.processing_time_s or 0:.0f} s de traitement")
        meta.setProperty("role", "muted")
        layout.addWidget(meta)

        for warning in analysis.warnings:
            item = QLabel("⚠️ " + warning)
            item.setWordWrap(True)
            item.setStyleSheet("font-weight: 600;")
            layout.addWidget(item)

        moment = analysis.key_moment or {}
        if moment:
            layout.addWidget(_separator())
            header = QLabel("🎯 MOMENT CLÉ")
            header.setStyleSheet("font-weight: 700;")
            layout.addWidget(header)
            stamp = QLabel(f"{format_timestamp(moment.get('start'))} → "
                           f"{format_timestamp(moment.get('end'))}   {moment.get('label', '')}")
            stamp.setWordWrap(True)
            layout.addWidget(stamp)
            for label, key in (("Avant", "setup_text"), ("Réaction", "reaction_text"),
                               ("Suite", "payoff_text")):
                value = moment.get(key)
                if value:
                    row = QLabel(f"{label} : « {value} »")
                    row.setWordWrap(True)
                    row.setProperty("role", "muted")
                    layout.addWidget(row)
            for alternative in moment.get("alternatives", []):
                row = QLabel(f"Autre candidat : {format_timestamp(alternative.get('start'))} — "
                             f"« {alternative.get('text', '')} »")
                row.setWordWrap(True)
                row.setProperty("role", "muted")
                layout.addWidget(row)

        for key, title_text, value in (
            ("summary", "📝 RÉSUMÉ", analysis.summary),
            ("description", "📖 DESCRIPTION", analysis.description),
            ("short", "⚡ DESCRIPTION COURTE", analysis.short_description),
            ("social", "📱 DESCRIPTION RÉSEAUX SOCIAUX", analysis.social_description),
            ("hashtags", "🏷️ HASHTAGS", " ".join(analysis.hashtags)),
        ):
            if not value:
                continue
            layout.addWidget(_separator())
            block = CopyableBlock(title_text, value)
            self.blocks[key] = block
            layout.addWidget(block)

        titles = [(label, text) for label, text in (
            ("DIRECT", analysis.title_direct),
            ("CURIOSITÉ", analysis.title_curiosity),
            ("PUNCHY", analysis.title_punchy),
        ) if text]
        if titles:
            layout.addWidget(_separator())
            header = QLabel("🔥 TITRES")
            header.setStyleSheet("font-weight: 700;")
            layout.addWidget(header)
            for label, text in titles:
                block = CopyableBlock(label, text)
                self.blocks[f"title_{label.lower()}"] = block
                layout.addWidget(block)

        layout.addWidget(_separator())
        header = QLabel("🧠 ANALYSE")
        header.setStyleSheet("font-weight: 700;")
        layout.addWidget(header)

        if analysis.detected_topics:
            layout.addWidget(self._muted("Sujet : " + ", ".join(analysis.detected_topics)))
        if analysis.detected_emotions:
            for emotion in analysis.detected_emotions:
                confidence = CONFIDENCE_LABELS.get(emotion.get("confidence"), "")
                text = f"Émotion : {emotion.get('name')} — {confidence}"
                if emotion.get("evidence"):
                    text += f"\n    d'après « {emotion['evidence']} »"
                layout.addWidget(self._muted(text))
        else:
            layout.addWidget(self._muted("Émotion : aucune détectée avec certitude."))
        if analysis.detected_signals:
            layout.addWidget(self._muted("Signaux détectés : " + ", ".join(analysis.detected_signals)))
        if analysis.speech_density is not None:
            layout.addWidget(self._muted(
                f"Densité de parole : {analysis.speech_density:.1f} mot/s  •  "
                f"silence : {(analysis.silence_ratio or 0) * 100:.0f} %"))

        layout.addWidget(_separator())
        confidence = QLabel("🎯 CONFIANCE   " + CONFIDENCE_LABELS.get(analysis.confidence,
                                                                     analysis.confidence))
        confidence.setStyleSheet("font-weight: 700;")
        layout.addWidget(confidence)
        for reason in analysis.confidence_reasons:
            layout.addWidget(self._muted("• " + reason))

        if analysis.transcript_text:
            layout.addWidget(_separator())
            block = CopyableBlock("🗣️ TRANSCRIPTION", analysis.transcript_text)
            self.blocks["transcript"] = block
            layout.addWidget(block)

        layout.addStretch(1)

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setProperty("role", "muted")
        return label
