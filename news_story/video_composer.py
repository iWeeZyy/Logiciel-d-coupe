"""Post VIDEO 9:16 (TikTok, Reels, Story) : la video de l'article, avec
l'info par-dessus.

Demande explicite de l'utilisateur :
- que du 9:16 pour TikTok et Instagram ;
- une video 16:9 (bande-annonce) GARDE son format, centree sur un fond flou
  fait de la video elle-meme, plutot que d'etre recadree ;
- l'info de l'article est posee PAR-DESSUS la video, avec une opacite reduite
  pour que la video reste visible et comprehensible.

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

DEFAULT_TEXT_OPACITY = 0.6


def build_overlay(*, title: str, label: str = "", logo_path: Path | None = None,
                  text_opacity: float = DEFAULT_TEXT_OPACITY, title_scale: float = 1.0):
    """Calque RGBA 1080x1920 : etiquette + titre centres sur la video, a
    l'opacite demandee, et logo (opaque) sous la video. Image Pillow."""
    from PIL import Image, ImageDraw

    from news_story.post_composer import (_GAP_LABEL_TITLE, _TITLE_LINE_HEIGHT, _fit_title,
                                          _font, _french_spacing, _load_logo)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    center_x = (_MARGIN_LEFT + W - _MARGIN_RIGHT) / 2
    max_w = W - _MARGIN_LEFT - _MARGIN_RIGHT

    bottom = _SAFE_BOTTOM
    logo = _load_logo(logo_path)
    if logo is not None:
        overlay.paste(logo, (int(center_x - logo.width / 2), bottom - logo.height), logo)
        bottom -= logo.height + _GAP

    label = " ".join((label or "").split()).upper()
    label_font = _font(_LABEL_SIZE)
    label_h = (sum(label_font.getmetrics()) + _GAP_LABEL_TITLE) if label else 0

    title = _french_spacing(" ".join((title or "").split()).upper())
    lines, size, font = [], 0, None
    if title:
        font, lines, size, _ = _fit_title(
            draw, title, max(0.6, min(1.6, title_scale)), max_w, bottom - _SAFE_TOP - label_h,
            min_size=_TITLE_SIZES[1], max_lines=_MAX_LINES, max_size=_TITLE_SIZES[0])
    line_h = int(size * _TITLE_LINE_HEIGHT) if lines else 0
    block_h = label_h + line_h * len(lines)
    # Bloc centre sur l'ecran (donc sur la video, elle-meme centree), sans
    # jamais descendre sur le logo ni monter sous les onglets du haut.
    top = int(max(_SAFE_TOP, min(H / 2 - block_h / 2, bottom - block_h)))

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
    y = top + label_h
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    for line in lines:
        line_w = draw.textlength(line, font=font)
        x = center_x - line_w / 2
        offset = max(2, size // 28)
        shadow_draw.text((x + offset, y + offset), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=white)
        y += line_h

    # Texte et ombre a l'opacite demandee : la video reste visible a travers.
    alpha = max(0.1, min(1.0, float(text_opacity)))
    text_block = Image.alpha_composite(shadow_layer, text_layer)
    text_block.putalpha(text_block.getchannel("A").point(lambda a: int(a * alpha)))
    return Image.alpha_composite(text_block, overlay)


def _background_and_frame(still):
    """Fond flou 9:16 + image au centre en entier (meme rendu que le filtre
    ffmpeg de compose_video), a partir d'une image fixe."""
    from PIL import Image, ImageEnhance, ImageFilter

    still = still.convert("RGB")
    cover = max(W / still.width, H / still.height)
    bg = still.resize((max(1, round(still.width * cover)), max(1, round(still.height * cover))),
                      Image.LANCZOS)
    left, upper = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, upper, left + W, upper + H)).filter(ImageFilter.GaussianBlur(_BG_BLUR_RADIUS))
    bg = ImageEnhance.Brightness(bg).enhance(_BG_DARKEN)
    fit = min(W / still.width, H / still.height)
    fg = still.resize((max(1, round(still.width * fit)), max(1, round(still.height * fit))),
                      Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg


def compose_still(image_path, out_path, *, title: str, label: str = "",
                  logo_path: Path | None = None, text_opacity: float = DEFAULT_TEXT_OPACITY,
                  title_scale: float = 1.0, output_format: str = "PNG") -> Path:
    """Apercu du post video a partir d'une image (vignette de l'article)."""
    from PIL import Image

    out_path = Path(out_path)
    with Image.open(image_path) as opened:
        frame = _background_and_frame(opened)
    overlay = build_overlay(title=title, label=label, logo_path=logo_path,
                            text_opacity=text_opacity, title_scale=title_scale)
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out_path, format="JPEG" if output_format.upper() == "JPEG" else "PNG")
    return out_path


def ffmpeg_args(video_path: str, overlay_path: str, out_path: str) -> list[str]:
    """Arguments ffmpeg (sans le binaire) : fond flou 9:16 fait de la video,
    video entiere au centre (une 16:9 garde son format), calque par-dessus,
    son conserve s'il existe. Fonction pure, testee telle quelle."""
    brightness = _FFMPEG_BG_BRIGHTNESS
    graph = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=luma_radius=30:luma_power=2,eq=brightness={brightness:.2f}[bg];"
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
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
                  title_scale: float = 1.0, cancel_token=None) -> Path:
    """Rend le post video MP4 9:16. Leve FfmpegError / CancelledError."""
    import tempfile

    from video.ffmpeg_utils import run_ffmpeg

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overlay = build_overlay(title=title, label=label, logo_path=logo_path,
                            text_opacity=text_opacity, title_scale=title_scale)
    with tempfile.TemporaryDirectory(prefix="clipfarming_video_post_") as workdir:
        overlay_path = Path(workdir) / "overlay.png"
        overlay.save(overlay_path)
        run_ffmpeg(ffmpeg_args(str(video_path), str(overlay_path), str(out_path)),
                   "montage du post video 9:16", cancel_token=cancel_token)
    logger.info(f"Post video ecrit : {out_path}")
    return out_path
