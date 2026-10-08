"""Post VIDEO 9:16 (TikTok, Reels, Story) : la video de l'article, avec
l'info au-dessus.

Demande explicite de l'utilisateur :
- que du 9:16 pour TikTok et Instagram ;
- une video 16:9 (bande-annonce) GARDE son format, centree sur un fond flou
  fait de la video elle-meme, plutot que d'etre recadree ;
- l'info de l'article (titre + chapo) est dans la bande floue AU-DESSUS de
  la video, jamais sur elle, pour que la video reste visible et
  comprehensible (« utiliser les bandes floues pour occuper l'espace »).

Decoupage : le texte et le logo sont dessines une seule fois dans un calque
PNG transparent (Pillow, memes polices et memes regles que le post image,
news_story/post_composer.py) ; ffmpeg fait le reste en une passe (fond flou,
video au centre, calque par-dessus, son conserve). Pour l'apercu, la meme
composition est faite en image a partir de la vignette de l'article
(compose_still) -- pas besoin de telecharger la video pour regler le texte.
"""
from __future__ import annotations

from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

W, H = 1080, 1920
# Zones sures TikTok / Reels (voir post_composer) : bas de l'ecran et
# colonne de boutons a droite recouverts par l'interface, onglets en haut.
_MARGIN_LEFT, _MARGIN_RIGHT = 64, 140
_SAFE_TOP = int(H * 0.10)
_SAFE_BOTTOM = H - 440
_TITLE_SIZES = (90, 44)
_MAX_LINES = 14
_GAP = 30
_LABEL_SIZE = 56
_LABEL_GAP = 26
_RULE_THICKNESS = 3
_BG_BLUR_RADIUS = 40
_BG_DARKEN = 0.72                # fond flou legerement assombri (apercu Pillow)
_FFMPEG_BG_BRIGHTNESS = -0.15    # meme intention cote ffmpeg (filtre eq)

# Le texte est desormais AU-DESSUS de la video, dans la bande floue (retour
# utilisateur : « le titre ne doit pas etre SUR la video ») : plus besoin de
# transparence par defaut. Le reglage reste disponible.
DEFAULT_TEXT_OPACITY = 1.0
_MIN_TEXT_ZONE = 380             # hauteur minimale reservee au texte au-dessus


def video_box(source_size: tuple[int, int], has_logo: bool = True,
              logo_h: int = 0) -> tuple[int, int, int, int]:
    """(x, y, largeur, hauteur) de la video dans le 9:16 : pleine largeur
    pour une 16:9, posee en bas de la zone visible (au-dessus du logo et de
    l'interface TikTok) pour laisser la bande floue du haut au texte."""
    src_w, src_h = source_size
    bottom = _SAFE_BOTTOM - ((logo_h + _GAP) if has_logo else 0)
    max_h = bottom - _SAFE_TOP - _MIN_TEXT_ZONE
    scale = min(W / src_w, max_h / src_h)
    w = min(W, max(2, round(src_w * scale / 2) * 2))
    h = min(max_h, max(2, round(src_h * scale / 2) * 2))
    return (W - w) // 2, bottom - h, w, h


def build_overlay(*, title: str, label: str = "", logo_path: Path | None = None,
                  text_opacity: float = DEFAULT_TEXT_OPACITY, title_scale: float = 1.0,
                  subtitle: str = "", source_size: tuple[int, int] = (16, 9)):
    """Calque RGBA 1080x1920 et position de la video : etiquette, titre et
    chapo dans la bande floue AU-DESSUS de la video ; logo sous la video.
    Renvoie (image Pillow, (x, y, w, h) de la video)."""
    from PIL import Image, ImageDraw

    from news_story.post_composer import (_GAP_LABEL_TITLE, _fit_block, _font, _french_spacing,
                                          _load_logo, draw_block)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw, shadow_draw = ImageDraw.Draw(text_layer), ImageDraw.Draw(shadow_layer)
    center_x = (_MARGIN_LEFT + W - _MARGIN_RIGHT) / 2
    max_w = W - _MARGIN_LEFT - _MARGIN_RIGHT

    logo = _load_logo(logo_path)
    box = video_box(source_size, has_logo=logo is not None, logo_h=logo.height if logo else 0)
    vx, vy, vw, vh = box
    if logo is not None:
        overlay.paste(logo, (int(center_x - logo.width / 2), vy + vh + _GAP), logo)

    label = " ".join((label or "").split()).upper()
    label_font = _font(_LABEL_SIZE)
    label_h = (sum(label_font.getmetrics()) + _GAP_LABEL_TITLE) if label else 0
    title = _french_spacing(" ".join((title or "").split()).upper())
    subtitle = _french_spacing(" ".join((subtitle or "").split()))

    zone_top, zone_bottom = _SAFE_TOP, vy - _GAP
    block, block_h = None, 0
    if title or subtitle:
        block = _fit_block(draw, title, subtitle, max(0.6, min(1.6, title_scale)), max_w,
                           max(zone_bottom - zone_top - label_h, 0),
                           min_size=_TITLE_SIZES[1], max_size=_TITLE_SIZES[0],
                           max_lines=_MAX_LINES)
        block_h = block[6]
    total_h = label_h + block_h
    # Bloc centre dans la bande du haut, jamais sur la video.
    top = int(max(zone_top, zone_top + (zone_bottom - zone_top - total_h) / 2))

    white = (255, 255, 255, 255)
    if label:
        ascent, descent = label_font.getmetrics()
        text_w = draw.textlength(label, font=label_font)
        x_text = center_x - text_w / 2
        draw.text((x_text, top), label, font=label_font, fill=white)
        y_rule = top + (ascent + descent) // 2 + 2
        left_end, right_start = x_text - _LABEL_GAP, x_text + text_w + _LABEL_GAP
        if left_end > _MARGIN_LEFT:
            draw.rectangle([_MARGIN_LEFT, y_rule, left_end, y_rule + _RULE_THICKNESS - 1], fill=white)
        if right_start < W - _MARGIN_RIGHT:
            draw.rectangle([right_start, y_rule, W - _MARGIN_RIGHT, y_rule + _RULE_THICKNESS - 1],
                           fill=white)
    if block is not None:
        draw_block(draw, top + label_h, center_x, block, fill=white,
                   shadow_fill=(0, 0, 0, 255), shadow_draw=shadow_draw)

    alpha = max(0.1, min(1.0, float(text_opacity)))
    text_block = Image.alpha_composite(shadow_layer, text_layer)
    if alpha < 1.0:
        text_block.putalpha(text_block.getchannel("A").point(lambda a: int(a * alpha)))
    return Image.alpha_composite(text_block, overlay), box


def _background_and_frame(still, box):
    """Fond flou 9:16 + image a la place de la video (meme rendu que le
    filtre ffmpeg de compose_video), a partir d'une image fixe."""
    from PIL import Image, ImageEnhance, ImageFilter

    still = still.convert("RGB")
    cover = max(W / still.width, H / still.height)
    bg = still.resize((max(1, round(still.width * cover)), max(1, round(still.height * cover))),
                      Image.LANCZOS)
    left, upper = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, upper, left + W, upper + H)).filter(ImageFilter.GaussianBlur(_BG_BLUR_RADIUS))
    bg = ImageEnhance.Brightness(bg).enhance(_BG_DARKEN)
    x, y, w, h = box
    bg.paste(still.resize((w, h), Image.LANCZOS), (x, y))
    return bg


def compose_still(image_path, out_path, *, title: str, label: str = "",
                  logo_path: Path | None = None, text_opacity: float = DEFAULT_TEXT_OPACITY,
                  title_scale: float = 1.0, output_format: str = "PNG",
                  subtitle: str = "") -> Path:
    """Apercu du post video a partir d'une image (vignette de l'article)."""
    from PIL import Image

    out_path = Path(out_path)
    with Image.open(image_path) as opened:
        still = opened.convert("RGB")
    overlay, box = build_overlay(title=title, label=label, logo_path=logo_path,
                                 text_opacity=text_opacity, title_scale=title_scale,
                                 subtitle=subtitle, source_size=still.size)
    frame = _background_and_frame(still, box)
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out_path, format="JPEG" if output_format.upper() == "JPEG" else "PNG")
    return out_path


def ffmpeg_args(video_path: str, overlay_path: str, out_path: str,
                box: tuple[int, int, int, int]) -> list[str]:
    """Arguments ffmpeg (sans le binaire) : fond flou 9:16 fait de la video,
    video entiere a la place `box` (une 16:9 garde son format, sous la bande
    de texte), calque par-dessus, son conserve s'il existe. Fonction pure."""
    x, y, w, h = box
    graph = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=luma_radius=30:luma_power=2,eq=brightness={_FFMPEG_BG_BRIGHTNESS:.2f}[bg];"
        f"[0:v]scale={w}:{h}[fg];"
        f"[bg][fg]overlay={x}:{y}[base];"
        f"[base][1:v]overlay=0:0,format=yuv420p[v]"
    )
    return [
        "-i", str(video_path), "-i", str(overlay_path),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out_path),
    ]


def compose_video(video_path, out_path, *, title: str, label: str = "",
                  logo_path: Path | None = None, text_opacity: float = DEFAULT_TEXT_OPACITY,
                  title_scale: float = 1.0, subtitle: str = "", cancel_token=None) -> Path:
    """Rend le post video MP4 9:16. Leve FfmpegError / CancelledError."""
    import tempfile

    from video.ffmpeg_utils import run_ffmpeg, video_resolution

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overlay, box = build_overlay(title=title, label=label, logo_path=logo_path,
                                 text_opacity=text_opacity, title_scale=title_scale,
                                 subtitle=subtitle, source_size=video_resolution(str(video_path)))
    with tempfile.TemporaryDirectory(prefix="clipfarming_video_post_") as workdir:
        overlay_path = Path(workdir) / "overlay.png"
        overlay.save(overlay_path)
        run_ffmpeg(ffmpeg_args(str(video_path), str(overlay_path), str(out_path), box),
                   "montage du post video 9:16", cancel_token=cancel_token)
    logger.info(f"Post video ecrit : {out_path}")
    return out_path
