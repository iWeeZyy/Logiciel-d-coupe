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
TEMPLATE_POST = "post"


@dataclass(frozen=True)
class TemplateSpec:
    """Ce qu'un gabarit affiche. `title_max_chars` borne le texte raccourci
    (title_shortener.build_display_title) -- desormais le TITRE ET LE RESUME
    combines quand l'article en fournit un (le titre seul est souvent un
    teaser sans l'information elle-meme -- demande explicite de
    l'utilisateur). BREAKING reste le plus court des deux, mais les deux
    budgets restent larges : NE JAMAIS OMETTRE DE MOT prime sur "texte
    court".

    story_composer._draw_title() n'est plus borne par un nombre de lignes
    fixe (voir son _available_title_lines) : il utilise tout l'espace
    vertical reellement disponible sur le canvas, donc ces plafonds ne sont
    plus contraints par une capacite d'affichage mesuree a l'avance -- ils
    restent la pour garder le texte "compacte" (demande explicite) plutot
    que d'afficher un resume de flux dans son integralite."""

    key: str
    label: str
    show_title: bool
    show_badge: bool
    title_max_chars: int
    # Format de sortie. Les Stories sont en 9:16 ; POST est un post de fil
    # Instagram en 4:5 (1080x1350), le format portrait le plus grand que le
    # fil affiche sans le rogner.
    canvas_size: tuple[int, int] = (1080, 1920)
    # POST n'affiche que le TITRE, court et percutant : le detail de la news
    # va dans la legende du post (news_story/caption.py), pas sur l'image --
    # le format des comptes d'actu qui l'ont demande.
    title_uses_summary: bool = True


TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(key=TEMPLATE_IMAGE, label="Image", show_title=False, show_badge=False, title_max_chars=0),
    TemplateSpec(key=TEMPLATE_NEWS, label="News", show_title=True, show_badge=False, title_max_chars=550),
    TemplateSpec(key=TEMPLATE_BREAKING, label="Breaking", show_title=True, show_badge=True, title_max_chars=400),
    TemplateSpec(key=TEMPLATE_POST, label="Post 4:5", show_title=True, show_badge=False,
                 title_max_chars=160, canvas_size=(1080, 1350), title_uses_summary=False),
)

_BY_KEY = {t.key: t for t in TEMPLATES}


def get_template(key: str) -> TemplateSpec:
    """Le gabarit demande, ou IMAGE par defaut si la cle est inconnue --
    jamais une exception pour une valeur venue d'un vieux fichier de
    preferences ou d'une faute de frappe dans un appel programmatique."""
    return _BY_KEY.get(key, _BY_KEY[TEMPLATE_IMAGE])
