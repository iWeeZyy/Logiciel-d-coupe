"""Video d'intro posee EN INCRUSTATION sur le debut du clip.

Remplace une premiere version (video/intro_concat.py, supprime) qui
concatenait l'intro comme une SCENE SEPAREE, jouee en entier avant le clip :
la demande etait de la superposer, en petit dans un coin, PENDANT que le clip
joue -- le clip reste visible en dessous et autour, seulement les toutes
premieres secondes.

Le contenu utile (l'anneau, le "+ Follow", le halo, les etincelles) bouge et
deborde du centre au fil de l'animation -- CROP_* est un cadre fixe, mesure
une fois sur l'asset livre (assets/branding/intro_follow.mp4, 1080x1920) en
analysant les pixels non-noirs sur plusieurs images de l'animation, assez
large pour contenir le mouvement entier sans jamais le rogner. A retuner si
l'asset change de composition.

LA TRANSPARENCE PASSE PAR UN MASQUE PRECALCULE (assets/branding/
intro_follow_mask.mp4, genere par tools/generate_intro_mask.py), pas par un
colorkey au rendu. Un colorkey supprimerait TOUT le noir sans distinction --
or le logo contient lui-meme du noir voulu (le disque a l'interieur de
l'anneau, sous "ClipsOfStreams") : un colorkey le rendait transparent en
meme temps que le fond, laissant voir le clip a travers le logo. Le masque,
lui, ne retire que le noir ATTEIGNABLE DEPUIS LE BORD par remplissage
(flood-fill, voir le script de generation) -- le disque interieur, entoure
par l'anneau neon, n'est jamais atteint et reste opaque.

LE MASQUE EST UNE VIDEO, PAS UNE IMAGE FIXE -- une image unique suffisait
tant que l'anneau ne bougeait pas, mais il GROSSIT depuis rien pendant la
premiere seconde environ de l'animation ; un masque fixe, construit sur une
image tardive (anneau pleinement etabli), continuait a marquer cette zone
comme opaque avant meme que l'anneau y soit dessine, produisant un aplat
noir plein cadre au debut de chaque clip (signale sur les sorties 9:16, ou
l'incrustation occupe une plus grande part du cadre -- le defaut existe en
realite dans tous les formats). tools/generate_intro_mask.py flood-fill
desormais CHAQUE image de intro_follow.mp4 individuellement et encode le
resultat en video, alignee image pour image avec elle -- alphamerge n'a pas
eu a changer, il prenait deja son second flux comme une source video
ordinaire.

Module PUR comme watermark.py et filter_graph.py : construit des morceaux de
filtre ffmpeg, ne lit ni ne decode aucune image. Les dimensions/duree reelles
de l'asset sont lues par l'appelant (clip_builder.py, via ffmpeg_utils) et
passees toutes faites dans IntroOverlay -- pas d'appel ffmpeg ici.
"""
from __future__ import annotations

from dataclasses import dataclass

# Cadre de rognage, mesure sur l'asset de reference (1080x1920) : mis a
# l'echelle proportionnellement si l'asset livre change un jour de definition.
# IDENTIQUE aux constantes de tools/generate_intro_mask.py -- le masque n'a de
# sens qu'aligne sur ce recadrage, les deux changent ensemble ou pas du tout.
_REF_W, _REF_H = 1080, 1920
_CROP_X, _CROP_Y, _CROP_W, _CROP_H = 40, 330, 1000, 1140

_MASK_ASSET = "branding/intro_follow_mask.mp4"

DEFAULT_SIZE_PERCENT = 35.0
DEFAULT_MARGIN_PERCENT = 4.0
# En haut a droite par defaut : le filigrane reste en bas au centre (voir
# watermark.py), donc rien ne se superpose entre les deux tant que l'un des
# deux ne change pas de coin explicitement.
DEFAULT_POSITION = "haut-droite"

_POSITIONS = ("haut-gauche", "haut-centre", "haut-droite",
              "bas-gauche", "bas-centre", "bas-droite")


@dataclass(frozen=True)
class IntroOverlay:
    """Une video d'intro prete a etre posee en incrustation, avec ses
    dimensions et sa duree reelles deja lues (voir docstring du module)."""

    path: str
    src_w: int
    src_h: int
    duration: float
    size_percent: float = DEFAULT_SIZE_PERCENT
    margin_percent: float = DEFAULT_MARGIN_PERCENT
    position: str = DEFAULT_POSITION


def crop_rect(src_w: int, src_h: int) -> tuple[int, int, int, int]:
    """Le cadre a rogner dans l'asset intro, adapte a sa vraie definition."""
    x = round(_CROP_X * src_w / _REF_W)
    y = round(_CROP_Y * src_h / _REF_H)
    w = round(_CROP_W * src_w / _REF_W)
    h = round(_CROP_H * src_h / _REF_H)
    return x, y, w, h


def mask_asset_path() -> str:
    """Chemin du masque livre avec l'application. Meme raisonnement que
    watermark.asset_path()/intro.asset_path() : passe par app_base_dir()."""
    from core.paths import app_base_dir

    return str(app_base_dir() / "assets" / _MASK_ASSET)


def scaled_size(intro: IntroOverlay, out_w: int, out_h: int) -> tuple[int, int]:
    """Dimensions finales de l'incrustation (largeur en pourcentage du plus
    petit cote de sortie, meme raisonnement que le filigrane ; hauteur
    calculee EXPLICITEMENT, jamais via un `-1` ffmpeg, pour que la video
    recadree et le masque soient mis a l'echelle sur EXACTEMENT la meme
    taille -- alphamerge exige des tailles pixel identiques image par
    image)."""
    _, _, w, h = crop_rect(intro.src_w, intro.src_h)
    width = max(2, int(round(min(out_w, out_h) * intro.size_percent / 100.0)))
    width -= width % 2
    height = max(2, int(round(width * h / w)))
    height -= height % 2
    return width, height


def prepare_filter(intro: IntroOverlay, out_w: int, out_h: int,
                    video_index: int, mask_index: int) -> str:
    """Sous-graphe complet posant la transparence sur l'incrustation : la
    video d'intro (entree `video_index`) est recadree sur son contenu utile
    puis mise a l'echelle ; le masque precalcule (entree `mask_index`) est
    mis a l'echelle sur la meme taille exacte et devient son canal alpha via
    `alphamerge`.

    Contrairement au filigrane, ce calque consomme DEUX entrees ffmpeg
    (video + masque) -- il ne peut donc pas rentrer dans le format "un
    filtre, une entree" du reste du compositeur a N calques ; les indices
    sont fournis tout faits par l'appelant (build_ffmpeg_args), qui sait
    combien d'entrees les calques precedents ont deja consommees.
    """
    x, y, w, h = crop_rect(intro.src_w, intro.src_h)
    width, height = scaled_size(intro, out_w, out_h)
    return (
        f"[{video_index}:v]crop={w}:{h}:{x}:{y},scale={width}:{height},format=rgba[introrgb];"
        f"[{mask_index}:v]scale={width}:{height},format=gray[introalpha];"
        f"[introrgb][introalpha]alphamerge"
    )


def overlay_position(intro: IntroOverlay, out_w: int, out_h: int) -> str:
    """Coordonnees de l'incrustation. Meme calcul que watermark.overlay_position
    (marge sur le plus petit cote de sortie) -- duplique plutot que reutilise :
    c'est huit lignes, et emprunter le type Watermark pour un objet qui n'en
    est pas un aurait ete plus trompeur que la duplication."""
    reference = min(out_w, out_h)
    margin = max(0, int(round(reference * intro.margin_percent / 100.0)))
    horizontal = {"gauche": f"{margin}", "centre": "(W-w)/2", "droite": f"W-w-{margin}"}
    vertical = {"haut": f"{margin}", "bas": f"H-h-{margin}"}
    position = intro.position if intro.position in _POSITIONS else DEFAULT_POSITION
    band, side = position.split("-")
    return f"{horizontal[side]}:{vertical[band]}"


def overlay_spec(intro: IntroOverlay, out_w: int, out_h: int,
                  video_index: int, mask_index: int) -> tuple[str, str, str]:
    """(sous-graphe de preparation, position, condition d'activation) -- pret
    a entrer dans le compositeur a N calques de build_ffmpeg_args.

    La condition limite l'incrustation aux `intro.duration` premieres
    secondes de SORTIE : passe cette duree, la video d'intro n'a plus
    d'image reelle a montrer (overlay figerait sa derniere image sinon), et
    le clip doit redevenir entierement visible.
    """
    filt = prepare_filter(intro, out_w, out_h, video_index, mask_index)
    position = overlay_position(intro, out_w, out_h)
    enable = f"between(t,0,{max(0.0, intro.duration):.3f})"
    return filt, position, enable
