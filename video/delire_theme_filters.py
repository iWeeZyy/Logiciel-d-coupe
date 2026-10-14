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
from pathlib import Path

from editing.delire import THEME_MYSTERE
from video.delire_theme import ANCHOR_BOTTOM, ANCHOR_TOP, FIT_COVER, FIT_TILE, asset_path, layers_for


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
    # Mosaique ("tile") : un point de depart (s) par copie, lue comme une
    # ENTREE ffmpeg distincte. Un seul flux decoupe en copies decalees par
    # `split`+`trim` obligerait ffmpeg a garder en memoire toutes les images
    # du decalage (des centaines d'images RGBA en 1080p) -- plusieurs
    # lectures du meme fichier, chacune avec son -ss, n'ont pas ce cout.
    tile_offsets: tuple = ()
    tile_copy: str = ""      # chaine appliquee a chaque copie
    tile_tail: str = ""      # apres l'empilement (rognage, opacite)

    def input_args(self) -> list:
        if self.tile_offsets:
            args = []
            for offset in self.tile_offsets:
                args += ["-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", self.asset_path]
            return args
        if self.is_video:
            return ["-stream_loop", "-1", "-i", self.asset_path]
        return ["-i", self.asset_path]

    def tiled_graph(self, first_index: int, tag: str) -> str:
        """Sous-graphe de la mosaique, entrees `first_index`.. : chaque copie
        preparee (une sur deux en miroir), empilees, rognees au cadre. Sans
        etiquette de sortie : le compositeur ajoute la sienne."""
        labels, parts = [], []
        for k in range(len(self.tile_offsets)):
            flip = ",hflip" if k % 2 else ""
            parts.append(f"[{first_index + k}:v]{self.tile_copy}{flip}[{tag}t{k}]")
            labels.append(f"[{tag}t{k}]")
        parts.append(f"{''.join(labels)}vstack=inputs={len(labels)},{self.tile_tail}")
        return ";".join(parts)


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


def tile_count(layer, out_w: int, out_h: int) -> int:
    """Copies empilees pour remplir la hauteur : le nombre ENTIER le plus
    proche (3 en 1080x1920, et non 4 -- chaque copie est alors agrandie de
    5 % au lieu d'en decoder une quatrieme) ; 1 en 16:9 (cadre deja plein)."""
    tile_h = out_w / (layer.aspect or 16 / 9)
    return max(1, round(out_h / tile_h))


def _tiled(layer, out_w: int, out_h: int) -> PreparedOverlay | None:
    copies = tile_count(layer, out_w, out_h)
    if copies < 2:
        return None
    chroma = (f"chromakey=color={layer.chroma_color}"
              f":similarity={layer.chroma_similarity}:blend={layer.chroma_blend}")
    # Chaque copie remplit sa case (largeur du cadre x 1/N de la hauteur)
    # sans deformation : mise a l'echelle par exces puis rognage centre.
    cell_h = -(-out_h // copies)
    cell_h += cell_h % 2
    tail = [f"crop={out_w}:{out_h}:0:0"]
    if layer.opacity < 1.0:
        tail.append(f"colorchannelmixer=aa={layer.opacity:.3f}")
    loop = layer.loop_s or 10.0
    proxy = tile_proxy_path(layer, out_w)
    return PreparedOverlay(
        asset_path=str(proxy if proxy.is_file() else asset_path(layer.filename)),
        is_video=True, prep_filter="",
        tile_offsets=tuple(round(k * loop / copies, 2) for k in range(copies)),
        tile_copy=(f"scale={out_w}:{cell_h}:force_original_aspect_ratio=increase,"
                   f"crop={out_w}:{cell_h},{chroma},format=rgba"),
        tile_tail=",".join(tail))


# ------------------------------------------------ copie reduite (mosaique)
#
# Une mosaique lit la video du theme 3 fois en parallele. Les rushes font
# 2560x1440 : les decoder trois fois triplait presque le temps d'export
# (mesure : 12,7 s -> 36 s pour 5 s de clip 1080x1920 sur 4 coeurs). Une
# copie deja a la largeur du clip, faite une fois et gardee dans le dossier
# de l'utilisateur, ramene ce cout a peu de chose. Sans elle (premier export
# en echec, disque plein), le rush d'origine est lu : plus lent, meme image.

_PROXY_DIR = "delire_cache"


def tile_proxy_path(layer, out_w: int) -> Path:
    from core.paths import user_data_dir

    return user_data_dir() / _PROXY_DIR / f"{Path(layer.filename).stem}_{out_w}w.mp4"


def proxy_args(source: str, out_w: int, out_path: str) -> list:
    """Copie sans son, a la largeur `out_w`, assez peu compressee pour que le
    detourage du fond vert reste net. Fonction pure."""
    return ["-i", source, "-an", "-vf", f"scale={out_w}:-2", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", out_path]


def prepare_tile_sources(theme: str, out_w: int, out_h: int, cancel_token=None) -> None:
    """Cree les copies reduites dont la mosaique de ce theme a besoin, si
    elles manquent. Jamais d'exception (sauf annulation) : sans copie, le
    rush d'origine sert."""
    from core.logging_setup import get_logger
    from utils.errors import CancelledError
    from video.ffmpeg_utils import run_ffmpeg

    for layer in layers_for(theme):
        if layer.fit != FIT_TILE or tile_count(layer, out_w, out_h) < 2:
            continue
        target = tile_proxy_path(layer, out_w)
        if target.is_file():
            continue
        partial = target.with_name(target.stem + ".part.mp4")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            run_ffmpeg(proxy_args(str(asset_path(layer.filename)), out_w, str(partial)),
                       f"preparation du theme {theme}", cancel_token=cancel_token)
            partial.replace(target)
        except CancelledError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001 -- le rush d'origine reste utilisable
            partial.unlink(missing_ok=True)
            get_logger().warning(f"Copie reduite du theme {theme} impossible : {e}")


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

    prepared = []
    for layer in layers_for(theme):
        tiled = _tiled(layer, out_w, out_h) if layer.fit == FIT_TILE else None
        prepared.append(tiled or PreparedOverlay(
            asset_path=str(asset_path(layer.filename)),
            is_video=True,
            prep_filter=_prep_filter(layer, out_w, out_h),
        ))
    return prepared
