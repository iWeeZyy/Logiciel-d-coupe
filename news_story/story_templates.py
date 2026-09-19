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
    (title_shortener.build_display_title) -- BREAKING est volontairement le
    plus court : la spec demande un titre "tres court" pour ce gabarit, les
    deux autres un titre "court"."""

    key: str
    label: str
    show_title: bool
    show_badge: bool
    title_max_chars: int


TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(key=TEMPLATE_IMAGE, label="Image", show_title=False, show_badge=False, title_max_chars=0),
    TemplateSpec(key=TEMPLATE_NEWS, label="News", show_title=True, show_badge=False, title_max_chars=90),
    TemplateSpec(key=TEMPLATE_BREAKING, label="Breaking", show_title=True, show_badge=True, title_max_chars=48),
)

_BY_KEY = {t.key: t for t in TEMPLATES}


def get_template(key: str) -> TemplateSpec:
    """Le gabarit demande, ou IMAGE par defaut si la cle est inconnue --
    jamais une exception pour une valeur venue d'un vieux fichier de
    preferences ou d'une faute de frappe dans un appel programmatique."""
    return _BY_KEY.get(key, _BY_KEY[TEMPLATE_IMAGE])
