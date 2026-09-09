"""Options de production, partagees par l'accueil et le Radar.

Ces quatre choix -- format, sous-titres, cadrage intelligent, montage
automatique -- se font avant de lancer un traitement. Ils etaient sur la page
Accueil uniquement ; le Radar en a besoin aussi, puisqu'on peut y lancer une
production directement depuis un clip.

Un seul composant plutot que deux listes de cases : deux copies finiraient par
diverger, et une case presente d'un cote mais pas de l'autre serait un piege.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from video.cropper import ASPECT_LANDSCAPE, ASPECT_PORTRAIT

# Modules proposes a la coche. Source unique : l'accueil et le Radar lisent
# cette liste, ils n'en tiennent pas chacun la leur.
QUICK_MODULES = [
    ("context_detection", "Contexte"),
    ("framing", "Cadrage intelligent"),
    ("montage", "Montage auto"),
    ("captions", "Sous-titres"),
    # "Titres" et "Descriptions" sont deux cases distinctes dans la demande,
    # mais un seul module ici : editing/metadata.py les extrait ensemble, du
    # meme texte et du meme classement de phrases. Deux interrupteurs pour un
    # seul mecanisme donneraient une case sans effet propre.
    ("metadata", "Titres et descriptions"),
    ("thumbnails", "Miniatures"),
]

LANDSCAPE_HINT = ("L'image d'origine est conservée : aucun recadrage, "
                  "donc pas de cadrage intelligent.")


class ProductionOptionsBox(QWidget):
    """Format de sortie et modules actifs pour UN traitement."""

    def __init__(self, parent=None, columns: int = 0):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        format_row = QHBoxLayout()
        label = QLabel("Format")
        label.setProperty("role", "fieldLabel")
        format_row.addWidget(label)
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItem("9:16 — vertical (Reels, Shorts, TikTok)", ASPECT_PORTRAIT)
        self.aspect_combo.addItem("16:9 — horizontal (image d'origine)", ASPECT_LANDSCAPE)
        self.aspect_combo.currentIndexChanged.connect(self._on_aspect_changed)
        format_row.addWidget(self.aspect_combo)
        format_row.addStretch(1)
        layout.addLayout(format_row)

        self.hint = QLabel("")
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        layout.addWidget(self.hint)

        self.boxes: dict[str, QCheckBox] = {}
        # En colonne quand la place manque (une fenetre), en ligne sinon (une
        # page). Le meme composant sert aux deux, seule la disposition change.
        if columns > 0:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            for index, (key, text) in enumerate(QUICK_MODULES):
                box = QCheckBox(text)
                box.setChecked(True)
                self.boxes[key] = box
                grid.addWidget(box, index // columns, index % columns)
            layout.addLayout(grid)
        else:
            row = QHBoxLayout()
            for key, text in QUICK_MODULES:
                box = QCheckBox(text)
                box.setChecked(True)
                self.boxes[key] = box
                row.addWidget(box)
            row.addStretch(1)
            layout.addLayout(row)

        self._on_aspect_changed()

    # ------------------------------------------------------------ lecture
    def aspect(self) -> str:
        return self.aspect_combo.currentData()

    def is_portrait(self) -> bool:
        return self.aspect() == ASPECT_PORTRAIT

    def editing_overrides(self) -> dict:
        """Modules actifs pour ce traitement.

        Le cadrage intelligent est force a l'arret en paysage : la case est
        grisee, mais renvoyer sa valeur cochee laisserait le pipeline croire
        qu'on la lui demande.
        """
        overrides = {key: box.isChecked() for key, box in self.boxes.items()}
        if not self.is_portrait():
            overrides["framing"] = False
        return overrides

    def set_module_enabled(self, key: str, enabled: bool) -> None:
        box = self.boxes.get(key)
        if box is not None:
            box.setChecked(enabled)

    # ------------------------------------------------------------- interne
    def _on_aspect_changed(self) -> None:
        """En 16:9 l'image n'est pas recadree, donc rien a cadrer.

        La case reste visible mais devient inoperante : la griser et le dire est
        plus honnete que de la laisser cochee sans effet.
        """
        portrait = self.is_portrait()
        self.hint.setText("" if portrait else LANDSCAPE_HINT)
        self.hint.setVisible(not portrait)
        box = self.boxes.get("framing")
        if box is not None:
            box.setEnabled(portrait)
