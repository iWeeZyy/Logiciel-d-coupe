"""Traduire un theme du montage delire en filtres ffmpeg.

MODULE PUR, meme famille que video/delire_filters.py (qui traduit les
rafales) : aucun appel a ffmpeg ici, seulement des chaines de caracteres et
des chemins verifiables sans encoder une seule image.

DEUX FAMILLES DE THEMES, DEUX MECANISMES :

- "mystere" ne pose AUCUN calque : `mystere_chain_filters()` renvoie des
  filtres a ajouter DIRECTEMENT a la chaine video existante (meme point
  d'insertion que les rafales de video/delire_filters.py, voir
  `_delire_filters` dans video/filter_graph.py) -- assombrissement des bords
  (`vignette`) et legere desaturation (`eq`), tous deux natifs a ffmpeg et
  acceptant l'option `enable`. Aucune image a charger, aucune entree
  supplementaire.

- Les quatre autres (pluie, etoiles, confettis, braises) posent un ou
  plusieurs CALQUES -- une texture chargee comme une ENTREE SUPPLEMENTAIRE et
  superposee via `overlay`, exactement comme le filigrane (video/watermark.py)
  l'est deja. `overlay_layers()` renvoie, pour un theme donne, la liste des
  calques PRETS A ETRE BRANCHES : chacun porte son chemin de fichier, s'il a
  besoin d'etre boucle (`-loop 1`, pour que `scroll` ait plusieurs images
  differentes a faire defiler -- une image fixe n'en donnerait qu'une seule,
  qu'`overlay` repeterait alors telle quelle) et la chaine de filtres qui le
  prepare (mise a l'echelle, opacite, defilement). C'est
  video/filter_graph.py qui sait ENSUITE brancher ces calques sur la chaine
  ffmpeg -- ce module ne le fait jamais lui-meme.
"""
from __future__ import annotations

from dataclasses import dataclass

from editing.delire import THEME_MYSTERE
from video.delire_theme import asset_path, layers_for, scroll_vertical_for_fps


def mystere_chain_filters() -> list:
    """Assombrissement des bords et legere desaturation, pour toute la duree
    du clip -- aucune option `enable` : un theme est une ambiance continue,
    pas un evenement ponctuel comme les rafales.

    `vignette` seul (sans parametre d'angle/rayon particulier) donne deja
    l'effet recherche : un centre net, des bords qui s'enfoncent dans le
    noir. `eq=saturation=` reste PROCHE de 1 (0.75) -- une desaturation
    complete rendrait le clip gris et illisible, l'idee est d'assourdir les
    couleurs, pas de les supprimer.
    """
    return ["vignette", "eq=saturation=0.75:brightness=-0.02"]


@dataclass(frozen=True)
class PreparedOverlay:
    """Un calque pret a etre branche par video/filter_graph.py.

    `needs_loop` distingue les deux mecanismes possibles pour une image fixe
    en entree ffmpeg : sans boucle, une seule image, tenue telle quelle
    pendant tout le clip (le filigrane) ; avec boucle, un flux continu
    d'images identiques que `scroll` peut alors faire glisser."""

    asset_path: str
    needs_loop: bool
    prep_filter: str
    # Toujours "0:0" pour un theme (la texture couvre deja tout le cadre,
    # voir tools/generate_delire_assets.py) -- distinct du filigrane, dont la
    # position se choisit dans les Parametres.
    position: str = "0:0"


def overlay_layers(theme: str, out_w: int, out_h: int, fps: float) -> list:
    """Les calques d'un theme, DANS L'ORDRE DE SUPERPOSITION, ou une liste
    vide si le theme n'en porte aucun ("mystere", ou un theme inconnu/vide).

    Chaque calque est mis a l'echelle de sortie AVANT d'etre boucle-defile :
    `scroll` deplace le calque d'une fraction de SA PROPRE hauteur, donc la
    mise a l'echelle doit avoir deja eu lieu pour que cette fraction
    corresponde au cadre final, pas a la taille source de la texture.
    """
    if theme == THEME_MYSTERE:
        return []

    out = []
    for layer in layers_for(theme):
        vertical = scroll_vertical_for_fps(layer.scroll_vertical, fps)
        needs_loop = vertical != 0.0
        chain = [f"scale={out_w}:{out_h}", "format=rgba",
                 f"colorchannelmixer=aa={layer.opacity:.3f}"]
        if needs_loop:
            # scroll= sans enable : le defilement court sur toute la duree du
            # calque, qui EST celle du clip (voir needs_loop plus haut -- un
            # calque statique, comme l'arc-en-ciel, n'a justement pas besoin
            # d'etre boucle pour etre tenu tout du long).
            chain.append(f"scroll=vertical={vertical:.6f}")
        out.append(PreparedOverlay(
            asset_path=str(asset_path(layer.filename)),
            needs_loop=needs_loop,
            prep_filter=",".join(chain),
        ))
    return out
