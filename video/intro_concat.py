"""Pose une video d'intro DEVANT un clip deja rendu, par concatenation.

Le clip principal est un fichier fini (produit par un premier appel ffmpeg,
voir clip_builder.py) : ce module construit donc les arguments d'un SECOND
appel, qui normalise l'intro au format du clip puis les recolle. Deux passages
plutot qu'un seul graphe : l'intro et le clip n'ont ni la meme duree ni la
meme origine (un asset livre avec l'appli contre un rendu par-clip tout juste
produit), les fondre dans le graphe deja complexe de build_ffmpeg_args
n'aurait rien simplifie et aurait rendu ce graphe-la plus difficile a lire
pour un gain de qualite nul -- un second encodage de quelques secondes de plus
ne se voit pas.

Module PUR, meme famille que filter_graph.py : construit les arguments,
n'appelle jamais ffmpeg.
"""
from __future__ import annotations

from editing.timeline import EditList
from video.filter_graph import FILL_BLACK, FIT_WHOLE, build_video_chain


def build_concat_args(
    *,
    intro_path: str,
    intro_src_size: tuple[int, int],
    clip_path: str,
    clip_has_audio: bool,
    out_mp4_path: str,
    target_size: tuple[int, int],
    fps: float,
    fill: str = FILL_BLACK,
    watermark=None,
    export_settings: dict | None = None,
) -> list[str]:
    """Args ffmpeg qui ecrivent intro_path + clip_path bout a bout dans
    out_mp4_path.

    L'intro passe par EXACTEMENT le traitement d'une source dont le format ne
    correspond pas au format de sortie (`build_video_chain`, `fit="entier"`
    force) : elle ne doit jamais etre recadree ni suivie, seulement montree en
    entier. En 9:16 -- son format natif -- c'est une mise a l'echelle
    identite ; en 16:9 elle recoit le meme fond flou/noir qu'importe quelle
    source verticale postee en paysage.

    `clip_has_audio` decide s'il faut concatener une piste son : un clip sans
    audio (source muette, cas rare) sort sans audio plutot que de garder
    seulement les quelques secondes de son de l'intro, qui s'arreteraient net
    et surprendraient plus qu'un clip silencieux de bout en bout.

    `watermark`, s'il est fourni, n'est pose QUE sur l'intro -- jamais sur
    `[1:v]` (le clip). Le clip est deja un fichier fini a ce stade : son
    filigrane a ete incruste au premier passage (build_ffmpeg_args). Le poser
    une seconde fois ici le dedoublerait (deux logos superposes, plus opaque
    et legerement desaligne). Meme position que sur le clip (bas-centre par
    defaut) : la video d'intro n'a rien en bas de cadre qui l'occupe deja --
    son propre "+ Follow" est pose plus haut, au-dessus de l'anneau -- donc
    rien ne s'y superpose.
    """
    export_settings = export_settings or {}
    src_w, src_h = intro_src_size
    out_w, out_h = target_size

    intro_chain = build_video_chain(
        edit_list=EditList.identity(0.0, 1.0),  # non utilise : pas de suivi/zoom sur l'intro
        framing_plan=None, zoom_track=None,
        src_w=src_w, src_h=src_h, fps=fps, ass_path=None,
        target_size=target_size, fill=fill, fit=FIT_WHOLE, delire_plan=None,
    )

    args = ["-i", intro_path, "-i", clip_path]
    if watermark is not None:
        args += ["-i", watermark.image]

    if watermark is not None:
        from video.watermark import overlay_position, prepare_filter

        intro_video = (
            f"[0:v]{intro_chain},fps={fps:.4f},format=yuv420p[introbase];"
            f"[2:v]{prepare_filter(watermark, out_w, out_h)}[wmov];"
            f"[introbase][wmov]overlay={overlay_position(watermark, out_w, out_h)}[iv];"
        )
    else:
        intro_video = f"[0:v]{intro_chain},fps={fps:.4f},format=yuv420p[iv];"

    if clip_has_audio:
        graph = (
            intro_video +
            "[0:a]aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[ia];"
            f"[1:v]fps={fps:.4f},format=yuv420p[cv];"
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[ca];"
            "[iv][ia][cv][ca]concat=n=2:v=1:a=1[outv][outa]"
        )
        args += ["-filter_complex", graph, "-map", "[outv]", "-map", "[outa]"]
    else:
        graph = (
            intro_video +
            f"[1:v]fps={fps:.4f},format=yuv420p[cv];"
            "[iv][cv]concat=n=2:v=1:a=0[outv]"
        )
        args += ["-filter_complex", graph, "-map", "[outv]"]

    args += [
        "-c:v", "libx264",
        "-preset", str(export_settings.get("video_preset", "medium")),
        "-crf", str(export_settings.get("video_bitrate_crf", 20)),
    ]
    if clip_has_audio:
        args += ["-c:a", "aac", "-b:a", str(export_settings.get("audio_bitrate", "160k"))]
    args += ["-movflags", "+faststart", out_mp4_path]
    return args
