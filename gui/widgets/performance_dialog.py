"""Saisie manuelle des performances d'un clip (section 12).

Tous les champs sont facultatifs, y compris les vues : le logiciel doit
fonctionner meme si l'utilisateur ne renseigne qu'un chiffre. Un champ laisse
vide vaut "je ne sais pas" et non "zero" -- d'ou des QLineEdit valides a la
main plutot que des QSpinBox, qui affichent toujours une valeur et rendraient
l'absence impossible a exprimer.

Aucune logique d'analyse ici : la fenetre lit et ecrit un ClipPerformance, rien
de plus. Le calcul du Performance Score vit dans performance/metrics.py.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from performance.models import ClipPerformance

# (attribut, libelle, suffixe) -- l'ordre est celui de la section 12.
_INT_FIELDS = [
    ("views", "Vues", ""),
    ("likes", "Likes", ""),
    ("comments", "Commentaires", ""),
    ("shares", "Partages", ""),
    ("saves", "Enregistrements", ""),
    ("impressions", "Impressions (si connues)", ""),
    ("follower_count", "Abonnés au moment de la publication", ""),
]
_FLOAT_FIELDS = [
    ("average_view_duration_s", "Durée moyenne de visionnage", "secondes"),
    ("completion_rate", "Taux de complétion", "%"),
    ("rewatch_rate", "Rewatch / vues répétées", "%"),
]


def _parse_number(text: str, cast):
    """Texte -> nombre, ou None si le champ est vide ou illisible.

    Illisible traite comme vide, volontairement : refuser d'enregistrer toute
    la fiche parce qu'un champ contient "12 k" ferait perdre les huit autres.
    """
    text = (text or "").strip().replace(" ", "").replace(",", ".").replace("%", "")
    if not text:
        return None
    try:
        return cast(float(text))
    except ValueError:
        return None


class PerformanceDialog(QDialog):
    """Renvoie le ClipPerformance saisi via `result_performance()`."""

    def __init__(self, clip_id: str, clip_label: str,
                 existing: ClipPerformance | None = None, parent=None):
        super().__init__(parent)
        self.clip_id = clip_id
        self.setWindowTitle("Ajouter les performances")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        title = QLabel(clip_label)
        title.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(title)

        hint = QLabel(
            "Tous les champs sont facultatifs. Un champ laissé vide signifie "
            "« je ne sais pas », pas « zéro ».\nCes données restent sur votre "
            "machine et ne sont envoyées nulle part."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        self._edits: dict[str, QLineEdit] = {}

        self.platform_edit = QLineEdit(existing.platform if existing else "")
        self.platform_edit.setPlaceholderText("TikTok, Instagram, YouTube Shorts…")
        form.addRow("Plateforme", self.platform_edit)

        self.published_edit = QLineEdit(
            existing.published_at if existing and existing.published_at else ""
        )
        self.published_edit.setPlaceholderText(date.today().isoformat())
        form.addRow("Date de publication", self.published_edit)

        for attribute, label, suffix in _INT_FIELDS + _FLOAT_FIELDS:
            edit = QLineEdit()
            current = getattr(existing, attribute, None) if existing else None
            if current is not None:
                edit.setText(str(current))
            if suffix:
                edit.setPlaceholderText(suffix)
            self._edits[attribute] = edit
            form.addRow(label, edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_performance(self) -> ClipPerformance:
        performance = ClipPerformance(
            clip_id=self.clip_id,
            platform=self.platform_edit.text().strip(),
            published_at=self.published_edit.text().strip(),
            recorded_at=date.today().isoformat(),
        )
        for attribute, _label, _suffix in _INT_FIELDS:
            setattr(performance, attribute, _parse_number(self._edits[attribute].text(), int))
        for attribute, _label, _suffix in _FLOAT_FIELDS:
            setattr(performance, attribute, _parse_number(self._edits[attribute].text(), float))
        return performance
