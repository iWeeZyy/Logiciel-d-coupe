"""Montage d'une video narree : quoi durer, quel son garder, quel cadre.

Module PUR. Il ne lance pas ffmpeg : il decide, et construit la ligne de
commande. Tout est donc verifiable sans encoder une seule image, comme
video/filter_graph.py -- dont il REUTILISE la geometrie (`build_video_chain`)
plutot que d'en ecrire une deuxieme : le remplissage flou du 16:9, le recadrage
9:16 et l'incrustation des sous-titres doivent se comporter ici exactement
comme dans le rendu des clips.

Trois decisions y vivent, et aucune n'est prise a la place de l'utilisateur :

- LA DUREE. Une narration ne fait presque jamais la duree de la video. Les
  trois politiques (`couper`, `video`, `figer`) disent chacune ce qui arrive au
  plus long des deux ; aucune ne ralentit ni n'accelere quoi que ce soit --
  etirer une voix ou une image pour les faire coincider est audible, visible,
  et personne ne l'a demande.
- LE SON. Remplacer, garder, ou melanger. En melange, le son d'origine est
  BAISSE et non supprime, et les deux volumes sont explicites : `amix` divise
  par defaut le niveau par le nombre d'entrees (`normalize=1`), ce qui ferait
  chuter la narration de moitie sans que rien ne l'explique -- d'ou
  `normalize=0`.
- LE CADRE. En 9:16 on recadre (avec ou sans suivi du sujet) ; en 16:9 on garde
  l'image entiere et on remplit les bords, exactement comme le Radar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- Son -------------------------------------------------------------------
AUDIO_REPLACE = "remplacer"      # la narration seule
AUDIO_KEEP = "garder"            # le son d'origine seul (narration non utilisee)
AUDIO_MIX = "mixer"              # narration + son d'origine baisse
AUDIO_MODES = (AUDIO_REPLACE, AUDIO_KEEP, AUDIO_MIX)

AUDIO_LABELS = {
    AUDIO_REPLACE: "Remplacer le son de la vidéo par la narration",
    AUDIO_KEEP: "Garder le son de la vidéo (sans narration)",
    AUDIO_MIX: "Mixer la narration avec le son de la vidéo",
}

DEFAULT_ORIGINAL_VOLUME = 0.20   # son d'origine en fond, sous la voix
DEFAULT_NARRATION_VOLUME = 1.00

# --- Duree -----------------------------------------------------------------
DURATION_CUT = "couper"          # sortie = le plus court des deux
DURATION_VIDEO = "video"         # sortie = duree de la video
DURATION_FREEZE = "figer"        # sortie = le plus long ; derniere image figee
DURATION_POLICIES = (DURATION_CUT, DURATION_VIDEO, DURATION_FREEZE)

DURATION_LABELS = {
    DURATION_CUT: "Couper au plus court (la vidéo ou la narration est tronquée)",
    DURATION_VIDEO: "Garder toute la vidéo (silence après la narration)",
    DURATION_FREEZE: "Figer la dernière image si la narration est plus longue",
}

# --- Cadre -----------------------------------------------------------------
FRAMING_CENTER = "centre"
FRAMING_SUBJECT = "sujet"
FRAMING_LABELS = {
    FRAMING_CENTER: "Recadrage centré",
    FRAMING_SUBJECT: "Suivi du sujet (détection de visage)",
}

# Recadrer ou garder toute l'image : les memes valeurs que le rendu des clips
# (video/filter_graph.py), pas un second vocabulaire.
FIT_CROP = "recadrer"
FIT_WHOLE = "entier"
FIT_LABELS = {
    FIT_CROP: "Recadrer (l'image est rognée)",
    FIT_WHOLE: "Image entière + bords remplis",
}


@dataclass(frozen=True)
class DurationPlan:
    """Ce que la sortie va durer, et pourquoi."""

    output_s: float
    freeze_s: float = 0.0          # duree de derniere image figee ajoutee
    notes: tuple = field(default_factory=tuple)

    @property
    def freezes(self) -> bool:
        return self.freeze_s > 0.01


def _seconds(value) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def resolve_duration(video_s, narration_s, policy: str = DURATION_CUT) -> DurationPlan:
    """Duree de sortie, sans jamais deformer le temps.

    Une narration absente (ou vide) laisse la video telle quelle : il n'y a
    rien a aligner.
    """
    video = _seconds(video_s)
    narration = _seconds(narration_s)
    if video <= 0:
        # Duree de la video inconnue : on ne devine pas. La narration decide,
        # et le rendu s'arrete avec elle.
        return DurationPlan(output_s=narration, notes=(
            "La durée de la vidéo n'a pas pu être mesurée : le rendu s'arrête "
            "à la fin de la narration.",))
    if narration <= 0:
        return DurationPlan(output_s=video)

    policy = policy if policy in DURATION_POLICIES else DURATION_CUT
    gap = narration - video

    if policy == DURATION_FREEZE and gap > 0.05:
        return DurationPlan(output_s=narration, freeze_s=gap, notes=(
            f"La narration dépasse la vidéo de {gap:.1f} s : la dernière image "
            "reste affichée jusqu'à la fin de la voix.",))

    if policy == DURATION_VIDEO or (policy == DURATION_FREEZE and gap <= 0.05):
        notes = ()
        if gap > 0.05:
            notes = (f"La narration est {gap:.1f} s plus longue que la vidéo : "
                     "elle sera coupée à la fin de l'image.",)
        elif gap < -0.05:
            notes = (f"La narration s'arrête {abs(gap):.1f} s avant la fin de la "
                     "vidéo : la suite reste muette.",)
        return DurationPlan(output_s=video, notes=notes)

    # DURATION_CUT
    output = min(video, narration)
    notes = ()
    if gap > 0.05:
        notes = (f"La narration est {gap:.1f} s plus longue que la vidéo : "
                 "elle sera coupée à la fin de l'image.",)
    elif gap < -0.05:
        notes = (f"La vidéo est {abs(gap):.1f} s plus longue que la narration : "
                 "elle sera coupée à la fin de la voix.",)
    return DurationPlan(output_s=output, notes=notes)


def resolve_audio_mode(mode: str, *, has_original_audio: bool,
                       has_narration: bool) -> tuple[str, tuple]:
    """Mode de son reellement applicable, et ce qu'il faut en dire.

    Un mode impossible n'est jamais applique en silence : une video muette
    mixee avec la narration donnerait la narration seule, et laisser croire au
    melange serait mentir sur le contenu du fichier produit.
    """
    mode = mode if mode in AUDIO_MODES else AUDIO_REPLACE
    notes: list = []

    if not has_original_audio and mode in (AUDIO_KEEP, AUDIO_MIX):
        if has_narration:
            notes.append("Cette vidéo n'a pas de piste audio : seule la narration "
                         "est présente dans le fichier produit.")
            return AUDIO_REPLACE, tuple(notes)
        notes.append("Cette vidéo n'a pas de piste audio, et aucune narration n'a "
                     "été générée : le fichier produit sera muet.")
        return AUDIO_KEEP, tuple(notes)

    if not has_narration and mode in (AUDIO_REPLACE, AUDIO_MIX):
        notes.append("Aucune narration n'a été générée : le son d'origine de la "
                     "vidéo est conservé.")
        return AUDIO_KEEP, tuple(notes)

    return mode, tuple(notes)


def build_audio_graph(mode: str, narration_index: int, *,
                      original_volume: float = DEFAULT_ORIGINAL_VOLUME,
                      narration_volume: float = DEFAULT_NARRATION_VOLUME) -> str:
    """Fragment de filtre produisant [aout], ou une chaine vide si muet.

    `apad` termine chaque branche : la piste audio ne doit jamais s'arreter
    avant l'image, sinon certains lecteurs coupent la video a la fin du son.
    La duree exacte est imposee par le `-t` de sortie, pas par cette rallonge.
    """
    original = max(0.0, min(4.0, float(original_volume)))
    narration = max(0.0, min(4.0, float(narration_volume)))

    if mode == AUDIO_KEEP:
        return "[0:a]apad[aout]"
    if mode == AUDIO_REPLACE:
        return f"[{narration_index}:a]volume={narration:.3f},apad[aout]"
    if mode == AUDIO_MIX:
        return (f"[0:a]volume={original:.3f}[amixsrc];"
                f"[{narration_index}:a]volume={narration:.3f}[amixnar];"
                "[amixsrc][amixnar]amix=inputs=2:duration=longest:normalize=0,"
                "alimiter=limit=0.95,apad[aout]")
    return ""


def build_video_graph(*, src_w: int, src_h: int, target_size: tuple,
                      fill: str, ass_path: str | None, face_hint=None,
                      framing_plan=None, freeze_s: float = 0.0,
                      fps: float = 25.0, fit: str = FIT_CROP) -> str:
    """Fragment de filtre produisant [vbase] a partir de l'entree video.

    La geometrie vient de video/filter_graph.build_video_chain : le meme code
    que les clips. `tpad` est place AVANT, sur la source : figer la derniere
    image doit se faire sur l'image d'origine, puis subir le recadrage et les
    sous-titres comme le reste -- pose apres, le gel n'aurait pas de
    sous-titres.
    """
    from editing.timeline import EditList
    from video.filter_graph import build_video_chain

    chain = build_video_chain(
        edit_list=EditList.identity(0.0, 1.0),
        framing_plan=framing_plan,
        zoom_track=None,
        src_w=src_w or 1920,
        src_h=src_h or 1080,
        fps=fps,
        face_hint=face_hint,
        ass_path=ass_path,
        target_size=tuple(target_size),
        fill=fill,
        fit=fit,
    )
    prefix = ""
    if freeze_s > 0.01:
        prefix = f"tpad=stop_mode=clone:stop_duration={freeze_s:.3f},"
    return f"[0:v]{prefix}{chain}[vbase]"


def build_render_args(*, video_path: str, narration_path: str | None,
                      ass_path: str | None, out_path: str,
                      src_w: int, src_h: int, target_size: tuple,
                      fill: str, audio_mode: str, duration_plan: DurationPlan,
                      export_settings: dict | None = None,
                      face_hint=None, framing_plan=None,
                      original_volume: float = DEFAULT_ORIGINAL_VOLUME,
                      narration_volume: float = DEFAULT_NARRATION_VOLUME,
                      watermark=None, fps: float = 25.0,
                      fit: str = FIT_CROP) -> list[str]:
    """Ligne de commande complete du rendu final, en un seul encodage."""
    export_settings = export_settings or {}
    uses_narration = bool(narration_path) and audio_mode in (AUDIO_REPLACE, AUDIO_MIX)

    args = ["-i", video_path]
    narration_index = 0
    if uses_narration:
        narration_index = len(args) // 2
        args += ["-i", narration_path]

    graph_parts = [build_video_graph(
        src_w=src_w, src_h=src_h, target_size=target_size, fill=fill,
        ass_path=ass_path, face_hint=face_hint, framing_plan=framing_plan,
        freeze_s=duration_plan.freeze_s, fps=fps, fit=fit,
    )]

    if watermark is not None:
        from video.watermark import overlay_position, prepare_filter

        logo_index = len(args) // 2
        args += ["-i", watermark.image]
        graph_parts.append(f"[{logo_index}:v]"
                           f"{prepare_filter(watermark, int(target_size[0]))}[wm]")
        graph_parts.append(f"[vbase][wm]overlay="
                           f"{overlay_position(watermark, int(target_size[0]), int(target_size[1]))}"
                           f"[vout]")
        video_label = "[vout]"
    else:
        graph_parts.append("[vbase]null[vout]")
        video_label = "[vout]"

    audio_graph = build_audio_graph(
        audio_mode, narration_index,
        original_volume=original_volume, narration_volume=narration_volume,
    ) if (uses_narration or audio_mode == AUDIO_KEEP) else ""
    if audio_graph:
        graph_parts.append(audio_graph)

    args += ["-filter_complex", ";".join(graph_parts), "-map", video_label]
    if audio_graph:
        args += ["-map", "[aout]"]
    else:
        args += ["-an"]

    args += ["-t", f"{max(0.05, duration_plan.output_s):.3f}"]
    args += [
        "-c:v", "libx264",
        "-preset", str(export_settings.get("video_preset", "medium")),
        "-crf", str(export_settings.get("video_bitrate_crf", 20)),
        # Une video YouTube peut arriver en VP9 10 bits : sans ce format
        # impose, libx264 produirait un fichier que la moitie des lecteurs et
        # des reseaux sociaux refuse. Le rendu des clips part d'un H.264 8 bits
        # et n'a jamais eu besoin de le dire.
        "-pix_fmt", "yuv420p",
    ]
    if audio_graph:
        args += ["-c:a", "aac", "-b:a", str(export_settings.get("audio_bitrate", "160k"))]
    args += ["-movflags", "+faststart", out_path]
    return args
