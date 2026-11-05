"""Le fond de l'image bouge-t-il ? (hors incrustation webcam)

Demande de l'utilisateur (2026-10-31) : quand un streamer montre une image
FIXE (une affiche, un tweet, un site) avec sa webcam dans un coin, couper en
« webcam en haut, jeu en bas » n'a pas de sens -- il n'y a pas de jeu. Le
cadrage se recentre alors sur la webcam.

Mesure : difference moyenne (niveaux de gris, 0-255, image reduite a 160 px)
entre images prises a intervalles reguliers dans le clip, la zone de la
webcam masquee. Releve sur des clips reels : affiche commentee par Squeezie
~1,9 (webcam comprise) ; jeux de Kamet0 et plateau de Squeezie 14 a 28.
"""
from __future__ import annotations

from core.logging_setup import get_logger

logger = get_logger()

STATIC_THRESHOLD = 5.0
SAMPLES = 16
WIDTH = 160


def mean_difference(frames, mask=None) -> float:
    """Mediane des differences moyennes entre images successives (pure).
    `frames` : tableaux 2D de meme taille ; `mask` : True la ou l'on mesure."""
    import numpy as np

    diffs = []
    for a, b in zip(frames, frames[1:]):
        d = np.abs(np.asarray(a, dtype="float32") - np.asarray(b, dtype="float32"))
        diffs.append(float(d[mask].mean()) if mask is not None and mask.any() else float(d.mean()))
    return float(np.median(diffs)) if diffs else 0.0


def background_motion(video_path: str, start: float, end: float,
                      exclude=None, samples: int = SAMPLES, only=None) -> float | None:
    """Mouvement du fond entre `start` et `end` (secondes), la boite
    `exclude` = (x0, y0, x1, y1) en fractions de l'image ignoree -- ou, avec
    `only`, mesure DANS cette boite seulement. None si la video ne se lit
    pas."""
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        frames = []
        span = max(0.0, end - start)
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + span * (0.05 + 0.9 * i / max(1, samples - 1))) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h = max(2, int(WIDTH * frame.shape[0] / frame.shape[1]))
            frames.append(cv2.cvtColor(cv2.resize(frame, (WIDTH, h)), cv2.COLOR_BGR2GRAY))
        cap.release()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Mouvement du fond non mesuré : {e}")
        return None
    if len(frames) < 3:
        return None
    mask = None
    box = exclude if exclude is not None else only
    if box is not None:
        h, w = frames[0].shape
        inside = np.zeros((h, w), dtype=bool)
        x0, y0, x1, y1 = box
        inside[max(0, int(y0 * h)):max(0, int(y1 * h) + 1), max(0, int(x0 * w)):max(0, int(x1 * w) + 1)] = True
        mask = inside if exclude is None else ~inside
    return mean_difference(frames, mask)


def is_static(motion: float | None, threshold: float = STATIC_THRESHOLD) -> bool:
    return motion is not None and motion < threshold


EDGE_MIN = 40.0     # bord d'incrustation : saut de luminance median >= 40 le long du bord
EDGE_REL = 0.35     # ... et au moins 35 % du bord le plus franc de la zone


def nearest_edge(scores: list[float]) -> int | None:
    """Indice du PREMIER bord franc en partant du visage (pure) -- pas du
    plus franc : celui-ci est souvent dans le contenu montre (affiche,
    page). None sans bord franc."""
    if not scores:
        return None
    limit = max(EDGE_MIN, EDGE_REL * max(scores))
    return next((i for i, v in enumerate(scores) if v >= limit), None)


def _jump(a, b):
    """Saut entre deux lignes/colonnes de pixels : le plus fort des trois
    canaux couleur (BGR), pas la luminance seule. Le bord d'une webcam ou une
    chaise sombre touche un jeu sombre d'une autre teinte ne se voit qu'en
    couleur (Kamet0, 2026-11-05 : 91 en couleur, ~25 en luminance). Sur un
    bord gris, c'est le meme saut qu'en luminance : seuils inchanges."""
    import numpy as np

    d = np.abs(a - b)
    return d.max(axis=-1) if d.ndim > 1 else d


def webcam_rect(video_path: str, start: float, end: float, cx: float, cy: float,
                face_h: float, samples: int = 6):
    """Rectangle reel de l'incrustation webcam (x0, y0, x1, y1 en fractions),
    trouve par ses bords droits dans l'image : collee a son coin, elle se
    termine la ou la luminance saute tout le long d'une ligne. None si un
    bord n'est pas trouve (on garde alors l'estimation par le visage)."""
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        frames = []
        span = max(0.0, end - start)
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + span * (0.1 + 0.8 * i / max(1, samples - 1))) * 1000)
            ok, frame = cap.read()
            if ok:
                h = max(4, int(640 * frame.shape[0] / frame.shape[1]))
                frames.append(cv2.resize(frame, (640, h)).astype("float32"))
        cap.release()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Bords de la webcam non mesurés : {e}")
        return None
    if not frames:
        return None
    H, W = frames[0].shape[:2]
    left, top = cx < 0.5, cy < 0.5
    face_w = face_h * H / W
    ry0, ry1 = max(1, int((cy - face_h) * H)), min(H - 1, int((cy + face_h) * H))
    if ry1 <= ry0:
        return None

    def vscore(x):
        return float(np.median([np.median(_jump(g[ry0:ry1, x + 1], g[ry0:ry1, x - 1])) for g in frames]))

    xs = (list(range(max(1, int((cx + 0.6 * face_w) * W)), int(0.5 * W))) if left
          else list(range(min(W - 2, int((cx - 0.6 * face_w) * W)), int(0.5 * W), -1)))
    i = nearest_edge([vscore(x) for x in xs])
    if i is None:
        return None
    edge_x = xs[i]
    x0, x1 = (0, edge_x) if left else (edge_x, W)
    if x1 - x0 < 8:
        return None

    def hscore(y):
        return float(np.median([np.median(_jump(g[y + 1, x0 + 2:x1 - 2], g[y - 1, x0 + 2:x1 - 2])) for g in frames]))

    ys = (list(range(max(1, int((cy + face_h) * H)), int(0.6 * H))) if top
          else list(range(min(H - 2, int((cy - face_h) * H)), int(0.4 * H), -1)))
    j = nearest_edge([hscore(y) for y in ys])
    if j is None:
        return None
    edge_y = ys[j]
    y0, y1 = (0, edge_y) if top else (edge_y, H)
    rect = (x0 / W, y0 / H, x1 / W, y1 / H)
    return rect if plausible_webcam(rect, W, H) else None


def plausible_webcam(rect, width: int, height: int) -> bool:
    """Une incrustation webcam a une forme de camera (de 4:5 a 21:9 en
    pixels) et ne couvre pas la moitie de l'ecran -- sinon le « bord »
    trouve est un trait du decor ou du contenu, pas la webcam. Pure."""
    x0, y0, x1, y1 = rect
    w_px, h_px = (x1 - x0) * width, (y1 - y0) * height
    if w_px <= 0 or h_px <= 0:
        return False
    return 0.8 <= w_px / h_px <= 2.4 and (x1 - x0) * (y1 - y0) <= 0.35


BORDER_SNAP = 0.35   # pas de bord trouve de ce cote : le bord de l'ecran, s'il est proche
LIVE_MIN = 1.0       # une webcam en direct bouge un peu (une photo fixe : ~0)


def _color_frames(video_path: str, start: float, end: float, samples: int = 6, width: int = 640):
    import cv2

    cap = cv2.VideoCapture(video_path)
    frames = []
    span = max(0.0, end - start)
    try:
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + span * (0.1 + 0.8 * i / max(1, samples - 1))) * 1000)
            ok, frame = cap.read()
            if ok:
                h = max(4, int(width * frame.shape[0] / frame.shape[1]))
                # En couleur : voir _jump.
                frames.append(cv2.resize(frame, (width, h)).astype("float32"))
    finally:
        cap.release()
    return frames


def floating_webcam_rect(video_path: str, start: float, end: float, cx: float, cy: float,
                         face_h: float):
    """Rectangle d'une incrustation webcam placee N'IMPORTE OU (pas forcement
    dans un coin) : ses quatre bords, cherches depuis le visage vers
    l'exterieur. Un cote sans bord franc prend le bord de l'ecran s'il est
    proche (webcam collee a un bord), sinon rien n'est renvoye."""
    try:
        import numpy as np

        frames = _color_frames(video_path, start, end)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Bords de la webcam non mesurés : {e}")
        return None
    if not frames:
        return None
    H, W = frames[0].shape[:2]
    face_w = face_h * H / W
    ry0, ry1 = max(1, int((cy - face_h) * H)), min(H - 1, int((cy + face_h) * H))
    if ry1 <= ry0:
        return None

    def vscore(x):
        return float(np.median([np.median(_jump(g[ry0:ry1, x + 1], g[ry0:ry1, x - 1]))
                                for g in frames]))

    def search(positions, score, border, distance):
        i = nearest_edge([score(v) for v in positions])
        if i is not None:
            return positions[i]
        return border if distance < BORDER_SNAP else None

    right = search(list(range(min(W - 2, int((cx + 0.6 * face_w) * W)), min(W - 1, int((cx + 0.5) * W)))),
                   vscore, W, 1 - cx)
    left = search(list(range(max(1, int((cx - 0.6 * face_w) * W)), max(0, int((cx - 0.5) * W)), -1)),
                  vscore, 0, cx)
    if right is None or left is None or right - left < 8:
        return None
    cx0, cx1 = left + 2, right - 2

    def hscore(y):
        return float(np.median([np.median(_jump(g[y + 1, cx0:cx1], g[y - 1, cx0:cx1]))
                                for g in frames]))

    # Depuis le menton / le haut du crane : le bas d'une webcam serree tombe
    # juste sous le visage.
    bottom = search(list(range(min(H - 2, int((cy + 0.6 * face_h) * H)),
                               min(H - 1, int((cy + 0.5) * H)))), hscore, H, 1.0)
    top = search(list(range(max(1, int((cy - 0.6 * face_h) * H)), max(0, int((cy - 0.5) * H)), -1)),
                 hscore, 0, 1.0)
    # Un bord haut ou bas sans contraste (t-shirt noir sur bandeau noir) :
    # deduit de la largeur, une webcam etant en 16:9.
    height = (right - left) * 9 / 16
    if bottom is None and top is not None:
        bottom = round(top + height) if top + height <= H else None
    elif top is None and bottom is not None:
        top = round(bottom - height) if bottom - height >= 0 else None
    elif top is None and bottom is None:
        # Ni l'un ni l'autre : collee aux deux bords seulement s'ils sont proches.
        top = 0 if cy < BORDER_SNAP else None
        bottom = H if 1 - cy < BORDER_SNAP else None
    if top is None or bottom is None or bottom - top < 8:
        return None
    # Un bord trouve est le liseré de l'incrustation : resserrer de 3 px (sur
    # 640) pour ne pas le garder ; un bord de l'ecran reste tel quel.
    inset = 3
    left = left if left == 0 else left + inset
    right = right if right == W else right - inset
    top = top if top == 0 else top + inset
    bottom = bottom if bottom == H else bottom - inset
    rect = (left / W, top / H, right / W, bottom / H)
    inside = rect[0] < cx < rect[2] and rect[1] < cy < rect[3]
    return rect if inside and plausible_webcam(rect, W, H) else None
