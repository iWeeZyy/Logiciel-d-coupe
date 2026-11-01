"""Video d'intro posee EN INCRUSTATION sur le debut du clip.

Remplace une premiere version (video/intro_concat.py, supprime) qui
concatenait l'intro comme une SCENE SEPAREE, jouee en entier avant le clip :
la demande etait de la superposer, en petit dans un coin, PENDANT que le clip
joue -- le clip reste visible en dessous et autour, seulement les toutes
premieres secondes.

Le contenu utile (l'anneau, la pastille "Follow", le curseur, le halo)
tient dans CROP_*, un cadre fixe de l'asset livre (assets/branding/
intro_follow.mp4, 1080x1920). tools/build_intro_asset.py dessine toute
l'animation a l'interieur de ce cadre -- les deux changent ensemble.

LA TRANSPARENCE PASSE PAR UN MASQUE PRECALCULE (assets/branding/
intro_follow_mask.mp4, au format du cadre, aligne image pour image avec la
video), pas par un colorkey au rendu : le logo contient du noir voulu (le
disque sous "ClipsOfStreams") qu'un colorkey rendrait transparent. Le
masque est desormais l'alpha EXACT de l'animation, calcule par
tools/build_intro_asset.py depuis une source sur fond vert pur -- il
remplace le detourage par remplissage depuis les bords de l'ancienne source
a fond noir, qui laissait un aplat noir autour du medaillon sur les clips
9:16.

Module PUR comme watermark.py et filter_graph.py : construit des morceaux de
filtre ffmpeg, ne lit ni ne decode aucune image. Les dimensions/duree reelles
de l'asset sont lues par l'appelant (clip_builder.py, via ffmpeg_utils) et
passees toutes faites dans IntroOverlay -- pas d'appel ffmpeg ici.
"""
from __future__ import annotations

from dataclasses import dataclass

# Cadre de rognage, mesure sur l'asset de reference (1080x1920) : mis a
# l'echelle proportionnellement si l'asset livre change un jour de definition.
# IDENTIQUE aux constantes de tools/build_intro_asset.py -- le masque n'a de
# sens qu'aligne sur ce recadrage, les deux changent ensemble ou pas du tout.
_REF_W, _REF_H = 1080, 1920
_CROP_X, _CROP_Y, _CROP_W, _CROP_H = 40, 330, 1000, 1140

_MASK_ASSET = "branding/intro_follow_mask.mp4"

# Plus petit qu'a l'origine (35 %) et descendu sous le haut de l'ecran
# (retour utilisateur, capture TikTok a l'appui) : colle au bord superieur,
# le medaillon passait sous la barre de TikTok (onglets « Pour toi », loupe,
# barre de recherche) et sous le titre que l'utilisateur pose dans TikTok
# vers 18-27 % de la hauteur. La place libre a droite va de ~28 % (sous ce
# titre) a ~46 % (debut de la colonne de boutons : avatar, j'aime...) :
# ~330 px en 1080x1920, medaillon ET phrase d'accroche compris -- d'ou 25 %.
# Marge laterale 6 % : un ecran plus allonge que 9:16 peut rogner les cotes.
DEFAULT_SIZE_PERCENT = 25.0
DEFAULT_MARGIN_PERCENT = 6.0
# Haut du cadre de l'incrustation, en % de la hauteur, en sortie VERTICALE
# seulement (les zones de TikTok n'existent pas en 16:9) ; le dessin commence
# ~6 % plus bas dans ce cadre.
DEFAULT_TOP_PERCENT = 26.5
# A droite : le filigrane reste en bas au centre (voir watermark.py), donc
# rien ne se superpose entre les deux tant que l'un des deux ne change pas de
# coin explicitement.
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
    top_percent: float = DEFAULT_TOP_PERCENT
    # Phrase d'accroche deja dessinee (PNG transparent plein cadre, voir
    # video/intro_text.py) ; "" = pas de phrase.
    text_png: str = ""


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


def _top(intro: IntroOverlay, out_w: int, out_h: int, margin: int) -> int:
    """Ordonnee du haut de l'incrustation placee en haut : sous les zones de
    TikTok en vertical, a la marge en 16:9 ou en carre."""
    if out_h > out_w and intro.top_percent:
        return int(round(out_h * intro.top_percent / 100.0))
    return margin


def overlay_position(intro: IntroOverlay, out_w: int, out_h: int) -> str:
    """Coordonnees de l'incrustation. Meme calcul que watermark.overlay_position
    (marge sur le plus petit cote de sortie) -- duplique plutot que reutilise :
    c'est huit lignes, et emprunter le type Watermark pour un objet qui n'en
    est pas un aurait ete plus trompeur que la duplication."""
    reference = min(out_w, out_h)
    margin = max(0, int(round(reference * intro.margin_percent / 100.0)))
    horizontal = {"gauche": f"{margin}", "centre": "(W-w)/2", "droite": f"W-w-{margin}"}
    vertical = {"haut": f"{_top(intro, out_w, out_h, margin)}", "bas": f"H-h-{margin}"}
    position = intro.position if intro.position in _POSITIONS else DEFAULT_POSITION
    band, side = position.split("-")
    return f"{horizontal[side]}:{vertical[band]}"


def overlay_rect(intro: IntroOverlay, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """(x, y, largeur, hauteur) de l'incrustation, en pixels -- le meme
    placement que overlay_position(), calcule en nombres pour y aligner la
    phrase d'accroche."""
    width, height = scaled_size(intro, out_w, out_h)
    margin = max(0, int(round(min(out_w, out_h) * intro.margin_percent / 100.0)))
    position = intro.position if intro.position in _POSITIONS else DEFAULT_POSITION
    band, side = position.split("-")
    x = {"gauche": margin, "centre": (out_w - width) // 2, "droite": out_w - width - margin}[side]
    y = _top(intro, out_w, out_h, margin) if band == "haut" else out_h - height - margin
    return x, y, width, height


TEXT_FADE_IN = 0.3
TEXT_FADE_OUT = 0.4


def text_overlay_spec(intro: IntroOverlay) -> tuple[list[str], str, str, str]:
    """(entree, filtre, position, condition) du calque de la phrase : l'image
    deja dessinee, en fondu d'entree et de sortie sur la duree de l'intro."""
    duration = max(TEXT_FADE_IN, intro.duration)
    out_start = max(0.0, duration - TEXT_FADE_OUT)
    filt = (f"format=rgba,fade=t=in:st=0:d={TEXT_FADE_IN}:alpha=1,"
            f"fade=t=out:st={out_start:.3f}:d={TEXT_FADE_OUT}:alpha=1")
    return (["-loop", "1", "-t", f"{duration:.3f}", "-i", intro.text_png], filt, "0:0",
            f"between(t,0,{duration:.3f})")


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


def prepare(intro, out_w: int, out_h: int, workdir) -> "IntroOverlay | None":
    """L'incrustation prete a poser (dimensions, duree, phrase dessinee dans
    `workdir`) pour une intro de config (video/intro.py), ou None si elle
    manque. Partagee par le rendu du clip (video/clip_builder.py) et par la
    passe du texte en haut (video/hook_text.py), qui doit poser l'intro
    PAR-DESSUS le bandeau."""
    from dataclasses import replace
    from pathlib import Path

    from video.ffmpeg_utils import video_duration, video_resolution

    if intro is None or not intro.exists:
        return None
    intro_w, intro_h = video_resolution(intro.video)
    overlay = IntroOverlay(path=intro.video, src_w=intro_w, src_h=intro_h,
                           duration=video_duration(intro.video))
    text = getattr(intro, "text", "")
    if text:
        from video.intro_text import render_intro_text

        png = Path(workdir) / "intro_text.png"
        try:
            render_intro_text(overlay, text, out_w, out_h, png)
            overlay = replace(overlay, text_png=str(png))
        except Exception as error:  # noqa: BLE001 -- l'intro sort sans la phrase
            from core.logging_setup import get_logger

            get_logger().warning(f"Phrase de l'intro non dessinee : {error}")
    return overlay


def layers(overlay: "IntroOverlay", out_w: int, out_h: int, first_index: int):
    """[(arguments d'entree, sous-graphe prepare, position, condition), ...]
    pour poser l'intro (et sa phrase) dans un graphe dont la prochaine entree
    libre est `first_index`. Les sous-graphes referencent deja leurs
    entrees ; chacun sort sous l'etiquette [introN]."""
    out = []
    filt, position, enable = overlay_spec(overlay, out_w, out_h, first_index, first_index + 1)
    out.append((["-i", overlay.path, "-i", mask_asset_path()], f"{filt}[intro0]", position,
                enable))
    if overlay.text_png:
        inputs, text_filt, text_position, text_enable = text_overlay_spec(overlay)
        out.append((inputs, f"[{first_index + 2}:v]{text_filt}[intro1]", text_position,
                    text_enable))
    return out
