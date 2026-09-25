#!/usr/bin/env python3
"""Construit l'incrustation intro (assets/branding/intro_follow.mp4 + son
masque intro_follow_mask.mp4) a partir de la source FOND VERT
(tools/source/ClipsOfStreams_Follow_GreenScreen.mp4).

RUN A LA MAIN, PAS AU BUILD -- meme convention que le reste de tools/ : les
fichiers produits sont COMMITES.

Remplace tools/generate_intro_mask.py, qui detourait l'ancienne source a
fond NOIR par remplissage depuis les bords : le noir du fond et le noir du
disque du logo se confondaient, d'ou l'aplat noir signale sur les clips
9:16. Un fond vert pur (0,255,0) n'a pas ce probleme -- aucune couleur du
logo n'en est proche -- donc l'alpha se calcule directement, pixel par
pixel, sans heuristique.

LA SOURCE FOND VERT A ELLE-MEME DES DEFAUTS (animation generee), corriges
ici plutot que conserves :
  - la pastille garde son "+" une fois passee a "Following" (le "+" devient
    un "✓" ici) ;
  - le texte "Following" deborde de la pastille pendant la transition, et
    son "g" final est teinte de vert -- il deviendrait transparent au
    detourage ;
  - arc sombre parasite au-dessus du bouton "+", bord superieur de la
    pastille dentele ;
  - curseur fantome qui traverse le logo avant le clic, trainee rose ;
  - 4,7 s d'image quasi fixe avant l'action, apparition et disparition
    seches (premiere et derniere image), piste audio muette.
Seul le CORPS (anneau neon + logo + etincelles) est repris de la source, sur
sa portion propre (avant l'apparition du curseur). La pastille, le curseur,
le clic et les transitions sont redessines ici, en vectoriel.

Usage :
    .venv/bin/python tools/build_intro_asset.py [--green chemin.mp4]
--green ecrit en plus une version fond vert 1080x1920 de la nouvelle
animation (pour un logiciel de montage).
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE_VIDEO = ROOT / "tools" / "source" / "ClipsOfStreams_Follow_GreenScreen.mp4"
OUT_VIDEO = ROOT / "assets" / "branding" / "intro_follow.mp4"
OUT_MASK = ROOT / "assets" / "branding" / "intro_follow_mask.mp4"

FRAME_W, FRAME_H = 1080, 1920
FPS = 30
DURATION = 7.0
N_FRAMES = int(round(DURATION * FPS))

# Meme cadre que video/intro_overlay.py (_CROP_*) : seule cette zone est
# utilisee par l'appli, tout le rendu se fait dedans.
CROP_X, CROP_Y, CROP_W, CROP_H = 40, 330, 1000, 1140

# Portion propre de la source : avant ~3,6 s le curseur n'est pas encore
# entre dans le cadre. Jouee en aller-retour pour couvrir toute la duree.
BODY_FIRST, BODY_LAST = 0, 104

# Geometrie mesuree sur la source (coordonnees 1080x1920).
RING_CX, RING_CY, RING_R = 545, 964, 357
BTN_CX, BTN_CY, BTN_R = 388, 584, 82
BAR_X0, BAR_Y0, BAR_Y1 = 392, 517, 651
OLD_PILL_RIGHT = 766          # l'ancienne pastille "Follow" finit a x=762
TEXT_X = 500
FONT_SIZE = 62
PIVOT = (RING_CX, 900)        # centre visuel de l'ensemble pastille + anneau

BAR_COLOR = np.array([48, 9, 23], np.float32)
PINK = (255, 64, 118)

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


# --------------------------------------------------------------- utilitaires
def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _seg(t: float, t0: float, t1: float) -> float:
    return _clamp01((t - t0) / (t1 - t0)) if t1 > t0 else float(t >= t1)


def _ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


def _ease_in_out(x: float) -> float:
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def _ease_out_back(x: float, s: float = 1.7) -> float:
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def _font() -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, FONT_SIZE)
    sys.exit("Aucune police grasse trouvee (Liberation Sans / Arial / DejaVu).")


def _local(x: float, y: float) -> tuple[float, float]:
    """Coordonnees image 1080x1920 -> coordonnees dans le cadre utile."""
    return x - CROP_X, y - CROP_Y


# ------------------------------------------------------------ corps (source)
def _key_green(rgb: np.ndarray) -> np.ndarray:
    """RGBA (droit, non premultiplie) d'une image sur fond vert pur.

    Alpha = 1 - exces de vert ; la couleur de premier plan est retrouvee en
    retirant la part de vert melangee (P = a*F + (1-a)*vert), puis le vert
    residuel des bords est plafonne (despill)."""
    a = rgb.astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    excess = g - np.maximum(r, b)
    alpha = 1.0 - np.clip((excess - 10.0) / 235.0, 0.0, 1.0)
    bg = np.array([0.0, 255.0, 0.0], np.float32)
    fg = (a - (1.0 - alpha[..., None]) * bg) / np.maximum(alpha[..., None], 1e-3)
    fg = np.clip(fg, 0, 255)
    edge = alpha < 0.98
    fg[..., 1] = np.where(edge, np.minimum(fg[..., 1], np.maximum(fg[..., 0], fg[..., 2])), fg[..., 1])
    return np.dstack([fg, alpha * 255.0]).astype(np.uint8)


def _old_pill_erase_mask() -> np.ndarray:
    """1 = garder, 0 = effacer : la pastille d'origine (et ses artefacts)
    au-dessus de l'anneau, remplacee par la pastille redessinee. Bords
    adoucis pour ne pas entailler le halo de l'anneau."""
    yy, xx = np.mgrid[0:CROP_H, 0:CROP_W].astype(np.float32)
    xx += CROP_X
    yy += CROP_Y
    dist = np.hypot(xx - RING_CX, yy - RING_CY)
    outside_ring = np.clip((dist - (RING_R + 2)) / 8.0, 0, 1)
    in_x = np.clip((xx - 296) / 10.0, 0, 1) * np.clip((780 - xx) / 10.0, 0, 1)
    in_y = np.clip((668 - yy) / 10.0, 0, 1)
    return 1.0 - outside_ring * in_x * in_y


def _load_body(tmp: Path) -> list[Image.Image]:
    frames_dir = tmp / "src"
    frames_dir.mkdir()
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(SOURCE_VIDEO),
           "-vf", f"crop={CROP_W}:{CROP_H}:{CROP_X}:{CROP_Y}",
           "-frames:v", str(BODY_LAST + 1), "-vsync", "0",
           str(frames_dir / "f_%04d.png")]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("ffmpeg : extraction de la source impossible")
    erase = _old_pill_erase_mask()
    body = []
    for i in range(BODY_FIRST, BODY_LAST + 1):
        rgba = _key_green(np.array(Image.open(frames_dir / f"f_{i + 1:04d}.png").convert("RGB")))
        rgba[..., 3] = (rgba[..., 3].astype(np.float32) * erase).astype(np.uint8)
        body.append(Image.fromarray(rgba, "RGBA"))
    return body


# ------------------------------------------------------- pastille redessinee
SS = 3  # sur-echantillonnage du dessin vectoriel


class Pill:
    def __init__(self) -> None:
        self.font = _font()
        self.big_font = ImageFont.truetype(self.font.path, FONT_SIZE * SS)
        self.w_follow = self.font.getlength("Follow")
        self.w_following = self.font.getlength("Following")
        self.end_follow = max(OLD_PILL_RIGHT, TEXT_X + self.w_follow + 58)
        self.end_following = TEXT_X + self.w_following + 58

    def render(self, *, reveal: float, width_mix: float, text_mix: float,
               btn_scale: float, icon_mix: float, hover: float) -> Image.Image:
        """La pastille seule, sur un calque RGBA de la taille du cadre."""
        x_left, y_top = BTN_CX - BTN_R - 30, BAR_Y0 - 40
        x_right, y_bot = int(self.end_following) + 40, BTN_CY + BTN_R + 40
        w, h = (x_right - x_left) * SS, (y_bot - y_top) * SS

        def p(x: float, y: float) -> tuple[float, float]:
            return (x - x_left) * SS, (y - y_top) * SS

        full_end = self.end_follow + (self.end_following - self.end_follow) * width_mix
        bar_end = BTN_CX + (full_end - BTN_CX) * reveal
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        # Barre : degrade vertical discret + liseré rose.
        bar_mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(bar_mask).rounded_rectangle(
            [*p(BAR_X0, BAR_Y0), *p(max(bar_end, BAR_X0 + 1), BAR_Y1)],
            radius=(BAR_Y1 - BAR_Y0) * SS // 2, fill=255)
        grad = np.linspace(1.35, 0.85, h, dtype=np.float32)[:, None, None]
        bar_rgb = np.clip(BAR_COLOR[None, None, :] * grad, 0, 255) * np.ones((1, w, 1), np.float32)
        bar = Image.fromarray(np.dstack([bar_rgb, np.array(bar_mask, np.float32)]).astype(np.uint8), "RGBA")
        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow.putalpha(bar_mask.filter(ImageFilter.GaussianBlur(9 * SS)).point(lambda v: int(v * 0.55)))
        layer.alpha_composite(shadow, (0, 6 * SS))
        layer.alpha_composite(bar)
        rim = Image.new("L", (w, h), 0)
        ImageDraw.Draw(rim).rounded_rectangle(
            [*p(BAR_X0, BAR_Y0), *p(max(bar_end, BAR_X0 + 1), BAR_Y1)],
            radius=(BAR_Y1 - BAR_Y0) * SS // 2, outline=255, width=2 * SS)
        layer.alpha_composite(Image.merge("RGBA", [
            Image.new("L", (w, h), c) for c in PINK] + [rim.point(lambda v: int(v * 0.45))]))

        # Texte, DECOUPE par la barre : ne deborde jamais (defaut de la source).
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        td = ImageDraw.Draw(text_layer)
        ty = BTN_CY
        # L'un APRES l'autre (sortie par le haut, entree par le bas) : un
        # fondu croise superposait les deux mots, illisible.
        out_k, in_k = _clamp01(text_mix * 2), _clamp01(text_mix * 2 - 1)
        for word, a, dy in (("Follow", 1.0 - out_k, -18 * out_k),
                            ("Following", in_k, 18 * (1.0 - in_k))):
            if a <= 0.01:
                continue
            td.text(p(TEXT_X, ty + dy), word, font=self.big_font, anchor="lm",
                    fill=(255, 255, 255, int(255 * a * reveal)))
        text_alpha = np.minimum(np.array(text_layer.getchannel("A")),
                                np.array(bar_mask.filter(ImageFilter.GaussianBlur(2 * SS))))
        text_layer.putalpha(Image.fromarray(text_alpha))
        layer.alpha_composite(text_layer)

        # Bouton rond : degrade radial, liseré clair, ombre portee.
        r = BTN_R * btn_scale
        cx, cy = p(BTN_CX, BTN_CY)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.hypot(xx - cx, yy - cy)
        disc = np.clip((r * SS - d) / (1.2 * SS), 0, 1)
        hl = np.hypot(xx - (cx - 0.35 * r * SS), yy - (cy - 0.45 * r * SS)) / (1.9 * r * SS)
        hl = np.clip(hl, 0, 1)[..., None]
        top = np.array([255, 96, 140], np.float32) * (1 + 0.12 * hover)
        bottom = np.array([214, 12, 58], np.float32) * (1 + 0.12 * hover)
        btn_rgb = np.clip(top * (1 - hl) + bottom * hl, 0, 255)
        btn_shadow = Image.fromarray((disc * 150).astype(np.uint8)).filter(ImageFilter.GaussianBlur(8 * SS))
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.putalpha(btn_shadow)
        layer.alpha_composite(sh, (0, 5 * SS))
        layer.alpha_composite(Image.fromarray(np.dstack([btn_rgb, disc * 255]).astype(np.uint8), "RGBA"))
        ring = np.clip(1 - np.abs(d - (r * SS - 3 * SS)) / (1.6 * SS), 0, 1) * 0.55
        layer.alpha_composite(Image.fromarray(np.dstack([
            np.full((h, w), 255, np.float32), np.full((h, w), 170, np.float32),
            np.full((h, w), 195, np.float32), ring * 255]).astype(np.uint8), "RGBA"))

        # Icone : le "+" se retracte et s'efface, puis le "✓" se trace (pas
        # de rotation : a mi-course un "+" tourne se lit comme un "×").
        icon = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        idr = ImageDraw.Draw(icon)
        stroke = int(13 * SS * btn_scale)
        shrink = _ease_out(_clamp01(icon_mix * 2.5))
        plus_a = 1.0 - shrink
        if plus_a > 0.01:
            arm = 30 * SS * btn_scale * (1 - 0.7 * shrink)
            _round_line(idr, (cx - arm, cy), (cx + arm, cy), stroke, int(255 * plus_a))
            _round_line(idr, (cx, cy - arm), (cx, cy + arm), stroke, int(255 * plus_a))
        check_p = _clamp01((icon_mix - 0.35) / 0.65)
        if check_p > 0:
            s = SS * btn_scale
            pts = [(cx - 27 * s, cy + 1 * s), (cx - 8 * s, cy + 20 * s), (cx + 28 * s, cy - 18 * s)]
            _partial_polyline(idr, pts, _ease_out(check_p), stroke)
        layer.alpha_composite(icon)

        if reveal < 1.0:
            a = np.array(layer.getchannel("A"), np.float32) * _clamp01(reveal * 1.5)
            layer.putalpha(Image.fromarray(a.astype(np.uint8)))

        small = layer.resize((w // SS, h // SS), Image.LANCZOS)
        out = Image.new("RGBA", (CROP_W, CROP_H), (0, 0, 0, 0))
        out.alpha_composite(small, tuple(int(v) for v in _local(x_left, y_top)))
        return out


def _round_line(draw: ImageDraw.ImageDraw, a, b, width: int, alpha: int = 255) -> None:
    fill = (255, 255, 255, alpha)
    draw.line([a, b], fill=fill, width=width)
    for x, y in (a, b):
        draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=fill)


def _partial_polyline(draw, pts, progress: float, width: int) -> None:
    lengths = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    remaining = sum(lengths) * progress
    for (a, b), seg in zip(zip(pts, pts[1:]), lengths):
        if remaining <= 0:
            break
        f = min(1.0, remaining / seg)
        end = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        _round_line(draw, a, end, width)
        remaining -= seg


# --------------------------------------------------------- curseur et effets
_ARROW = [(0, 0), (0, 54), (13, 42), (23, 63), (33, 58), (23, 38), (40, 38)]


def _cursor(tip: tuple[float, float], scale: float, alpha: float) -> Image.Image:
    layer = Image.new("RGBA", (CROP_W, CROP_H), (0, 0, 0, 0))
    if alpha <= 0.01:
        return layer
    k = 1.3 * scale * SS
    tx, ty = _local(*tip)
    x0, y0 = int(tx) - 20, int(ty) - 20
    size = (int(90 * 1.3) + 40) * SS
    big = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pts = [((px * k) + 20 * SS + (tx - int(tx)) * SS, (py * k) + 20 * SS + (ty - int(ty)) * SS) for px, py in _ARROW]
    sh = Image.new("L", (size, size), 0)
    ImageDraw.Draw(sh).polygon([(x + 4 * SS, y + 6 * SS) for x, y in pts], fill=110)
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow.putalpha(sh.filter(ImageFilter.GaussianBlur(5 * SS)))
    big.alpha_composite(shadow)
    d = ImageDraw.Draw(big)
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(15, 15, 18, 255), width=int(3.2 * SS))
    small = big.resize((size // SS, size // SS), Image.LANCZOS)
    a = np.array(small.getchannel("A"), np.float32) * alpha
    small.putalpha(Image.fromarray(a.astype(np.uint8)))
    layer.alpha_composite(small, (x0, y0))
    return layer


_rng = np.random.default_rng(7)
_PARTICLES = [(_rng.uniform(0, 2 * math.pi), _rng.uniform(120, 230), _rng.uniform(3.0, 6.5),
               (255, 255, 255) if i % 3 == 0 else (255, 120, 170)) for i in range(14)]


def _click_fx(t_since: float) -> Image.Image:
    """Onde de choc + etincelles au clic."""
    layer = Image.new("RGBA", (CROP_W, CROP_H), (0, 0, 0, 0))
    if t_since < 0 or t_since > 0.7:
        return layer
    d = ImageDraw.Draw(layer)
    cx, cy = _local(BTN_CX, BTN_CY)
    k = _ease_out(_clamp01(t_since / 0.5))
    if t_since <= 0.5:
        r = BTN_R + 75 * k
        wdt = max(1, int(9 * (1 - k)))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*PINK, int(220 * (1 - k))), width=wdt)
    k2 = _ease_out(_clamp01(t_since / 0.7))
    for ang, dist, size, color in _PARTICLES:
        rr = BTN_R * 0.8 + dist * k2
        px, py = cx + math.cos(ang) * rr, cy + math.sin(ang) * rr
        s = size * (1 - 0.6 * k2)
        d.ellipse([px - s, py - s, px + s, py + s], fill=(*color, int(255 * (1 - k2))))
    return layer.filter(ImageFilter.GaussianBlur(0.6))


# ------------------------------------------------------------------ timeline
T_CLICK = 2.35
CURSOR_FROM = (905.0, 1120.0)
CURSOR_ON = (BTN_CX + 14.0, BTN_CY + 10.0)
CURSOR_TO = (980.0, 900.0)


def _state(t: float) -> dict:
    enter = _seg(t, 0.0, 0.55)
    leave = _seg(t, DURATION - 0.55, DURATION - 0.05)
    scale = (0.55 + 0.45 * _ease_out_back(enter)) * (1 - 0.15 * _ease_in_out(leave))
    opacity = _clamp01(enter * 2.2) * (1 - _ease_in_out(leave))

    # Curseur : arrive, survole, clique, repart.
    if t < T_CLICK:
        k = _ease_in_out(_seg(t, 1.25, 2.1))
        pos = (CURSOR_FROM[0] + (CURSOR_ON[0] - CURSOR_FROM[0]) * k,
               CURSOR_FROM[1] + (CURSOR_ON[1] - CURSOR_FROM[1]) * k)
        c_alpha = _seg(t, 1.25, 1.5)
    else:
        k = _ease_in_out(_seg(t, T_CLICK + 0.4, T_CLICK + 1.1))
        pos = (CURSOR_ON[0] + (CURSOR_TO[0] - CURSOR_ON[0]) * k,
               CURSOR_ON[1] + (CURSOR_TO[1] - CURSOR_ON[1]) * k)
        c_alpha = 1 - _seg(t, T_CLICK + 0.7, T_CLICK + 1.1)
    press = max(0.0, 1 - abs(t - (T_CLICK + 0.06)) / 0.1)
    return dict(
        scale=scale, opacity=opacity,
        reveal=_ease_out(_seg(t, 0.35, 0.9)),
        width_mix=_ease_out(_seg(t, T_CLICK + 0.05, T_CLICK + 0.4)),
        text_mix=_ease_in_out(_seg(t, T_CLICK + 0.05, T_CLICK + 0.35)),
        icon_mix=_seg(t, T_CLICK + 0.02, T_CLICK + 0.45),
        btn_scale=1 - 0.1 * press + 0.07 * math.sin(math.pi * _seg(t, T_CLICK + 0.1, T_CLICK + 0.45)),
        hover=_seg(t, 1.95, 2.15) * (1 - _seg(t, T_CLICK + 0.3, T_CLICK + 0.6)),
        cursor=pos, cursor_alpha=c_alpha, cursor_scale=1 - 0.14 * press,
        fx=t - T_CLICK,
    )


def _transform(img: Image.Image, scale: float) -> Image.Image:
    """Mise a l'echelle autour du pivot, en alpha premultiplie (pas de
    liseré sombre sur les bords adoucis)."""
    if abs(scale - 1.0) < 1e-4:
        return img
    px, py = _local(*PIVOT)
    inv = 1.0 / max(scale, 1e-3)
    pre = img.convert("RGBa")
    out = pre.transform(img.size, Image.AFFINE,
                        (inv, 0, px - px * inv, 0, inv, py - py * inv), resample=Image.BICUBIC)
    return out.convert("RGBA")


def render_frames(body: list[Image.Image]):
    pill = Pill()
    cycle = list(range(len(body))) + list(range(len(body) - 2, 0, -1))
    for n in range(N_FRAMES):
        t = n / FPS
        s = _state(t)
        frame = body[cycle[n % len(cycle)]].copy()
        frame.alpha_composite(pill.render(reveal=s["reveal"], width_mix=s["width_mix"],
                                          text_mix=s["text_mix"], btn_scale=s["btn_scale"],
                                          icon_mix=s["icon_mix"], hover=s["hover"]))
        frame.alpha_composite(_click_fx(s["fx"]))
        frame = _transform(frame, s["scale"])
        frame.alpha_composite(_cursor(s["cursor"], s["cursor_scale"], s["cursor_alpha"] * s["opacity"]))
        if s["opacity"] < 1.0:
            a = np.array(frame.getchannel("A"), np.float32) * s["opacity"]
            frame.putalpha(Image.fromarray(a.astype(np.uint8)))
        yield frame


# ----------------------------------------------------------------- encodage
def _bleed(rgba: np.ndarray) -> np.ndarray:
    """Couleur des pixels transparents = couleur voisine du contenu : evite
    qu'un sous-echantillonnage de chrominance (yuv420p) ou une mise a
    l'echelle ne tire les bords vers le noir."""
    rgb = rgba[..., :3].astype(np.float32)
    a = rgba[..., 3].astype(np.float32)[..., None] / 255.0
    img = Image.fromarray(np.clip(rgb * a, 0, 255).astype(np.uint8))
    blur_rgb = np.array(img.filter(ImageFilter.GaussianBlur(6)), np.float32)
    blur_a = np.array(Image.fromarray((a[..., 0] * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(6)), np.float32)[..., None] / 255.0
    fill = blur_rgb / np.maximum(blur_a, 1e-3)
    return np.clip(np.where(a > 0.02, rgb, fill), 0, 255).astype(np.uint8)


def _encoder(path: Path, pix_in: str, crf: int, preset: str = "slow",
             size: tuple[int, int] = (FRAME_W, FRAME_H)) -> subprocess.Popen:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_in,
           "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--green", type=Path, help="ecrit aussi une version fond vert")
    args = parser.parse_args()
    if not SOURCE_VIDEO.is_file():
        sys.exit(f"Introuvable : {SOURCE_VIDEO}")

    with tempfile.TemporaryDirectory() as tmp:
        body = _load_body(Path(tmp))

    OUT_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    video = _encoder(OUT_VIDEO, "rgb24", 14)
    # Le masque est au format du CADRE (video/intro_overlay.py le met a
    # l'echelle sans le rogner). crf 0 = sans perte : un masque degrade
    # dessinerait un liseré de bruit exactement sur le contour.
    mask = _encoder(OUT_MASK, "gray", 0, "veryslow", size=(CROP_W, CROP_H))
    green = _encoder(args.green, "rgb24", 14) if args.green else None

    for n, crop in enumerate(render_frames(body), start=1):
        rgba = np.array(crop)
        full_rgb = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
        full_a = np.zeros((FRAME_H, FRAME_W), np.uint8)
        full_rgb[CROP_Y:CROP_Y + CROP_H, CROP_X:CROP_X + CROP_W] = _bleed(rgba)
        full_a[CROP_Y:CROP_Y + CROP_H, CROP_X:CROP_X + CROP_W] = rgba[..., 3]
        video.stdin.write(full_rgb.tobytes())
        mask.stdin.write(np.ascontiguousarray(rgba[..., 3]).tobytes())
        if green:
            a = full_a.astype(np.float32)[..., None] / 255.0
            bg = np.array([0, 255, 0], np.float32)
            comp = full_rgb.astype(np.float32) * a + bg * (1 - a)
            green.stdin.write(np.clip(comp + 0.5, 0, 255).astype(np.uint8).tobytes())
        if n % 30 == 0 or n == N_FRAMES:
            print(f"  image {n}/{N_FRAMES}")

    for proc in (video, mask, green):
        if proc:
            proc.stdin.close()
            if proc.wait() != 0:
                sys.exit("ffmpeg (encodage) a echoue")
    print(f"Ecrit {OUT_VIDEO} et {OUT_MASK} ({N_FRAMES} images, {FPS} fps)")


if __name__ == "__main__":
    main()
