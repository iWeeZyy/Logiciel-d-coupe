"""Fleche rouge courbee vers le « + » d'abonnement de TikTok (animation
« Trace », validee par l'utilisateur le 2026-11-02), sur les clips seuls.

Reglages fournis par l'utilisateur, dans la video 1080x1920 :
- courbe de Bezier cubique (769.6, 1041.6) (776.9, 1123.6) (835.2, 1125.4)
  (915.4, 1059.8), qui s'arrete juste a gauche du + (vers (955, 1027)) ;
- rouge (238, 22, 32), contour blanc ~3,6 px, legere ombre noire floue ;
  demi-largeur 6,4 px au depart -> 10 px a l'arrivee, pointe ~56 px ; courte
  et epaisse (des versions longues et fines ont ete refusees) ;
- 3 s a 30 i/s au MILIEU du clip, trois passages de 1 s : trace de 0 a 55 %
  (ease-out cubique), fondu d'entree sur 6 %, fondu de sortie de 78 a 100 % ;
  plus rien sur la derniere image.
Calage TikTok mesure sur iPhone (capture 1170x2532) : la video est zoomee
x1,189 et coupee d'environ 57 px de chaque cote ; le + du profil, a
(1079, 1221) a l'ecran, tombe vers (955, 1027) dans la video. Le badge
« + Follow » incruste par l'intro n'est PAS la cible (il n'est pas cliquable).
"""
from __future__ import annotations

import math

P = [(769.6, 1041.6), (776.9, 1123.6), (835.2, 1125.4), (915.4, 1059.8)]
RED, WHITE = (238, 22, 32), (255, 255, 255)
OUTLINE, HW0, HW1, HEAD = 3.6, 6.4, 10.0, 56.0
FPS, PASSES = 30, 3
S = 3                                   # sur-echantillonnage
X0, Y0, X1, Y1 = 700, 960, 1010, 1180   # region dessinee (pixels video)


def bez(t):
    u = 1 - t
    return tuple(u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d
                 for a, b, c, d in zip(*P))


def tangent(t):
    u = 1 - t
    return tuple(3 * u * u * (b - a) + 6 * u * t * (c - b) + 3 * t * t * (d - c)
                 for a, b, c, d in zip(*P))


# Longueur d'arc : la pointe (56 px) est prise sur la fin de la portion dessinee.
N = 400
pts = [bez(i / N) for i in range(N + 1)]
cum = [0.0]
for a, b in zip(pts, pts[1:]):
    cum.append(cum[-1] + math.dist(a, b))
TOTAL = cum[-1]


def t_at(length):
    length = max(0.0, min(TOTAL, length))
    for i in range(N):
        if cum[i + 1] >= length:
            f = (length - cum[i]) / max(1e-9, cum[i + 1] - cum[i])
            return (i + f) / N
    return 1.0


def to_px(p):
    return ((p[0] - X0) * S, (p[1] - Y0) * S)


def draw_arrow(progress):
    """Calque RGBA de la region : la fleche dessinee jusqu'a `progress` (0-1)."""
    from PIL import Image, ImageDraw, ImageFilter

    w, h = (X1 - X0) * S, (Y1 - Y0) * S
    shape_white = Image.new("L", (w, h), 0)
    shape_red = Image.new("L", (w, h), 0)
    if progress <= 0:
        return Image.new("RGBA", (w // S, h // S), (0, 0, 0, 0))
    drawn = progress * TOTAL
    head = min(HEAD, drawn * 0.6)
    body_end = max(0.0, drawn - head * 0.8)
    dw, dr = ImageDraw.Draw(shape_white), ImageDraw.Draw(shape_red)
    steps = max(2, int(body_end / 1.0))
    for i in range(steps + 1):
        t = t_at(body_end * i / steps)
        hw = HW0 + (HW1 - HW0) * t
        x, y = to_px(bez(t))
        r_w, r_r = (hw + OUTLINE) * S, hw * S
        dw.ellipse([x - r_w, y - r_w, x + r_w, y + r_w], fill=255)
        dr.ellipse([x - r_r, y - r_r, x + r_r, y + r_r], fill=255)
    # Pointe : triangle dans l'axe de la tangente au bout du trait.
    t_tip = t_at(drawn)
    tip = bez(t_tip)
    tx, ty = tangent(t_tip)
    n = math.hypot(tx, ty) or 1.0
    tx, ty = tx / n, ty / n
    hw = HW0 + (HW1 - HW0) * t_tip
    half_base = max(hw * 2.4, head * 0.5)
    base = (tip[0] - tx * head, tip[1] - ty * head)
    nx, ny = -ty, tx

    def tri(grow):
        g_tip = (tip[0] + tx * grow * 1.6, tip[1] + ty * grow * 1.6)
        g_base = (base[0] - tx * grow * 0.6, base[1] - ty * grow * 0.6)
        hb = half_base + grow * 1.3
        return [to_px(g_tip), to_px((g_base[0] + nx * hb, g_base[1] + ny * hb)),
                to_px((g_base[0] - nx * hb, g_base[1] - ny * hb))]

    dw.polygon(tri(OUTLINE), fill=255)
    dr.polygon(tri(0), fill=255)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.putalpha(shape_white.point(lambda v: int(v * 0.55)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(5 * S))
    layer.alpha_composite(shadow, (2 * S, 3 * S))
    white = Image.new("RGBA", (w, h), WHITE + (0,)); white.putalpha(shape_white)
    red = Image.new("RGBA", (w, h), RED + (0,)); red.putalpha(shape_red)
    layer.alpha_composite(white); layer.alpha_composite(red)
    return layer.resize((w // S, h // S), Image.LANCZOS)


def ease_out_cubic(u):
    return 1 - (1 - u) ** 3




DURATION_S = 3.0
SIZE = (1080, 1920)            # la courbe est definie pour ce format
ZONE = (Y0 / SIZE[1], Y1 / SIZE[1])   # bande verticale occupee (fractions)


def start_for(clip_duration: float) -> float:
    """Debut de la fleche : au milieu du clip (fonction pure)."""
    return max(0.0, (float(clip_duration) - DURATION_S) / 2)


def frames():
    """Les 90 images (RGBA, region (X0, Y0)-(X1, Y1)) ; la derniere est vide."""
    from PIL import Image

    out = []
    total = round(DURATION_S * FPS)
    for n in range(total):
        t = (n % FPS) / FPS                 # position dans le passage (0-1)
        progress = ease_out_cubic(min(1.0, t / 0.55))
        alpha = min(1.0, t / 0.06)
        if t > 0.78:
            alpha *= max(0.0, 1 - (t - 0.78) / 0.22)
        if n == total - 1 or alpha <= 0:
            frame = Image.new("RGBA", (X1 - X0, Y1 - Y0), (0, 0, 0, 0))
        else:
            frame = draw_arrow(progress)
            if alpha < 1:
                frame.putalpha(frame.getchannel("A").point(lambda v, a=alpha: int(v * a)))
        out.append(frame)
    return out
