"""Cadrage intelligent 9:16 : trajectoire de recadrage lissee (fonctionnalite 2).

Transforme une suite de detections de visages (video/face_detector.py) en une
trajectoire de points de visee, lissee pour que le cadrage ne tremble jamais :

1. cible brute a chaque instant (visage seul, locuteur actif, ou barycentre
   du groupe quand personne ne se detache) ;
2. zone morte : sous un certain deplacement, on ne bouge pas du tout -- c'est
   ce qui elimine le tremblement de la detection, pas le lissage ;
3. moyenne exponentielle : le cadrage suit le sujet avec un peu d'inertie ;
4. limite de vitesse : meme sur un saut brutal de detection, le cadrage ne peut
   pas se deplacer plus vite qu'une fraction d'image par seconde.

Si les detections sont trop rares ou trop instables, la fonction renvoie un
plan STATIQUE : mieux vaut un cadrage fixe correct qu'un cadrage mobile qui se
trompe (section 13 du cahier des charges).

Module pur : il ne lit ni la video ni ffmpeg, seulement des positions.
"""
from __future__ import annotations

from dataclasses import dataclass

from editing.speaker import SpeakerDecision, order_faces_left_to_right, speaker_at
from video.face_detector import FaceSample

MODE_STATIC = "static"
_CUT_S = 0.04              # duree d'un cut : un peu plus d'une image a 30 i/s
MODE_TRACK = "track"
MODE_BOTH = "both"


@dataclass(frozen=True)
class FramingKeyframe:
    """Point de visee a un instant SOURCE, en fractions de l'image d'origine."""

    t: float
    cx: float
    cy: float
    # Changement de personne : le cadre y SAUTE (un cut, comme en realisation
    # tele) au lieu de glisser lentement d'un visage a l'autre.
    cut: bool = False


@dataclass(frozen=True)
class FramingPlan:
    keyframes: tuple[FramingKeyframe, ...]
    mode: str
    confidence: float
    reasons: tuple[str, ...] = ()
    # Intervalles (temps SOURCE) en plan large : plusieurs personnes, et
    # personne ne parle (ou on ne sait pas qui). Voir wide_intervals().
    wide: tuple[tuple[float, float], ...] = ()
    # Etendue horizontale du groupe pendant ces plans (fractions de l'image) :
    # le plan large montre tous les participants, sans les bords inutiles.
    wide_span: tuple[float, float] = (0.0, 1.0)
    # Bande webcam du mode portrait : hauteur de la fenetre en fraction de la
    # source (0 = la plus grande fenetre possible, comportement d'avant).
    window_h: float = 0.0
    # Zone a montrer ENTIERE, centree sur fond flou (x0, y0, x1, y1 en
    # fractions) : la webcam seule quand le reste de l'image est fixe.
    box: tuple[float, float, float, float] | None = None

    @property
    def is_static(self) -> bool:
        return len(self.keyframes) <= 1

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "confidence": round(self.confidence, 3),
            "keyframes": len(self.keyframes),
            "wide_shots": len(self.wide),
            "reasons": list(self.reasons),
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def choose_targets(
    samples: list[FaceSample],
    speaker_decisions: list[SpeakerDecision] | None = None,
    two_faces_min_distance_frac: float = 0.18,
    vertical_bias: float = 0.42,
    subject_filter=None,
    crop_width_frac: float | None = None,
) -> tuple[list[FramingKeyframe], str]:
    """Cible brute a chaque echantillon, plus le mode retenu.

    `crop_width_frac` : largeur du cadre 9:16 en fraction de l'image. Quand le
    groupe est plus large que le cadre (plateau a cinq dans une bande large,
    signale sur une compilation de Squeezie), cadrer son milieu montrerait le
    vide entre deux personnes : on reste alors sur la derniere personne qui a
    parle, a defaut sur le plus grand visage. None : comportement d'avant.

    `vertical_bias` place le visage un peu au-dessus du centre du cadre (0.42 =
    aux deux cinquiemes) : un visage exactement centre en 9:16 laisse un vide
    au-dessus de la tete et coupe le buste.

    `subject_filter` decide QUI cadrer -- par defaut `keep_subject_faces`
    (voir plus bas), qui ecarte une incrustation webcam. Le mode portrait
    webcam+gameplay (video/filter_graph.py, FIT_SPLIT_WEBCAM) passe
    `keep_webcam_faces` a la place pour cadrer l'inverse exact : l'incrustation
    elle-meme, voir editing/subject.py.
    """
    # Meme regle que pour le cadrage fixe : une incrustation webcam n'est pas un
    # participant. Sans ce filtre, deux visages eloignes -- le sujet et la
    # vignette -- declenchaient le cadrage "groupe", qui cadrait le vide entre
    # les deux.
    from editing.subject import keep_subject_faces

    subject_filter = subject_filter or keep_subject_faces
    filtered = subject_filter(samples)
    samples = filtered if filtered is not None else []

    targets: list[FramingKeyframe] = []
    modes: list[str] = []
    last_cx: float | None = None          # derniere personne suivie

    for sample in samples:
        if not sample.faces:
            continue

        ordered = order_faces_left_to_right(sample)
        if len(ordered) >= 2:
            boxes = [face for face, _ in ordered]
            # Etalement de TOUS les visages, pas seulement des deux premiers :
            # sur un plateau a trois ou quatre personnes, les deux plus a gauche
            # peuvent etre cote a cote alors que le groupe occupe tout le cadre.
            spread = max(b.cx for b in boxes) - min(b.cx for b in boxes)
            focus = speaker_at(speaker_decisions or [], sample.t)

            too_wide = crop_width_frac is not None and spread > crop_width_frac * 0.9
            if focus is None and too_wide:
                # Le groupe ne tient pas dans le cadre : on reste sur la
                # personne suivie jusqu'ici (celle qui parlait), sinon sur le
                # plus grand visage -- jamais sur le vide entre deux.
                if last_cx is None:
                    chosen = max(boxes, key=lambda b: b.area)
                else:
                    chosen = min(boxes, key=lambda b: abs(b.cx - last_cx))
                cx, cy = chosen.cx, chosen.cy
                modes.append(MODE_TRACK)
            elif spread >= two_faces_min_distance_frac and focus is None:
                # Plusieurs personnes distinctes, aucun locuteur identifie de
                # facon sure : on cadre le groupe plutot que de parier. Avec
                # deux visages, ce barycentre est exactement leur milieu --
                # le comportement d'avant est donc conserve tel quel.
                cx = sum(b.cx for b in boxes) / len(boxes)
                cy = sum(b.cy for b in boxes) / len(boxes)
                modes.append(MODE_BOTH)
            else:
                chosen = ordered[focus][0] if focus is not None and focus < len(ordered) else ordered[0][0]
                cx, cy = chosen.cx, chosen.cy
                modes.append(MODE_TRACK)
        else:
            cx, cy = ordered[0][0].cx, ordered[0][0].cy
            modes.append(MODE_TRACK)

        # Une autre personne que celle suivie jusqu'ici : cut (seulement si
        # on connait la largeur du cadre, sinon comportement d'avant).
        cut = (crop_width_frac is not None and last_cx is not None
               and abs(cx - last_cx) > crop_width_frac / 2)
        last_cx = cx
        targets.append(FramingKeyframe(t=sample.t, cx=_clamp01(cx),
                                       cy=_clamp01(cy - (0.5 - vertical_bias)), cut=cut))

    mode = MODE_BOTH if modes.count(MODE_BOTH) > len(modes) / 2 else MODE_TRACK
    return targets, (mode if targets else MODE_STATIC)


def smooth_trajectory(
    targets: list[FramingKeyframe],
    alpha: float = 0.25,
    max_speed_frac_per_s: float = 0.12,
    deadzone_frac: float = 0.02,
) -> list[FramingKeyframe]:
    """Lissage : zone morte, puis moyenne exponentielle, puis limite de vitesse."""
    if not targets:
        return []

    smoothed = [targets[0]]
    for previous_target, target in zip(targets, targets[1:]):
        last = smoothed[-1]
        dt = max(1e-3, target.t - previous_target.t)

        if target.cut:
            # Cut : on tient l'ancien cadre jusqu'a l'instant d'avant, puis on
            # saute -- deux points rapproches que l'interpolation ffmpeg rend
            # comme une coupe franche.
            smoothed.append(FramingKeyframe(t=max(last.t, target.t - _CUT_S), cx=last.cx,
                                            cy=last.cy))
            smoothed.append(target)
            continue

        cx, cy = target.cx, target.cy
        if abs(cx - last.cx) < deadzone_frac:
            cx = last.cx
        if abs(cy - last.cy) < deadzone_frac:
            cy = last.cy

        cx = alpha * cx + (1.0 - alpha) * last.cx
        cy = alpha * cy + (1.0 - alpha) * last.cy

        max_step = max_speed_frac_per_s * dt
        cx = last.cx + max(-max_step, min(max_step, cx - last.cx))
        cy = last.cy + max(-max_step, min(max_step, cy - last.cy))

        smoothed.append(FramingKeyframe(t=target.t, cx=_clamp01(cx), cy=_clamp01(cy)))

    return smoothed


def _decimate(keyframes: list[FramingKeyframe], max_keyframes: int) -> list[FramingKeyframe]:
    """Ramene la trajectoire sous une limite de points : l'expression ffmpeg
    construite ensuite grandit lineairement avec leur nombre, et une expression
    de plusieurs milliers de caracteres devient couteuse a evaluer pour chaque
    image. Les deux points d'un cut sont toujours gardes : sans eux, la coupe
    redeviendrait un glissement."""
    if len(keyframes) <= max_keyframes:
        return keyframes
    must = set()
    for i, kf in enumerate(keyframes):
        if kf.cut:
            must.update({i, i - 1})
    must.update({0, len(keyframes) - 1})
    budget = max(0, max_keyframes - len(must))
    step = len(keyframes) / budget if budget else len(keyframes) + 1
    chosen = must | {int(i * step) for i in range(budget)}
    return [keyframes[i] for i in sorted(i for i in chosen if 0 <= i < len(keyframes))]


def group_span(samples, intervals, margin: float = 0.05) -> tuple[float, float]:
    """Du bord gauche du participant le plus a gauche au bord droit du plus a
    droite, pendant les plans larges, avec une marge (fonction pure). Le
    visage seul ne suffit pas a « voir la personne en entier » : la marge
    couvre les epaules."""
    lefts, rights = [], []
    for sample in samples:
        if not any(a <= sample.t <= b for a, b in intervals):
            continue
        faces = getattr(sample, "faces", ()) or ()
        if not faces:
            # Un passage sans personne : rien a resserrer, l'image entiere.
            return 0.0, 1.0
        for face in faces:
            lefts.append(face.x - face.w)
            rights.append(face.x + 2 * face.w)
    if not lefts:
        return 0.0, 1.0
    return max(0.0, min(lefts) - margin), min(1.0, max(rights) + margin)


def wide_intervals(samples, speaker_decisions, speech_spans, crop_width_frac: float,
                   min_shot_s: float = 1.0, half_step_s: float = 0.25,
                   min_tight_s: float = 2.0, min_lost_s: float = 3.0):
    """Moments en plan large (fonction pure, temps SOURCE).

    Demande de l'utilisateur : cadrer la personne qui parle, et quand
    personne ne parle -- ou qu'on ne sait pas qui -- montrer TOUS les
    participants en entier ; a plus de deux, toujours le plan large. Un
    echantillon est « large » quand il montre plusieurs visages qui ne
    tiennent pas dans le cadre 9:16, et qu'il y en a plus de deux, ou
    qu'aucune parole ne l'entoure (`speech_spans`, mots de la transcription),
    ou qu'aucun locuteur n'est identifie. Les plans de moins de `min_shot_s`
    (larges comme serres) sont absorbes : un cadre qui clignote est pire
    qu'un cadre imparfait.

    Visage perdu moins de `min_lost_s` (tete baissee, penche hors du champ) :
    le cadrage en cours est garde. Sans ca, une personne seule qui baisse la
    tete une seconde et demie faisait surgir l'image entiere, minuscule, sur
    fond flou, au milieu d'un gros plan (Kamet0, 2026-11-12). Seule une
    absence de visage DURABLE (scene, capture d'ecran) passe en plan large."""
    from editing.speaker import speaker_at

    def speaking(t: float) -> bool:
        return any(a < t + half_step_s and b > t - half_step_s for a, b in speech_spans)

    flags = []
    for sample in samples:
        faces = list(getattr(sample, "faces", ()) or ())
        # Personne a cadrer (scene, capture d'ecran, affiche…) : l'image
        # entiere sur fond flou plutot qu'un recadrage au hasard (demande de
        # l'utilisateur).
        wide = not faces
        if len(faces) >= 2:
            left = min(f.x for f in faces)
            right = max(f.x + f.w for f in faces)
            if right - left > crop_width_frac:
                # Plus de deux personnes : toujours en plan large (demande de
                # l'utilisateur). A deux : celle qui parle, plan large dans
                # les silences ou si on ne sait pas qui parle.
                wide = (len(faces) > 2 or not speaking(sample.t)
                        or speaker_at(speaker_decisions or [], sample.t) is None)
        flags.append((sample.t, wide, not faces))
    if not flags:
        return ()

    # Pertes de visage breves : on garde le cadrage en cours (voir docstring).
    i = 0
    while i < len(flags):
        j = i
        while j < len(flags) and flags[j][1] and flags[j][2]:
            j += 1
        if j > i:
            lost = flags[j - 1][0] - flags[i][0] + 2 * half_step_s
            if lost < min_lost_s:
                flags[i:j] = [(t, False, True) for t, _, _ in flags[i:j]]
            i = j
        else:
            i += 1

    # Suites d'echantillons « larges » -> intervalles.
    raw, start, last = [], None, None
    for t, wide, _ in flags:
        if wide and start is None:
            start = t
        if not wide and start is not None:
            raw.append((start - half_step_s, last + half_step_s))
            start = None
        last = t
    if start is not None:
        raw.append((start - half_step_s, last + half_step_s))

    # Trous serres trop courts : on reste en large ; plans larges trop courts : ecartes.
    merged = []
    for a, b in raw:
        # Retour en serre de moins de `min_tight_s` entre deux plans larges :
        # le plus souvent un visage perdu une seconde par la detection.
        if merged and a - merged[-1][1] < min_tight_s:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return tuple((max(0.0, a), b) for a, b in merged if b - a >= min_shot_s)


def build_framing_plan(
    samples: list[FaceSample],
    *,
    speaker_decisions: list[SpeakerDecision] | None = None,
    min_samples_ratio: float = 0.35,
    two_faces_min_distance_frac: float = 0.18,
    vertical_bias: float = 0.42,
    smoothing_alpha: float = 0.25,
    max_speed_frac_per_s: float = 0.12,
    deadzone_frac: float = 0.02,
    max_keyframes: int = 60,
    static_movement_threshold: float = 0.03,
    subject_filter=None,
    crop_width_frac: float | None = None,
    speech_spans=None,
    clip_span: tuple[float, float] | None = None,
) -> FramingPlan:
    """Trajectoire de cadrage, ou plan statique si le suivi n'est pas fiable.

    `subject_filter` est transmis tel quel a `choose_targets` -- voir son
    docstring. Reste `None` (donc `keep_subject_faces`) pour tout appelant
    existant."""
    def whole_image(ratio: float, reason: str) -> FramingPlan:
        """Personne a cadrer : en 9:16 (`crop_width_frac` connu), l'image
        entiere sur fond flou pendant tout le clip ; sinon cadrage fixe."""
        if crop_width_frac is None:
            return FramingPlan((), MODE_STATIC, ratio, (reason + " -- cadrage fixe",))
        if clip_span is not None:
            a, b = clip_span
        elif samples:
            a, b = samples[0].t - 1.0, samples[-1].t + 1.0
        else:
            return FramingPlan((), MODE_STATIC, ratio, (reason + " -- cadrage fixe",))
        return FramingPlan((), MODE_STATIC, ratio,
                           (reason + " -- image entiere sur fond flou",),
                           wide=((max(0.0, a), b),), wide_span=(0.0, 1.0))

    if not samples:
        return whole_image(0.0, "aucune detection de visage")

    if crop_width_frac is not None:
        # En 9:16, seules les personnes DURABLES se cadrent : un visage qui
        # passe dans le contenu montre (retransmission, video) n'en est pas une.
        from editing.subject import keep_persistent_faces

        samples = keep_persistent_faces(samples)

    with_faces = [s for s in samples if s.faces]
    ratio = len(with_faces) / len(samples)
    if ratio < min_samples_ratio or len(with_faces) < 3:
        return whole_image(round(ratio, 3),
                           f"visage detecte sur seulement {ratio:.0%} des images analysees")

    targets, mode = choose_targets(
        with_faces, speaker_decisions, two_faces_min_distance_frac, vertical_bias,
        subject_filter=subject_filter, crop_width_frac=crop_width_frac,
    )
    if not targets:
        return FramingPlan((), MODE_STATIC, 0.0, ("aucune cible exploitable",))

    smoothed = smooth_trajectory(targets, smoothing_alpha, max_speed_frac_per_s, deadzone_frac)
    wide = ()
    if crop_width_frac is not None and speech_spans is not None:
        # Les participants seulement : une incrustation webcam ou un visage
        # sur un ecran ne fait pas basculer en plan large (editing/subject.py).
        from editing.subject import keep_subject_faces

        participants = (subject_filter or keep_subject_faces)(samples) or samples
        wide = wide_intervals(participants, speaker_decisions, speech_spans, crop_width_frac)
    wide_span = group_span(participants, wide) if wide else (0.0, 1.0)

    span_x = max(k.cx for k in smoothed) - min(k.cx for k in smoothed)
    span_y = max(k.cy for k in smoothed) - min(k.cy for k in smoothed)
    if max(span_x, span_y) < static_movement_threshold:
        # Le sujet ne bouge pas : un cadrage fixe donne exactement le meme
        # resultat, sans expression temporelle a evaluer image par image.
        middle = smoothed[len(smoothed) // 2]
        return FramingPlan(
            (FramingKeyframe(t=smoothed[0].t, cx=middle.cx, cy=middle.cy),),
            MODE_STATIC, round(ratio, 3), ("sujet immobile -- cadrage fixe",), wide=wide,
            wide_span=wide_span,
        )

    reasons = [f"visage suivi sur {ratio:.0%} des images analysees"]
    if wide:
        reasons.append(f"{len(wide)} plan(s) large(s) quand personne ne parle")
    if mode == MODE_BOTH:
        reasons.append("deux personnes cadrees ensemble (locuteur non identifie de facon sure)")
    elif speaker_decisions and any(d.face_index is not None for d in speaker_decisions):
        reasons.append("cadrage centre sur le locuteur actif")

    return FramingPlan(
        tuple(_decimate(smoothed, max_keyframes)), mode, round(ratio, 3), tuple(reasons),
        wide=wide, wide_span=wide_span,
    )


def build_webcam_framing_plan(
    samples: list[FaceSample],
    *,
    min_samples_ratio: float = 0.35,
    smoothing_alpha: float = 0.25,
    max_speed_frac_per_s: float = 0.12,
    deadzone_frac: float = 0.02,
    max_keyframes: int = 60,
    static_movement_threshold: float = 0.03,
    webcam=None,
) -> FramingPlan | None:
    """Trajectoire de cadrage pour la bande WEBCAM du mode portrait (voir
    video/filter_graph.py, FIT_SPLIT_WEBCAM), ou None si aucune webcam ne se
    degage clairement.

    None est un signal DISTINCT d'un FramingPlan statique : c'est ce qui dit a
    l'appelant (pipeline.py) qu'il n'y a rien a cadrer pour la bande du haut,
    et qu'il faut renoncer au mode portrait pour ce clip plutot que de cadrer
    une bande entiere sur du bruit (demande explicite : repli silencieux vers
    le cadrage classique quand aucune webcam n'est detectee).

    Verifie AVANT tout calcul de trajectoire, sur l'ensemble complet des
    echantillons avec visage -- pas seulement en cas d'echec du lissage plus
    bas, qui ne distinguerait pas "pas de webcam" de "webcam trop instable
    pour etre suivie" (ce dernier cas retombe legitimement sur un cadrage fixe,
    comme build_framing_plan le fait deja pour le sujet)."""
    from editing.subject import build_tracks, keep_webcam_faces, webcam_track

    with_faces = [s for s in samples if s.faces]
    subject_filter = keep_webcam_faces
    if webcam is not None:
        # Webcam d'angle deja designee (editing/subject.corner_webcam) : on
        # cadre CELLE-LA. `webcam_track` prendrait le plus petit visage
        # durable -- sur une retransmission, la camera d'un joueur dans
        # l'interface du match (constate sur un clip de Kamet0).
        from dataclasses import replace as _replace

        from editing.subject import CLUSTER_RADIUS

        def subject_filter(items):
            out = []
            for item in items:
                faces = [b for b in item.faces
                         if ((b.cx - webcam.cx) ** 2 + (b.cy - webcam.cy) ** 2) ** 0.5
                         < CLUSTER_RADIUS]
                out.append(_replace(item, faces=tuple(faces),
                                    mouth_activity=(0.0,) * len(faces)))
            return out
    else:
        webcam = webcam_track(build_tracks(with_faces))
    if webcam is None:
        return None

    # `vertical_bias=0.5` (centre) et non 0.42 (le biais du cadrage plein
    # cadre) : ce dernier reserve de la place SOUS le visage pour le buste,
    # pertinent sur un cadre de 1920px de haut, pas sur une bande de ~670px
    # (35% de 1920) ou centrer le visage epuise deja presque toute la place.
    plan = build_framing_plan(
        samples,
        min_samples_ratio=min_samples_ratio,
        vertical_bias=0.5,
        smoothing_alpha=smoothing_alpha,
        max_speed_frac_per_s=max_speed_frac_per_s,
        deadzone_frac=deadzone_frac,
        max_keyframes=max_keyframes,
        static_movement_threshold=static_movement_threshold,
        subject_filter=subject_filter,
    )
    if plan.keyframes:
        return plan

    # `webcam_track` a trouve une incrustation fiable (sinon on serait deja
    # sorti plus haut), mais `min_samples_ratio` -- calcule sur la proportion
    # GLOBALE d'echantillons avec AU MOINS un visage, webcam ou pas -- peut
    # rester en dessous du seuil meme quand la webcam elle-meme est bien
    # identifiee (ex: le sujet principal, lui, est rarement visible). Un
    # cadrage fixe sur sa position MESUREE vaut toujours mieux qu'un crop
    # centre au hasard, qui ignorerait completement ou elle se trouve.
    return FramingPlan(
        (FramingKeyframe(t=with_faces[0].t if with_faces else 0.0, cx=webcam.cx, cy=webcam.cy),),
        MODE_STATIC, plan.confidence,
        plan.reasons + ("cadrage fixe sur la position mesuree de la webcam",),
    )
