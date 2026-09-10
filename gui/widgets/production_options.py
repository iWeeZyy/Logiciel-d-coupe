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
from video.filter_graph import FIT_CROP, FIT_WHOLE

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
    ("watermark", "Filigrane"),
]

LANDSCAPE_HINT = ("L'image d'origine est conservée : aucun recadrage, "
                  "donc pas de cadrage intelligent.")

WHOLE_HINT = ("L'image entière est conservée et centrée : le haut et le bas du "
              "cadre sont remplis. Aucun recadrage, donc pas de cadrage "
              "intelligent.")

# Comment faire tenir l'image dans un cadre vertical. Les deux ont un usage
# reel : recadrer suit le sujet et remplit l'ecran mais perd les bords ; garder
# l'image entiere ne perd rien et remplit le cadre avec un fond flou -- ce que
# Instagram et TikTok font sinon avec deux bandes noires.
FIT_CHOICES = [
    (FIT_CROP, "Recadrer sur le sujet (l'image est rognée)"),
    (FIT_WHOLE, "Image entière + fond de remplissage"),
]


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

        fit_row = QHBoxLayout()
        fit_label = QLabel("Cadrage")
        fit_label.setProperty("role", "fieldLabel")
        fit_row.addWidget(fit_label)
        self.fit_combo = QComboBox()
        for value, text in FIT_CHOICES:
            self.fit_combo.addItem(text, value)
        self.fit_combo.currentIndexChanged.connect(self._on_aspect_changed)
        fit_row.addWidget(self.fit_combo)
        fit_row.addStretch(1)
        layout.addLayout(fit_row)

        # Le fond flou remplit ce que l'image ne couvre pas : les bandes
        # laterales d'une source verticale envoyee en 16:9, ou les bandes haute
        # et basse d'une source horizontale gardee ENTIERE en 9:16. Il est
        # rempli avec une copie floutee de l'image elle-meme -- rien n'est
        # invente, rien n'est rogne. Sans objet quand on recadre en 9:16 : le
        # recadrage remplit deja le cadre, et la case y est grisee.
        self.blur_check = QCheckBox("Fond flou au lieu des bandes noires")
        self.blur_check.setChecked(True)
        layout.addWidget(self.blur_check)

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

    def fill_mode(self) -> str:
        """« flou » ou « noir ». Sans effet quand on recadre en 9:16."""
        return "flou" if self.blur_check.isChecked() else "noir"

    def fit_mode(self) -> str:
        """« recadrer » ou « entier ». Sans effet en 16:9, ou une source plus
        etroite est de toute facon gardee entiere."""
        return self.fit_combo.currentData() or FIT_CROP

    def keeps_whole_image(self) -> bool:
        """Vrai quand aucun recadrage n'aura lieu, quel que soit le format."""
        return not self.is_portrait() or self.fit_mode() == FIT_WHOLE

    def is_portrait(self) -> bool:
        return self.aspect() == ASPECT_PORTRAIT

    def editing_overrides(self) -> dict:
        """Modules actifs pour ce traitement.

        Le cadrage intelligent est force a l'arret en paysage : la case est
        grisee, mais renvoyer sa valeur cochee laisserait le pipeline croire
        qu'on la lui demande.
        """
        overrides = {key: box.isChecked() for key, box in self.boxes.items()}
        if self.keeps_whole_image():
            overrides["framing"] = False
        return overrides

    def set_module_enabled(self, key: str, enabled: bool) -> None:
        box = self.boxes.get(key)
        if box is not None:
            box.setChecked(enabled)

    # ------------------------------------------------------------- interne
    def _on_aspect_changed(self) -> None:
        """Rien a cadrer quand l'image entiere est gardee, et rien a remplir
        quand elle est recadree.

        Les commandes concernees restent visibles mais deviennent inoperantes :
        les griser et le dire est plus honnete que de les laisser cochees sans
        effet.
        """
        portrait = self.is_portrait()
        whole = self.keeps_whole_image()

        self.fit_combo.setEnabled(portrait)
        if whole:
            self.hint.setText(WHOLE_HINT if portrait else LANDSCAPE_HINT)
        else:
            self.hint.setText("")
        self.hint.setVisible(whole)

        box = self.boxes.get("framing")
        if box is not None:
            box.setEnabled(not whole)
        # Le remplissage ne sert que si quelque chose reste a remplir.
        self.blur_check.setEnabled(whole)
