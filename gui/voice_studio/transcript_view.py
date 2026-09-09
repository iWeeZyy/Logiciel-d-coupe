"""Affichage de la transcription : minutages, recherche, selection.

Un QTextEdit en lecture seule plutot qu'une liste de widgets : une video d'une
heure produit des centaines de segments, et surtout l'utilisateur doit pouvoir
SELECTIONNER librement du texte a cheval sur plusieurs segments pour le faire
lire. Une liste de blocs separes rendrait cette selection impossible.

Le minutage de chaque bloc est ecrit dans le texte, et la position de chaque
segment est memorisee : c'est ce qui permet de retrouver le segment sous le
curseur, donc l'instant correspondant dans la video.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from voice_studio.transcript import (
    VIEW_CLEAN,
    VIEW_RAW,
    clean_text,
    format_timestamp,
    search,
    segment_label,
)

HIGHLIGHT = QColor("#FFD54F")
CURRENT = QColor("#FF8A65")


class TranscriptView(QTextEdit):
    """Vue de la transcription. Lecture seule, selection libre."""

    segment_activated = Signal(float)     # instant de debut du segment clique

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setAcceptRichText(False)
        self._transcript = None
        self._view = VIEW_RAW
        self._spans: list[tuple[int, int, object]] = []   # debut, fin, segment
        self._matches: list = []
        self._current_match = -1

    # ------------------------------------------------------------ contenu
    def set_transcript(self, transcript, view: str = VIEW_RAW) -> None:
        self._transcript = transcript
        self._view = view
        self._render()

    def set_view(self, view: str) -> None:
        if view != self._view:
            self._view = view
            self._render()

    @property
    def view(self) -> str:
        return self._view

    def _render(self) -> None:
        self._spans = []
        self._matches = []
        self._current_match = -1
        if self._transcript is None:
            self.setPlainText("")
            return

        if self._view == VIEW_CLEAN:
            # Vue nettoyee : mise en page seule, sans minutage a l'ecran. Le
            # texte brut reste accessible d'un clic, et c'est lui qui fait foi.
            self.setPlainText(clean_text(self._transcript))
            return

        pieces = []
        position = 0
        for segment in self._transcript.segments:
            header = f"[{segment_label(segment)}]\n"
            body = (segment.text or "").strip()
            pieces.append(header + body)
            start = position + len(header)
            self._spans.append((start, start + len(body), segment))
            position += len(header) + len(body) + 2      # les deux sauts de ligne
        self.setPlainText("\n\n".join(pieces))

    # ---------------------------------------------------------- recherche
    def find_all(self, query: str) -> int:
        """Surligne toutes les occurrences, renvoie leur nombre."""
        self._matches = []
        self._current_match = -1
        self.setExtraSelections([])
        if not query.strip() or self._transcript is None or self._view != VIEW_RAW:
            return 0

        selections = []
        for match in search(self._transcript, query):
            if match.segment_index >= len(self._spans):
                continue
            span_start, _, _ = self._spans[match.segment_index]
            start = span_start + match.start_in_segment
            end = span_start + match.end_in_segment
            self._matches.append((start, end))

            selection = QTextEdit.ExtraSelection()
            selection.format = QTextCharFormat()
            selection.format.setBackground(HIGHLIGHT)
            cursor = self.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selections.append(selection)

        self.setExtraSelections(selections)
        if self._matches:
            self.go_to_match(0)
        return len(self._matches)

    def go_to_match(self, index: int) -> None:
        if not self._matches:
            return
        self._current_match = index % len(self._matches)
        start, end = self._matches[self._current_match]
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def next_match(self) -> None:
        self.go_to_match(self._current_match + 1)

    def previous_match(self) -> None:
        self.go_to_match(self._current_match - 1)

    @property
    def match_count(self) -> int:
        return len(self._matches)

    @property
    def current_match_index(self) -> int:
        return self._current_match

    # ---------------------------------------------------------- selection
    def selected_text(self) -> str:
        """Texte selectionne, minutages retires.

        Les minutages sont une aide a la lecture, pas du contenu : les envoyer
        a la synthese vocale les ferait lire a voix haute.
        """
        raw = self.textCursor().selectedText().replace(" ", "\n")
        lines = [line for line in raw.split("\n")
                 if not (line.strip().startswith("[") and "→" in line)]
        return "\n".join(lines).strip()

    def segment_at_cursor(self):
        position = self.textCursor().position()
        for start, end, segment in self._spans:
            if start <= position <= end:
                return segment
        return None

    def mouseDoubleClickEvent(self, event) -> None:      # pragma: no cover - interaction
        super().mouseDoubleClickEvent(event)
        segment = self.segment_at_cursor()
        if segment is not None:
            self.segment_activated.emit(float(segment.start))

    def plain_text(self, with_timestamps: bool = True) -> str:
        if with_timestamps and self._view == VIEW_RAW:
            return self.toPlainText()
        if self._transcript is None:
            return ""
        if self._view == VIEW_CLEAN:
            return clean_text(self._transcript)
        return "\n".join((s.text or "").strip() for s in self._transcript.segments)
