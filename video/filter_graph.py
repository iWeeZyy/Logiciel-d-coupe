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
) -> str:
    """Chaine video (sans le montage, applique en amont) : cadrage, zoom,
    mise a l'echelle, sous-titres.

    `target_size` porte le format demande. En PAYSAGE, aucun recadrage n'est
    fait : recadrer une source deja horizontale vers un cadre horizontal ne
    ferait que rogner l'image pour rien. Le suivi de visage et le zoom ne
    s'appliquent donc qu'au portrait, ou ils servent a choisir QUOI garder dans
    un cadre bien plus etroit que la source.
    """
    out_w, out_h = target_size
    if out_w >= out_h:
        chain = [
            # decrease + pad : l'image entiere est conservee, et le cadre est
            # complete par des bandes plutot que de deformer ou de rogner.
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease",
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2",
        ]
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
        chain.append(f"scale={stage_w}:{stage_h}")
        chain.append(
            f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={out_w}x{out_h}:fps={fps:.4f}"
        )
    else:
        chain.append(f"scale={out_w}:{out_h}")

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
) -> list[str]:
    """Arguments complets de l'appel ffmpeg produisant le clip fini."""
    offset = edit_list.source_start
    span = max(0.05, edit_list.source_end - offset)

    video_chain = build_video_chain(
        edit_list=edit_list, framing_plan=framing_plan, zoom_track=zoom_track,
        src_w=src_w, src_h=src_h, fps=fps, face_hint=face_hint, ass_path=ass_path,
        target_size=target_size,
    )
    audio_chain = build_audio_chain(audio_cfg)

    # -ss avant -i : recherche rapide, indispensable pour un clip situe loin
    # dans une longue video. Les temps du montage deviennent donc relatifs a ce
    # point d'entree.
    args = ["-ss", f"{offset:.3f}", "-i", video_path, "-t", f"{span:.3f}"]

    if edit_list.is_identity:
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
