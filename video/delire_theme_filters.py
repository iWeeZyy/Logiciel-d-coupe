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
  acceptant l'option `enable`. Aucune video a charger, aucune entree
  supplementaire.

- Les neuf autres posent chacun une VRAIE VIDEO fond vert comme ENTREE
  SUPPLEMENTAIRE, bouclee via `-stream_loop -1` (la video source dure
  rarement aussi longtemps que le clip -- la boucler la fait couvrir toute sa
  duree, exactement comme `-t` en aval borne deja la sortie) et superposee
  via `overlay`, meme mecanisme que le filigrane (video/watermark.py).
  `overlay_layers()` renvoie, pour un theme donne, la liste des calques PRETS
  A ETRE BRANCHES -- chacun porte son chemin de fichier et la chaine de
  filtres qui le prepare (fond vert retire, mis a l'echelle, opacite). C'est
  video/filter_graph.py qui sait ENSUITE brancher ces calques sur la chaine
  ffmpeg -- ce module ne le fait jamais lui-meme.

LE CHROMAKEY : les neuf videos sont des rushes fond vert REELS (pas des
captures de studio calibrees), donc leur vert differe legerement d'un
tournage a l'autre -- mesure PAR CLIP (PIL sur une frame extraite en PNG sans
perte, mode statistique sur l'image entiere), de #00CA00 (fleurs, tres
sature) a #12850F (flammes, plus sombre) ; chaque `ThemeLayer` porte donc ses
propres `chroma_color`/`chroma_similarity`/`chroma_blend` (video/delire_theme.py),
il n'existe plus de reglage partage entre les neuf.

Une PREMIERE version utilisait une couleur/tolerance MOYENNE partagee entre
les neuf themes (avec un `blend` genereux, 0.08). Un rendu de "chimpanzee"
compose sur un fond de test bariole a revele un bug : le pelage sombre du
chimpanze devenait PARTIELLEMENT TRANSPARENT sur toute sa surface (pas
seulement ses bords), laissant les couleurs du fond y transparaitre. Isole en
comparant un rendu sans chromakey (opaque, prouvant que le probleme venait
bien de ce filtre) puis un rendu avec chromakey a `blend=0` (a nouveau
parfaitement opaque) : le parametre `blend` de `chromakey` applique un
degrade de transparence a TOUT pixel dont la distance chromatique a la
couleur cle est dans la plage [similarity, similarity+blend] -- pas
uniquement aux bords du sujet decoupe. Le pelage, bien que visuellement
sombre, a une composante chromatique legerement verdatre qui tombait dans
cette bande de transition. Sur un fond de test UNI (noir, rouge), cette
transparence partielle se fondait avec le fond et passait inapercue ; sur un
fond BARIOLE, elle devenait un bleed-through flagrant. D'ou : `chroma_blend`
reste desormais proche de 0 pour tous les themes (une coupure dure, sans
degrade etendu), et chaque theme garde sa couleur EXACTEMENT mesuree plutot
qu'une moyenne. Le cas "pluie" reste le plus delicat : les gouttes y sont
presque de la meme teinte que le fond (de l'eau translucide sur un fond vert
reste verdatre), donc meme bien reglee cette video ne donne qu'un effet TENU
-- limite physique du rush, pas un reglage a corriger indefiniment.
"""
from __future__ import annotations

from dataclasses import dataclass

from editing.delire import THEME_MYSTERE
from video.delire_theme import ANCHOR_BOTTOM, ANCHOR_TOP, FIT_COVER, asset_path, layers_for


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

    `is_video` dit au compositeur d'utiliser `-stream_loop -1` (une video de
    duree finie, bouclee pour couvrir tout le clip) plutot que `-loop 1`
    (une image fixe transformee en flux continu) -- seul le second cas
    existait avant que les themes ne deviennent des videos fond vert."""

    asset_path: str
    is_video: bool
    prep_filter: str
    # Toujours "0:0" pour un theme (le calque prepare occupe deja tout le
    # cadre de sortie, cover comme contain) -- distinct du filigrane, dont la
    # position se choisit dans les Parametres.
    position: str = "0:0"


def _prep_filter(layer, out_w: int, out_h: int) -> str:
    """La chaine de preparation d'UN calque : fond vert retire, mis a
    l'echelle dans le cadre de sortie sans jamais deformer l'image (voir
    video/delire_theme.py pour le choix cover/contain par theme), opacite
    appliquee en dernier."""
    chroma = (
        f"chromakey=color={layer.chroma_color}"
        f":similarity={layer.chroma_similarity}:blend={layer.chroma_blend}"
    )

    if layer.fit == FIT_COVER:
        # Echelle sur la HAUTEUR (toujours paire via scale=-2:H) puis rognage
        # horizontal centre : remplit tout le cadre, au prix des bords
        # lateraux -- convient a un sujet deja centre dans son rush.
        chain = [f"scale=-2:{out_h}", f"crop={out_w}:{out_h}", chroma, "format=rgba"]
    else:
        # Echelle sur la LARGEUR (toujours paire via scale=W:-2), aucun
        # rognage : `pad` complete la hauteur manquante par un bandeau
        # TRANSPARENT (color=black@0.0, pas noir opaque) plutot que de
        # perdre le moindre bord -- necessaire des qu'un element fixe
        # proche d'un bord du rush importe (le bandeau "LIVE" du theme
        # "infos", constate en testant "cover" dessus avant de corriger).
        if layer.anchor == ANCHOR_TOP:
            y_expr = "0"
        elif layer.anchor == ANCHOR_BOTTOM:
            y_expr = f"{out_h}-ih"
        else:
            y_expr = f"({out_h}-ih)/2"
        chain = [f"scale={out_w}:-2", chroma, f"pad={out_w}:{out_h}:0:{y_expr}:color=black@0.0", "format=rgba"]

    if layer.opacity < 1.0:
        chain.append(f"colorchannelmixer=aa={layer.opacity:.3f}")
    return ",".join(chain)


def overlay_layers(theme: str, out_w: int, out_h: int, fps: float) -> list:
    """Les calques d'un theme, DANS L'ORDRE DE SUPERPOSITION, ou une liste
    vide si le theme n'en porte aucun ("mystere", ou un theme inconnu/vide).

    `fps` n'est plus utilise ici (l'ancien mecanisme scroll+PNG en avait
    besoin pour compenser sa vitesse de defilement selon la cadence source ;
    une video fond vert n'a pas ce probleme, `overlay` gere deja des cadences
    d'entree differentes) -- garde dans la signature pour que
    video/filter_graph.py n'ait pas a distinguer ses appelants.
    """
    if theme == THEME_MYSTERE:
        return []

    return [
        PreparedOverlay(
            asset_path=str(asset_path(layer.filename)),
            is_video=True,
            prep_filter=_prep_filter(layer, out_w, out_h),
        )
        for layer in layers_for(theme)
    ]
