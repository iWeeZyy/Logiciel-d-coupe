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

from editing import delire
from video import watermark
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
    ("delire", "Délire"),
]

# Les modules DECOCHES au demarrage. Tous les autres sont actifs par defaut,
# parce qu'ils ameliorent un clip ; le delire est un parti pris esthetique, pas
# une amelioration, et il n'a rien a faire sur un clip sobre.
_DEFAULT_OFF = {"delire"}

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


# Libelles des trois crans. Les cles viennent de editing/delire.py : c'est lui
# qui decide ce que chacun autorise, pas cette liste.
_DELIRE_LEVELS = [
    ("doux", "Doux — quelques glitchs discrets"),
    ("moyen", "Moyen — glitchs, saturation, éclairs"),
    ("maximum", "Maximum — tout, y compris pixels et inversions"),
]


def _watermark_config() -> dict:
    """La section « watermark » de config/editing.json, ou {}.

    Lue a la construction : un fichier illisible ne doit pas empecher la page
    de s'afficher, le menu se contente alors du repli du module."""
    try:
        from core.config_loader import _load_editing_config

        return (_load_editing_config() or {}).get("watermark") or {}
    except Exception:                                  # pragma: no cover
        return {}


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
                box.setChecked(key not in _DEFAULT_OFF)
                self.boxes[key] = box
                grid.addWidget(box, index // columns, index % columns)
            layout.addLayout(grid)
        else:
            row = QHBoxLayout()
            for key, text in QUICK_MODULES:
                box = QCheckBox(text)
                box.setChecked(key not in _DEFAULT_OFF)
                self.boxes[key] = box
                row.addWidget(box)
            row.addStretch(1)
            layout.addLayout(row)

        # --- quelle chaine signe la video ---
        # Le catalogue vient de config/editing.json : ajouter une chaine ne
        # demande pas de toucher a ce fichier. Une entree dont le PNG manque
        # est ecartee en amont, donc le menu ne propose jamais un logo qu'on ne
        # saurait pas poser.
        self.watermark_row = QHBoxLayout()
        watermark_label = QLabel("Filigrane")
        watermark_label.setProperty("role", "fieldLabel")
        self.watermark_row.addWidget(watermark_label)
        self.watermark_combo = QComboBox()
        for entry in watermark.choices(_watermark_config()):
            self.watermark_combo.addItem(entry["label"], entry["key"])
        self.watermark_row.addWidget(self.watermark_combo)
        self.watermark_row.addStretch(1)
        layout.addLayout(self.watermark_row)

        # --- intensite du delire ---
        # Trois crans, pas un curseur continu : chaque cran ouvre aussi de
        # NOUVEAUX effets, ce qu'un pourcentage ne saurait pas exprimer.
        self.delire_row = QHBoxLayout()
        delire_label = QLabel("Intensité du délire")
        delire_label.setProperty("role", "fieldLabel")
        self.delire_row.addWidget(delire_label)
        self.delire_combo = QComboBox()
        for niveau, libelle in _DELIRE_LEVELS:
            self.delire_combo.addItem(libelle, niveau)
        self.delire_combo.setCurrentIndex(
            max(0, self.delire_combo.findData(delire.DEFAULT_LEVEL)))
        self.delire_row.addWidget(self.delire_combo)
        self.delire_row.addStretch(1)
        layout.addLayout(self.delire_row)

        self.delire_hint = QLabel(
            "Des effets francs (glitch, saturation, VHS, pixels, éclairs) posés "
            "sur les moments forts, en nombre borné. La durée de la vidéo ne "
            "change pas : sous-titres et son restent synchrones.")
        self.delire_hint.setProperty("role", "muted")
        self.delire_hint.setWordWrap(True)
        layout.addWidget(self.delire_hint)

        delire_box = self.boxes.get("delire")
        if delire_box is not None:
            delire_box.toggled.connect(self._on_delire_toggled)
        self._on_delire_toggled(delire_box.isChecked() if delire_box else False)

        # Grise avec la case : un menu actif alors que le filigrane est coupe
        # laisserait croire qu'il sera pose.
        box = self.boxes.get("watermark")
        if box is not None:
            box.toggled.connect(self._on_watermark_toggled)
        self._on_watermark_toggled(box.isChecked() if box is not None else False)

        self._on_aspect_changed()

    def _on_delire_toggled(self, enabled: bool) -> None:
        """Le cran et son explication ne servent a rien si le delire est
        coupe : on les grise plutot que de les laisser actifs sans effet."""
        self.delire_combo.setEnabled(bool(enabled))
        self.delire_hint.setEnabled(bool(enabled))

    def delire_level(self) -> str:
        return str(self.delire_combo.currentData() or delire.DEFAULT_LEVEL)

    def set_delire_level(self, level: str) -> None:
        index = self.delire_combo.findData(level)
        if index >= 0:
            self.delire_combo.setCurrentIndex(index)

    def _on_watermark_toggled(self, enabled: bool) -> None:
        self.watermark_combo.setEnabled(bool(enabled)
                                        and self.watermark_combo.count() > 1)

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
        # Le filigrane porte DEUX informations : actif ou non, et lequel. D'ou
        # un dictionnaire la ou les autres modules se contentent d'un booleen
        # -- le controleur accepte les deux formes.
        choice = self.watermark_choice()
        if choice:
            overrides["watermark"] = {"enabled": bool(overrides.get("watermark")),
                                      "choice": choice}
        # Meme forme pour le delire : actif ou non, et a quel cran.
        overrides["delire"] = {"enabled": bool(overrides.get("delire")),
                               "level": self.delire_level()}
        return overrides

    def watermark_choice(self) -> str:
        """Cle de la chaine choisie, ou "" si le catalogue est vide."""
        return str(self.watermark_combo.currentData() or "")

    def set_watermark_choice(self, key: str) -> None:
        index = self.watermark_combo.findData(key)
        if index >= 0:
            self.watermark_combo.setCurrentIndex(index)

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
