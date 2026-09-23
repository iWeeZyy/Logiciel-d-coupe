"""Composition de la chaine de filtres ffmpeg d'un clip, en UN SEUL encodage.

Assemble, dans cet ordre : montage (trim/concat) -> cadrage suivi (crop a
expression temporelle) -> zoom dynamique (zoompan) -> mise a l'echelle 9:16 ->
sous-titres incrustes -> traitement audio. Un seul passage d'encodage, donc
aucune perte de qualite due a des re-encodages successifs.

Deux points de vigilance qui expliquent la forme du code :

- **Tout est exprime en temps de SORTIE.** Des qu'un silence est coupe, la
  timeline change ; les trajectoires de cadrage et de zoom sont donc converties
  via l'EditList avant d'etre transformees en expressions. C'est exactement ce
  a quoi sert editing/timeline.py.
- **La taille d'un `crop` ne peut pas etre animee par ffmpeg** (seules `x` et
  `y` acceptent une expression evaluee image par image). Le suivi du sujet
  passe donc par `crop`, et le zoom -- qui fait varier la surface prise -- par
  `zoompan`, seul filtre capable de faire varier un facteur d'agrandissement au
  fil du temps. Les deux ne sont chaines que quand un zoom existe vraiment.
"""
from __future__ import annotations

from editing.delire import THEME_MYSTERE
from editing.framing import FramingPlan
from editing.timeline import EditList
from editing.zoom import ZoomTrack
from video.cropper import TARGET_H, TARGET_W, CenterHint, CropRect, compute_crop_rect
from video.subtitle_renderer import subtitle_filter

_TARGET_ASPECT = TARGET_W / TARGET_H


def _even(n: int) -> int:
    n = int(n)
    return n - (n % 2)


def base_crop_size(src_w: int, src_h: int, target_aspect: float = _TARGET_ASPECT) -> tuple[int, int]:
    """Plus grande fenetre a `target_aspect` (largeur/hauteur) tenant dans
    l'image source -- 9:16 par defaut. Le mode portrait webcam+gameplay
    (FIT_SPLIT_WEBCAM) passe l'aspect propre a chaque bande (bien plus large
    que haute pour la webcam, bien plus haute que large pour le jeu) : c'est
    la meme geometrie, appliquee a un rectangle different."""
    if src_w / src_h > target_aspect:
        return _even(min(src_w, round(src_h * target_aspect))), _even(src_h)
    return _even(src_w), _even(min(src_h, round(src_w / target_aspect)))


def piecewise_expression(points: list[tuple[float, float]], variable: str = "t") -> str:
    """Expression ffmpeg interpolant lineairement une suite de (temps, valeur).

    Les portes sont mutuellement exclusives (`gte` inclus / `lt` exclu) : a un
    instant donne un seul terme est non nul, sinon deux segments s'additionnent
    a chaque frontiere et la valeur double.
    """
    if not points:
        return "0"
    if len(points) == 1:
        return f"{points[0][1]:.4f}"

    points = sorted(points, key=lambda p: p[0])
    first_t, first_v = points[0]
    last_t, last_v = points[-1]

    terms = [f"lt({variable}\\,{first_t:.4f})*{first_v:.4f}",
             f"gte({variable}\\,{last_t:.4f})*{last_v:.4f}"]

    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t1 - t0 < 1e-4:
            continue
        slope = (v1 - v0) / (t1 - t0)
        terms.append(
            f"(gte({variable}\\,{t0:.4f})*lt({variable}\\,{t1:.4f}))"
            f"*({v0:.4f}+{slope:.6f}*({variable}-{t0:.4f}))"
        )

    return "+".join(terms)


def _framing_points(
    plan: FramingPlan, edit_list: EditList, src_w: int, src_h: int, crop_w: int, crop_h: int
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Trajectoire convertie en pixels de coin superieur gauche du crop, en
    temps de SORTIE, deja bornee a l'image (l'expression ffmpeg n'a donc pas a
    porter le rabattage elle-meme)."""
    xs: list[tuple[float, float]] = []
    ys: list[tuple[float, float]] = []
    for kf in plan.keyframes:
        t_out = edit_list.to_output_time_clamped(kf.t)
        x = max(0.0, min(kf.cx * src_w - crop_w / 2.0, src_w - crop_w))
        y = max(0.0, min(kf.cy * src_h - crop_h / 2.0, src_h - crop_h))
        xs.append((t_out, x))
        ys.append((t_out, y))
    return xs, ys


def _fixed_crop_rect(src_w: int, src_h: int, target_aspect: float,
                     hint) -> tuple[int, int, int, int]:
    """Rectangle de crop FIXE (x, y, w, h) a `target_aspect`, vise sur `hint`
    si fourni, sinon centre. Generalisation de video.cropper.compute_crop_rect
    a un aspect quelconque -- celle-ci reste cablee sur le 9:16 global et sert
    ailleurs (miniatures, images d'article) ; la dupliquer ici pour un aspect
    par bande evite de toucher a un module partage par des chemins sans
    rapport avec le mode portrait webcam+gameplay."""
    crop_w, crop_h = base_crop_size(src_w, src_h, target_aspect)
    if hint is not None:
        x = hint.x_center_frac * src_w - crop_w / 2
        y = hint.y_center_frac * src_h - crop_h / 2
    else:
        x = (src_w - crop_w) / 2
        y = (src_h - crop_h) / 2
    x = _even(int(max(0, min(x, src_w - crop_w))))
    y = _even(int(max(0, min(y, src_h - crop_h))))
    return x, y, crop_w, crop_h


def _crop_stage(plan: FramingPlan | None, face_hint, edit_list: EditList,
                src_w: int, src_h: int, target_aspect: float) -> tuple[str, int, int]:
    """Le filtre `crop=...` (sans mise a l'echelle) pour UNE fenetre a
    `target_aspect`, plus sa taille source (crop_w, crop_h) -- reutilise par
    le cadrage classique et chacune des deux bandes du mode portrait
    webcam+gameplay, seul `target_aspect` differant entre les trois."""
    moving = plan is not None and not plan.is_static and len(plan.keyframes) >= 2
    if moving:
        crop_w, crop_h = base_crop_size(src_w, src_h, target_aspect)
        xs, ys = _framing_points(plan, edit_list, src_w, src_h, crop_w, crop_h)
        x_expr = piecewise_expression(xs)
        y_expr = piecewise_expression(ys)
        return f"crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}'", crop_w, crop_h

    hint = face_hint
    if plan is not None and plan.keyframes:
        kf = plan.keyframes[0]
        hint = CenterHint(kf.cx, kf.cy)
    x, y, crop_w, crop_h = _fixed_crop_rect(src_w, src_h, target_aspect, hint)
    return f"crop={crop_w}:{crop_h}:{x}:{y}", crop_w, crop_h


FILL_BLACK = "noir"
FILL_BLUR = "flou"

# Comment faire tenir une image dans un cadre qui n'a pas sa forme.
#
# `recadrer` : on garde une fenetre a la forme du cadre et on jette le reste.
# C'est ce qu'il faut quand le sujet occupe une petite partie de l'image -- un
# visage dans un plan large -- et c'est le comportement historique du 9:16.
#
# `entier` : on garde TOUTE l'image, mise a l'echelle pour tenir dans le cadre,
# et on remplit ce qui reste (flou ou noir). C'est le rendu attendu quand rien
# ne doit sortir du champ : un plan large, un paysage, un tableau de jeu. Un
# clip 16:9 poste en vertical y gagne un cadre rempli au lieu de deux bandes
# noires ajoutees par la plateforme.
#
# `webcam_gameplay` : la camera du streamer en bande du HAUT, le jeu en bande
# du BAS -- le rendu du mode "Telecharger en mode portrait" de Twitch, compose
# ici plutot que recupere tout fait (Twitch ne l'expose ni via son API Helix ni
# via yt-dlp, voir editing/subject.py). Necessite une webcam identifiee de
# facon fiable (editing/subject.webcam_track) ; sans elle, retombe
# silencieusement sur FIT_CROP -- voir build_video_chain.
FIT_CROP = "recadrer"
FIT_WHOLE = "entier"
FIT_SPLIT_WEBCAM = "webcam_gameplay"
FIT_MODES = (FIT_CROP, FIT_WHOLE, FIT_SPLIT_WEBCAM)

# Part de la hauteur de sortie reservee a la bande webcam (le reste va au
# jeu). Twitch calcule autrement -- son rendu portrait connait la position
# EXACTE de l'incrustation dans le flux qu'il compose lui-meme -- mais visant
# le meme resultat visuel : un visage bien lisible sans reduire le jeu, qui
# reste l'attraction principale du clip, a une bande trop etroite.
WEBCAM_HEIGHT_FRAC = 0.35

# Flou du fond. Assez fort pour qu'on ne lise plus l'image, assez faible pour
# que les couleurs et le mouvement restent -- c'est ce qui fait que le cadre
# parait rempli plutot que barre de noir.
BLUR_SIGMA = 24

# ALGORITHME DE REDIMENSIONNEMENT. ffmpeg utilise bicubique par defaut ; on
# demande lanczos, qui conserve mieux les hautes frequences.
#
# POURQUOI CA COMPTE ICI PLUS QU'AILLEURS. Un clip vertical est presque
# toujours un AGRANDISSEMENT : la fenetre 9:16 d'une source 1280x720 ne fait
# que 404x720, soit un facteur 2,67 pour atteindre 1080x1920. C'est la plus
# grosse perte de nettete de toute la chaine, bien avant le zoom.
#
# MESURE (maitre 3840x2160 a detail fin, reduit en source puis remonte en
# 1080x1920 ; energie des hautes frequences de la sortie, moyenne du gradient
# absolu) : bicubique 4,94 -> lanczos 5,28 sur une source 720p, 4,96 -> 5,33
# sur une source 1080p, soit +7 % dans les deux cas. Le gain est le meme a
# zoom 1.0, donc il porte sur TOUT le clip et pas seulement sur les zooms.
#
# Le SSIM baisse legerement (0,882 -> 0,872) : c'est la signature connue de
# lanczos, qui garde le detail au prix d'un leger rebond sur les contours,
# ce que le SSIM penalise. La nettete mesuree et l'oeil vont dans l'autre
# sens, et c'est ce qui a decide.
SCALE_FLAGS = "lanczos"


_DEFAUT = object()      # « non precise », distinct de None qui veut dire « le defaut ffmpeg »


# COMPENSER L'AGRANDISSEMENT. La fenetre 9:16 d'une source 1280x720 ne fait que
# 404x720 : il faut l'agrandir 2,67 fois pour remplir 1080x1920, et aucun
# interpolateur ne cree le detail qui manque -- l'image ressort adoucie. Un
# masque flou leger en recupere une partie.
#
# POURQUOI CE N'EST PAS DU MAQUILLAGE, et comment ca a ete verifie. La mesure
# ne compare pas la sortie a elle-meme mais a la VERITE : un maitre 3840x2160
# dont la source a ete tiree. Si le masque flou rapproche la sortie de l'image
# vraie, il recupere du detail reel ; s'il l'en eloigne, il ne fait que durcir
# les contours. Mesure sur deux maitres differents, geometrie a mappage entier
# (indispensable : un decalage d'un seul pixel coute 2 a 3 dB et se ferait
# passer pour une perte de nettete) :
#
#   agrandissement   force optimale   gain en PSNR
#   x1,78            0,20 a 0,35      +0,17 / +0,22 dB
#   x2,67            0,50 a 0,80      +0,30 / +0,20 dB
#   x3,58            0,80 a 1,00      +0,26 / +0,19 dB
#   x4,46            0,80 a 1,00      +0,14 / +0,08 dB
#
# La force optimale CROIT avec l'agrandissement, et une force trop forte nuit
# quand l'agrandissement est faible (a x1,78, 1,0 coute -0,54 dB). D'ou une loi
# proportionnelle plutot qu'une valeur fixe.
#
# SANS AGRANDISSEMENT, AUCUN FILTRE : sur une source deja a la taille de
# sortie, le masque flou n'a rien a recuperer et ne fait que degrader -- mesure
# a -65 dB, l'image n'est plus identique a elle-meme. D'ou le seuil.
#
# LE RISQUE CONNU DE CE FILTRE est d'amplifier le bruit autant que le detail,
# et un stream sombre est granuleux la ou un maitre de synthese est propre. Du
# bruit a donc ete ajoute a la source, en mesurant toujours contre la verite
# PROPRE : le gain reste positif et diminue seulement -- +0,29 dB sans bruit,
# +0,28 avec un bruit leger, +0,23 moyen, +0,11 fort. Le reglage n'a donc pas
# eu a etre abaisse pour les sources bruitees.
SHARPEN_PER_FACTOR = 0.35      # force par unite d'agrandissement au-dela de 1
SHARPEN_MAX = 0.9              # au-dela, les halos se voient plus que le detail
SHARPEN_MIN_FACTOR = 1.15      # en dessous, il n'y a rien a recuperer


def sharpen_amount(factor: float) -> float:
    """Force du masque flou pour cet agrandissement. 0 = pas de filtre."""
    if not factor or factor < SHARPEN_MIN_FACTOR:
        return 0.0
    return round(min(SHARPEN_MAX, SHARPEN_PER_FACTOR * (factor - 1.0)), 2)


def _sharpen(factor: float) -> str:
    """Le filtre, ou une chaine vide quand il n'y a rien a compenser.

    La chrominance est laissee intacte (le dernier parametre a 0) : accentuer
    la couleur d'une image 4:2:0 produit des franges colorees sur les contours,
    pour un gain de nettete nul -- la nettete se lit dans la luminance.
    """
    amount = sharpen_amount(factor)
    if amount <= 0:
        return ""
    return f"unsharp=5:5:{amount:g}:5:5:0.0"


def _scale(w: int, h: int, flags=_DEFAUT, extra: str = "") -> str:
    """Un `scale=` avec l'algorithme choisi.

    `flags=None` laisse le defaut de ffmpeg : c'est ce qu'on veut pour une
    image destinee a etre FLOUTEE juste apres, ou soigner l'interpolation ne
    servirait a rien et couterait du temps.

    SCALE_FLAGS est lu DANS le corps et non comme valeur par defaut : une
    valeur par defaut est evaluee une seule fois, a la definition, donc
    remplacer la constante n'aurait eu aucun effet. Ce piege a fausse une
    mesure pendant l'ecriture de ce code -- les deux variantes sortaient
    identiques.
    """
    if flags is _DEFAUT:
        flags = SCALE_FLAGS
    parts = [f"scale={w}:{h}"]
    if extra:
        parts.append(extra)
    if flags:
        parts.append(f"flags={flags}")
    return ":".join(parts)


def landscape_fill_chain(out_w: int, out_h: int, fill: str = FILL_BLACK,
                         sharpen: str = "") -> str:
    """Comment remplir un cadre plus large que l'image.

    `noir` : bandes noires, le comportement d'origine.
    `flou` : une copie de l'image, agrandie pour couvrir tout le cadre puis
    floutee, sert de fond ; l'image nette est posee dessus, entiere et centree.
    Rien n'est rogne ni deforme -- ce qui est ajoute est une version floue de
    l'image elle-meme, pas une invention.

    Le graphe renvoye contient des etiquettes internes et des ';'. C'est
    volontaire et compatible avec les deux usages : place derriere une entree
    et devant une sortie, il reste un filtrage a une entree et une sortie.
    """
    # `sharpen` ne porte que sur l'image NETTE. L'appliquer apres la
    # composition accentuerait aussi le fond deliberement floute, ce qui est
    # contradictoire -- et sur un degrade lisse un masque flou fait apparaitre
    # des bandes.
    net = "," + sharpen if sharpen else ""
    if fill != FILL_BLUR:
        return (f"{_scale(out_w, out_h, extra='force_original_aspect_ratio=decrease')}{net},"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2")
    return (
        "split=2[vsbg][vsfg];"
        # Le fond garde l'algorithme par defaut : il finit floute, donc
        # soigner son interpolation serait du temps depense pour rien.
        f"[vsbg]{_scale(out_w, out_h, flags=None, extra='force_original_aspect_ratio=increase')},"
        f"crop={out_w}:{out_h},gblur=sigma={BLUR_SIGMA}[vsbgb];"
        f"[vsfg]{_scale(out_w, out_h, extra='force_original_aspect_ratio=decrease')}{net}[vsfgs];"
        "[vsbgb][vsfgs]overlay=(W-w)/2:(H-h)/2"
    )


def _delire_filters(plan) -> list:
    """Les effets du montage delire, ou une liste vide.

    POSES AVANT LES SOUS-TITRES, et c'est deliberé : un texte qui glitche
    n'est plus lisible, alors que l'interet des sous-titres est justement
    qu'on les lise. Le filigrane, lui, est applique plus loin encore (c'est
    une incrustation separee), donc il reste net aussi.

    LE THEME "mystere" EST TRAITE ICI, PAS EN CALQUE : contrairement aux
    autres themes (pluie, etoiles, confettis, braises -- voir
    `_theme_overlay_specs` et `build_ffmpeg_args`), il ne pose aucune image,
    seulement un assombrissement des bords et une desaturation -- deux
    filtres qui se chainent exactement comme les rafales, au meme endroit.
    """
    if plan is None:
        return []
    from video.delire_filters import build_filters
    from video.delire_theme_filters import mystere_chain_filters

    filters = build_filters(plan)
    if getattr(plan, "theme", "") == THEME_MYSTERE:
        filters = filters + mystere_chain_filters()
    return filters


def _split_webcam_chain(
    edit_list: EditList, webcam_plan: FramingPlan, gameplay_plan: FramingPlan | None,
    face_hint, src_w: int, src_h: int, out_w: int, out_h: int,
    webcam_height_frac: float = WEBCAM_HEIGHT_FRAC,
) -> str:
    """Mode portrait webcam+gameplay (FIT_SPLIT_WEBCAM) : la webcam du
    streamer en bande du HAUT, le jeu en bande du BAS, dans UN SEUL passage
    ffmpeg -- comme le rendu "Telecharger en mode portrait" de Twitch, mais
    compose ici plutot que recupere tout fait (voir editing/subject.py pour
    pourquoi).

    Chaque bande a sa PROPRE fenetre de cadrage, a son PROPRE aspect (la bande
    webcam est bien plus large que haute, la bande jeu bien plus haute que
    large) et son PROPRE plan de suivi : le meme mecanisme `crop=` anime que
    le cadrage classique (`_crop_stage`), applique deux fois sur deux copies
    de la source (`split=2`), puis empile verticalement (`vstack`, dans
    l'ordre webcam-puis-jeu -- vstack empile ses entrees de haut en bas dans
    l'ordre donne). Meme principe de composition multi-flux que
    `landscape_fill_chain` (fond+premier plan), la seule autre de ce fichier.

    `gameplay_plan` peut etre None (aucun sujet fiable en dehors de la
    webcam) : `_crop_stage` sait deja retomber sur `face_hint` puis un crop
    centre pur dans ce cas, exactement comme le cadrage classique.

    Le zoom dynamique (zoompan) N'EST PAS applique ici, deliberement : il
    zoome sur UNE fenetre, hors de propos des qu'il y en a deux independantes
    -- l'animer sur les deux a la fois demanderait deux etages zoompan
    distincts pour un gain que le split lui-meme, deja un changement de mise
    en page marque, rend secondaire.
    """
    webcam_h = _even(round(out_h * webcam_height_frac))
    gameplay_h = out_h - webcam_h  # le reste EXACT, jamais un second arrondi
    webcam_aspect = out_w / webcam_h
    gameplay_aspect = out_w / gameplay_h

    webcam_crop, webcam_cw, _ = _crop_stage(webcam_plan, None, edit_list, src_w, src_h, webcam_aspect)
    gameplay_crop, gameplay_cw, _ = _crop_stage(
        gameplay_plan, face_hint, edit_list, src_w, src_h, gameplay_aspect)

    webcam_sharpen = _sharpen(out_w / webcam_cw if webcam_cw else 0)
    gameplay_sharpen = _sharpen(out_w / gameplay_cw if gameplay_cw else 0)

    top = ",".join(p for p in (webcam_crop, _scale(out_w, webcam_h), webcam_sharpen) if p)
    bottom = ",".join(p for p in (gameplay_crop, _scale(out_w, gameplay_h), gameplay_sharpen) if p)

    return (
        "split=2[wcsrc][gpsrc];"
        f"[wcsrc]{top}[wctop];"
        f"[gpsrc]{bottom}[gpbottom];"
        "[wctop][gpbottom]vstack=inputs=2"
    )


def build_video_chain(
    *,
    edit_list: EditList,
    framing_plan: FramingPlan | None,
    zoom_track: ZoomTrack | None,
    src_w: int,
    src_h: int,
    fps: float,
    face_hint=None,
    ass_path: str | None = None,
    target_size: tuple[int, int] = (TARGET_W, TARGET_H),
    fill: str = FILL_BLACK,
    fit: str = FIT_CROP,
    delire_plan=None,
    webcam_plan: FramingPlan | None = None,
) -> str:
    """Chaine video (sans le montage, applique en amont) : cadrage, zoom,
    mise a l'echelle, sous-titres.

    `target_size` porte le format demande. En PAYSAGE, aucun recadrage n'est
    fait : recadrer une source deja horizontale vers un cadre horizontal ne
    ferait que rogner l'image pour rien. Le suivi de visage et le zoom ne
    s'appliquent donc qu'au portrait, ou ils servent a choisir QUOI garder dans
    un cadre bien plus etroit que la source.

    `fit` a `entier` demande de garder toute l'image meme en portrait : le
    cadre est alors rempli exactement comme en paysage. Le suivi de visage et
    le zoom n'ont plus rien a decider dans ce cas -- il n'y a pas de choix a
    faire sur ce qu'on garde, on garde tout.

    `fit` a `webcam_gameplay` demande le mode portrait webcam+gameplay --
    mais seulement si `webcam_plan` est fourni (voir editing/subject.py,
    editing/framing.build_webcam_framing_plan) : SANS webcam identifiee de
    facon fiable, il n'y a rien a mettre dans la bande du haut, et ce mode
    retombe silencieusement sur `recadrer` -- jamais une bande vide ni une
    erreur (repli explicitement demande).
    """
    out_w, out_h = target_size

    if fit == FIT_SPLIT_WEBCAM and webcam_plan is not None and out_w < out_h:
        chain = [_split_webcam_chain(edit_list, webcam_plan, framing_plan, face_hint,
                                     src_w, src_h, out_w, out_h)]
        chain.extend(_delire_filters(delire_plan))
        if ass_path:
            chain.append(subtitle_filter(ass_path))
        return ",".join(chain)
    if out_w >= out_h or fit == FIT_WHOLE:
        # L'image entiere est conservee, et le cadre est complete -- par des
        # bandes noires, ou par une copie floutee de l'image (voir
        # landscape_fill_chain).
        #
        # SAUF quand la source a DEJA le format demande : il n'y a alors rien a
        # completer, l'image mise a l'echelle couvre le cadre exactement. Le
        # fond etait quand meme calcule puis entierement recouvert -- un flou
        # gaussien sur chaque image, pour rien. Mesure sur une source
        # 1080x1920 : l'encodage passait de 0,9 s a 1,9 s pour un rendu dont
        # l'ecart de luminance avec le chemin simple est exactement 0.
        #
        # La condition est une egalite ENTIERE des formats, volontairement
        # stricte : c'est ce qui garantit qu'aucune bande d'un pixel ne peut
        # apparaitre par arrondi du redimensionnement. Elle couvre toutes les
        # resolutions verticales reelles (1080x1920, 720x1280, 540x960...) ; un
        # format seulement proche garde l'ancien chemin.
        if src_w > 0 and src_h > 0 and src_w * out_h == src_h * out_w:
            # L'image couvre le cadre : l'agrandissement est le rapport direct.
            chain = [_scale(out_w, out_h)]
            accentuation = _sharpen(out_w / src_w if src_w else 0)
            if accentuation:
                chain.append(accentuation)
        else:
            # L'image est posee ENTIERE dans le cadre : c'est la plus petite
            # des deux mises a l'echelle qui la limite.
            tenue = min(out_w / src_w, out_h / src_h) if src_w and src_h else 0
            chain = [landscape_fill_chain(out_w, out_h, fill, _sharpen(tenue))]
        chain.extend(_delire_filters(delire_plan))
        if ass_path:
            chain.append(subtitle_filter(ass_path))
        return ",".join(chain)

    moving = framing_plan is not None and not framing_plan.is_static and len(framing_plan.keyframes) >= 2
    zooming = zoom_track is not None and not zoom_track.is_empty

    if moving:
        crop_w, crop_h = base_crop_size(src_w, src_h)
        xs, ys = _framing_points(framing_plan, edit_list, src_w, src_h, crop_w, crop_h)
        x_expr = piecewise_expression(xs)
        y_expr = piecewise_expression(ys)
        chain = [f"crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}'"]
    else:
        # Cadrage fixe : on reutilise exactement la geometrie de cropper.py, y
        # compris quand un plan statique a ete calcule par le suivi.
        hint = face_hint
        if framing_plan is not None and framing_plan.keyframes:
            kf = framing_plan.keyframes[0]
            hint = CenterHint(kf.cx, kf.cy)
        rect: CropRect = compute_crop_rect(src_w, src_h, hint)
        chain = [f"crop={rect.w}:{rect.h}:{rect.x}:{rect.y}"]

    if zooming:
        # zoompan travaille sur l'image deja recadree ; on la porte d'abord a la
        # taille cible multipliee par le zoom maximal, pour que l'agrandissement
        # prenne des pixels reels au lieu d'etirer une image deja reduite.
        max_zoom = max(kf.zoom for kf in zoom_track.keyframes)
        stage_w = _even(out_w * max_zoom)
        stage_h = _even(out_h * max_zoom)
        points = [(edit_list.to_output_time_clamped(kf.t), kf.zoom) for kf in zoom_track.keyframes]
        z_expr = piecewise_expression(points, variable="it")
        chain.append(_scale(stage_w, stage_h))
        chain.append(
            f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={out_w}x{out_h}:fps={fps:.4f}"
        )
    else:
        chain.append(_scale(out_w, out_h))

    # L'agrandissement est celui de la FENETRE reellement prise dans la source,
    # pas celui de l'image entiere : c'est cette fenetre qui doit remplir le
    # cadre. Le zoom l'augmente encore pendant ses quelques dixiemes de
    # seconde, mais on ne suit pas cette variation -- la force resterait dans
    # la meme plage et le filtre ne sait pas s'animer proprement.
    fenetre_w = base_crop_size(src_w, src_h)[0] if src_w and src_h else 0
    accentuation = _sharpen(out_w / fenetre_w if fenetre_w else 0)
    if accentuation:
        chain.append(accentuation)

    chain.extend(_delire_filters(delire_plan))

    if ass_path:
        chain.append(subtitle_filter(ass_path))

    return ",".join(chain)


def build_audio_chain(audio_cfg: dict | None) -> str:
    """Traitement audio : normalisation du volume et limitation des pics.

    Rien n'est applique par defaut si le bloc est absent -- on ne retouche pas
    l'audio d'un utilisateur sans qu'il l'ait demande."""
    cfg = audio_cfg or {}
    if not cfg.get("enabled", False):
        return ""

    filters = []
    if cfg.get("denoise", False):
        # Reduction de bruit legere : trop agressive, elle donne une voix
        # "sous l'eau" bien pire que le bruit de fond d'origine.
        filters.append("afftdn=nr=10:nf=-25")
    if cfg.get("loudnorm", True):
        target = float(cfg.get("target_lufs", -14))
        filters.append(f"loudnorm=I={target}:TP=-1.5:LRA=11")
    if cfg.get("limiter", True):
        filters.append("alimiter=limit=0.95")
    return ",".join(filters)


def build_ffmpeg_args(
    *,
    video_path: str,
    edit_list: EditList,
    framing_plan: FramingPlan | None,
    zoom_track: ZoomTrack | None,
    src_w: int,
    src_h: int,
    fps: float,
    face_hint=None,
    ass_path: str | None,
    audio_cfg: dict | None,
    export_settings: dict,
    out_mp4_path: str,
    target_size: tuple[int, int] = (TARGET_W, TARGET_H),
    watermark=None,
    fill: str = FILL_BLACK,
    fit: str = FIT_CROP,
    delire_plan=None,
    intro=None,
    webcam_plan: FramingPlan | None = None,
) -> list[str]:
    """Arguments complets de l'appel ffmpeg produisant le clip fini."""
    offset = edit_list.source_start
    span = max(0.05, edit_list.source_end - offset)

    video_chain = build_video_chain(
        edit_list=edit_list, framing_plan=framing_plan, zoom_track=zoom_track,
        src_w=src_w, src_h=src_h, fps=fps, face_hint=face_hint, ass_path=ass_path,
        target_size=target_size, fill=fill, fit=fit, delire_plan=delire_plan,
        webcam_plan=webcam_plan,
    )
    audio_chain = build_audio_chain(audio_cfg)

    # -ss avant -i : recherche rapide, indispensable pour un clip situe loin
    # dans une longue video. Les temps du montage deviennent donc relatifs a ce
    # point d'entree.
    args = ["-ss", f"{offset:.3f}", "-i", video_path]

    # Chaque calque superpose (theme, filigrane) est une ENTREE
    # SUPPLEMENTAIRE : cela impose filter_complex des qu'il y en a au moins
    # un, meme quand le reste tiendrait dans un simple -vf. Le theme est
    # branche AVANT le filigrane -- le logo reste toujours au-dessus, jamais
    # recouvert par une texture qui couvre tout le cadre.
    #
    # Une image fixe (le filigrane) convient telle quelle -- overlay repete
    # sa derniere image par defaut, il n'y a rien a boucler. Un calque de
    # theme est desormais une VRAIE VIDEO fond vert (voir
    # video/delire_theme_filters.py) dont la duree est rarement celle du
    # clip : `-stream_loop -1` la boucle indefiniment en ENTREE, le `-t`
    # global juste en dessous bornant deja la SORTIE -- meme principe que
    # `-loop 1` pour une image fixe, adapte a une source qui a deja plusieurs
    # images bien a elle.
    #
    # Chaque entree est declaree AVANT le -t, et non apres : une option
    # placee juste devant une entree s'applique a cette entree. Un -t glisse
    # entre les entrees limiterait leur lecture au lieu de limiter la duree
    # de sortie, et le clip s'etendrait jusqu'a la fin de la video source.
    out_w, out_h = target_size

    overlays = []  # (arguments d'entree, filtre de preparation, position)
    if delire_plan is not None:
        from video.delire_theme_filters import overlay_layers as _theme_overlay_layers

        for layer in _theme_overlay_layers(getattr(delire_plan, "theme", ""), out_w, out_h, fps):
            input_args = (["-stream_loop", "-1", "-i", layer.asset_path] if layer.is_video
                          else ["-i", layer.asset_path])
            overlays.append((input_args, layer.prep_filter, layer.position))
    if watermark is not None:
        from video.watermark import overlay_position, prepare_filter

        overlays.append((
            ["-i", watermark.image],
            prepare_filter(watermark, out_w, out_h),
            overlay_position(watermark, out_w, out_h),
        ))
    if intro is not None:
        # Poser APRES le filigrane : l'incrustation "+ Follow" doit rester
        # au-dessus de tout le reste pendant ses quelques secondes, jamais
        # recouverte. C'est aussi le seul calque a consommer DEUX entrees
        # (la video ET son masque de transparence precalcule, voir
        # intro_overlay.py) -- les indices sont calcules ici, a partir du
        # nombre d'entrees deja utilisees par les calques precedents (1 pour
        # la video principale, +1 par calque anterieur). "prepared=True"
        # dit au compositeur plus bas que le sous-graphe reference deja ses
        # propres entrees, contrairement a un calque a une seule entree.
        from video.intro_overlay import mask_asset_path, overlay_spec

        video_index = 1 + len(overlays)
        mask_index = video_index + 1
        intro_filt, intro_position, intro_enable = overlay_spec(
            intro, out_w, out_h, video_index=video_index, mask_index=mask_index)
        overlays.append((
            ["-i", intro.path, "-i", mask_asset_path()],
            intro_filt, intro_position, intro_enable, True,
        ))

    for input_args, *_ in overlays:
        args += input_args

    args += ["-t", f"{span:.3f}"]

    if not overlays and edit_list.is_identity:
        args += ["-vf", video_chain]
        if audio_chain:
            args += ["-af", audio_chain]
    else:
        # Le dernier maillon de la chaine video PRINCIPALE (avant tout
        # calque) sort directement en "vout" quand il n'y a aucun calque a
        # composer par-dessus -- sinon en "base", pour laisser la boucle
        # ci-dessous le reprendre et produire "vout" a son tour.
        base_label = "vout" if not overlays else "base"

        if edit_list.is_identity:
            pieces = [f"[0:v]{video_chain}[{base_label}]"]
        else:
            segments_v, segments_a, labels = [], [], []
            for i, cut in enumerate(edit_list.cuts):
                start = max(0.0, cut.source_start - offset)
                end = max(start + 0.02, cut.source_end - offset)
                segments_v.append(
                    f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]"
                )
                segments_a.append(
                    f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
                )
                labels.append(f"[v{i}][a{i}]")
            concat = f"{''.join(labels)}concat=n={len(edit_list.cuts)}:v=1:a=1[vc][ac]"
            pieces = segments_v + segments_a + [concat, f"[vc]{video_chain}[{base_label}]"]

        # Les calques se composent EN SERIE : chacun se superpose sur le
        # resultat du precedent, le dernier produisant "vout". Un calque
        # PERMANENT (filigrane, themes) n'a que 3 elements ; un calque BORNE
        # DANS LE TEMPS (l'intro) porte une 4e valeur (la condition ffmpeg
        # `enable=`) et une 5e (`prepared=True`) quand son filtre reference
        # DEJA ses propres entrees (l'intro en consomme deux -- video et
        # masque -- voir intro_overlay.py) : le compositeur ne doit alors pas
        # lui prefixer un "[N:v]" comme pour un calque a une seule entree.
        # `next_index` avance du nombre REEL d'entrees consommees par
        # chaque calque, pas de 1 systematiquement.
        current = base_label
        next_index = 1  # l'entree 0 est toujours la video principale
        for i, (input_args, filt, position, *rest) in enumerate(overlays):
            n_inputs = len(input_args) // 2  # chaque entree est un couple "-i", chemin
            enable = rest[0] if len(rest) > 0 else None
            prepared = rest[1] if len(rest) > 1 else False
            next_label = "vout" if i == len(overlays) - 1 else f"stage{i}"
            enable_part = f":enable='{enable}'" if enable else ""
            layer_filt = filt if prepared else f"[{next_index}:v]{filt}"
            pieces.append(f"{layer_filt}[ov{i}]")
            pieces.append(f"[{current}][ov{i}]overlay={position}{enable_part}[{next_label}]")
            current = next_label
            next_index += n_inputs

        graph = ";".join(pieces)

        if edit_list.is_identity:
            args += ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a?"]
            if audio_chain:
                args += ["-af", audio_chain]
        else:
            graph += f";[ac]{audio_chain}[aout]" if audio_chain else ";[ac]anull[aout]"
            args += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]"]

    args += [
        "-c:v", "libx264",
        "-preset", str(export_settings.get("video_preset", "medium")),
        "-crf", str(export_settings.get("video_bitrate_crf", 20)),
        "-c:a", "aac",
        "-b:a", str(export_settings.get("audio_bitrate", "160k")),
        "-movflags", "+faststart",
        out_mp4_path,
    ]
    return args
