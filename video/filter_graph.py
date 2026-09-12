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

from editing.framing import FramingPlan
from editing.timeline import EditList
from editing.zoom import ZoomTrack
from video.cropper import TARGET_H, TARGET_W, CenterHint, CropRect, compute_crop_rect
from video.subtitle_renderer import subtitle_filter

_TARGET_ASPECT = TARGET_W / TARGET_H


def _even(n: int) -> int:
    n = int(n)
    return n - (n % 2)


def base_crop_size(src_w: int, src_h: int) -> tuple[int, int]:
    """Plus grande fenetre 9:16 tenant dans l'image source."""
    if src_w / src_h > _TARGET_ASPECT:
        return _even(min(src_w, round(src_h * _TARGET_ASPECT))), _even(src_h)
    return _even(src_w), _even(min(src_h, round(src_w / _TARGET_ASPECT)))


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
FIT_CROP = "recadrer"
FIT_WHOLE = "entier"
FIT_MODES = (FIT_CROP, FIT_WHOLE)

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
    """
    if plan is None:
        return []
    from video.delire_filters import build_filters

    return build_filters(plan)


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
    """
    out_w, out_h = target_size
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
) -> list[str]:
    """Arguments complets de l'appel ffmpeg produisant le clip fini."""
    offset = edit_list.source_start
    span = max(0.05, edit_list.source_end - offset)

    video_chain = build_video_chain(
        edit_list=edit_list, framing_plan=framing_plan, zoom_track=zoom_track,
        src_w=src_w, src_h=src_h, fps=fps, face_hint=face_hint, ass_path=ass_path,
        target_size=target_size, fill=fill, fit=fit, delire_plan=delire_plan,
    )
    audio_chain = build_audio_chain(audio_cfg)

    # -ss avant -i : recherche rapide, indispensable pour un clip situe loin
    # dans une longue video. Les temps du montage deviennent donc relatifs a ce
    # point d'entree.
    args = ["-ss", f"{offset:.3f}", "-i", video_path]

    # Le filigrane est une SECONDE ENTREE : il impose donc filter_complex, meme
    # quand le reste tiendrait dans un simple -vf. Une image fixe convient telle
    # quelle -- overlay repete sa derniere image par defaut, il n'y a rien a
    # boucler.
    #
    # Il est declare AVANT le -t, et non apres : une option placee juste devant
    # une entree s'applique a cette entree. Un -t glisse entre les deux
    # limiterait la lecture du logo au lieu de limiter la duree de sortie, et le
    # clip s'etendrait jusqu'a la fin de la video source.
    out_w = target_size[0]
    logo_chain = position = ""
    if watermark is not None:
        from video.watermark import overlay_position, prepare_filter

        args += ["-i", watermark.image]
        logo_chain = prepare_filter(watermark, out_w)
        position = overlay_position(watermark, out_w, target_size[1])

    args += ["-t", f"{span:.3f}"]

    if watermark is not None and edit_list.is_identity:
        graph = (f"[0:v]{video_chain}[base];"
                 f"[1:v]{logo_chain}[wm];"
                 f"[base][wm]overlay={position}[vout]")
        args += ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a?"]
        if audio_chain:
            args += ["-af", audio_chain]
    elif edit_list.is_identity:
        args += ["-vf", video_chain]
        if audio_chain:
            args += ["-af", audio_chain]
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
        graph = ";".join(segments_v + segments_a + [concat])
        if watermark is not None:
            graph += f";[vc]{video_chain}[base]"
            graph += f";[1:v]{logo_chain}[wm]"
            graph += f";[base][wm]overlay={position}[vout]"
        else:
            graph += f";[vc]{video_chain}[vout]"
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
