"""Les trois gabarits de Story demandes -- une declaration de donnees pure,
consommee par story_composer.py et par le menu deroulant du dialogue GUI
(gui/radar/story_dialog.py, tache #33). Volontairement pas de logique de
dessin ici : seulement CE que chaque gabarit affiche, pas COMMENT (qui reste
dans story_composer.py, la seule chose qui touche a Pillow).
"""
from __future__ import annotations

from dataclasses import dataclass

TEMPLATE_IMAGE = "image"
TEMPLATE_NEWS = "news"
TEMPLATE_BREAKING = "breaking"


@dataclass(frozen=True)
class TemplateSpec:
    """Ce qu'un gabarit affiche. `title_max_chars` borne le titre raccourci
    (title_shortener.build_display_title) -- BREAKING reste le plus court
    des deux, mais les deux budgets sont desormais tres larges : NE JAMAIS
    OMETTRE DE MOT prime sur "titre court" (demande explicite -- un titre
    RSS gaming reel depasse rarement 150-200 caracteres, un texte plus grand
    ou sur plus de lignes est prefere a une info manquante).

    Les deux valeurs restent EN DESSOUS de la capacite reelle d'affichage
    (mesuree par un rendu Pillow reel avec video/text_render.fit_font_for_lines
    a la taille de police minimale de chaque gabarit, voir story_composer._TITLE_SIZES :
    un titre de ~290 caracteres tient deja en 5-6 lignes a 24px) -- au-dela
    de ce plafond tres genereux, fit_font_for_lines() signale la coupure par
    une ellipse plutot que de la faire silencieusement, voir son docstring."""

    key: str
    label: str
    show_title: bool
    show_badge: bool
    title_max_chars: int


TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(key=TEMPLATE_IMAGE, label="Image", show_title=False, show_badge=False, title_max_chars=0),
    TemplateSpec(key=TEMPLATE_NEWS, label="News", show_title=True, show_badge=False, title_max_chars=300),
    TemplateSpec(key=TEMPLATE_BREAKING, label="Breaking", show_title=True, show_badge=True, title_max_chars=200),
)

_BY_KEY = {t.key: t for t in TEMPLATES}


def get_template(key: str) -> TemplateSpec:
    """Le gabarit demande, ou IMAGE par defaut si la cle est inconnue --
    jamais une exception pour une valeur venue d'un vieux fichier de
    preferences ou d'une faute de frappe dans un appel programmatique."""
    return _BY_KEY.get(key, _BY_KEY[TEMPLATE_IMAGE])
