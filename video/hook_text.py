"""Texte en haut du clip : une phrase dans un encadre blanc.

Demande de l'utilisateur (2026-11-01) : sa video la plus vue de la semaine
(11 K vues, contre ~800 pour ses compilations) portait en haut une phrase
dans un encadre blanc (« Et vous vous allez faire quoi en Novembre ? »).
Instagram demande aussi du texte unique ajoute pour recommander un compte de
clips.

Le texte n'est JAMAIS invente : c'est une phrase reellement prononcee dans le
clip, choisie par le module « Titres et descriptions » (editing/metadata.py,
extraction sans IA) -- une question d'abord (elle fait commenter), sinon la
phrase la plus percutante. Rien a dire : pas de texte.

Le dessin est fait par Pillow (une image transparente plein cadre, recadree
sur son contenu), posee comme le filigrane : en calque permanent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

TOP_PERCENT = 11.0          # sous la barre « Abonnements | Pour toi » de TikTok
MAX_WIDTH_PERCENT = 86.0
MAX_WORDS = 12
MAX_LINES = 3


@dataclass(frozen=True)
class HookText:
    path: str
    x: int
    y: int


_SMALL_WORDS = {"le", "la", "les", "l'", "de", "du", "des", "d'", "un", "une", "et", "à", "a",
                "en", "que", "qui", "pour", "par", "sur", "dans", "au", "aux", "ce", "cette",
                "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses", "mais", "ou",
                "c'est", "je", "tu", "il", "on", "nous", "vous"}


def clean(text: str) -> str:
    """Nettoie une phrase transcrite ou un titre : guillemets autour, espace
    parasite avant une apostrophe (« c 'est »), score coupe (« 3 -2 »)."""
    text = " ".join((text or "").split())
    text = re.sub(r"(\w) ?['’] ?(\w)", r"\1'\2", text)
    text = re.sub(r"(\d) ?- ?(\d)", r"\1-\2", text)
    return text.strip(' "«»“”').strip()


def shorten(text: str, max_words: int = MAX_WORDS) -> str:
    """Au plus `max_words` mots, sans finir sur un petit mot (« … mes
    enfants qui… » -> « … mes enfants… »)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    kept = words[:max_words]
    while len(kept) > 3 and kept[-1].lower().strip(",;:") in _SMALL_WORDS:
        kept.pop()
    return " ".join(kept).rstrip(" ,;:") + "…"


def choose_text(titles, max_words: int = MAX_WORDS, preferred: str = "") -> str:
    """La phrase a afficher : une QUESTION reellement posee dans le clip
    d'abord (elle fait commenter) ; sinon `preferred` -- le titre donne au
    clip sur Twitch, ecrit par un humain et souvent la chute du moment --
    s'il fait au moins 3 mots ; sinon la phrase la plus percutante prononcee.
    "" si rien. Fonction pure."""
    by_kind = {}
    for title in titles or ():
        kind = title.get("kind") if isinstance(title, dict) else getattr(title, "kind", "")
        text = title.get("text") if isinstance(title, dict) else getattr(title, "text", "")
        if kind and text and kind not in by_kind:
            by_kind[kind] = " ".join(str(text).split())
    preferred = clean(preferred)
    if len(preferred.split()) < 3:
        preferred = ""
    text = clean(by_kind.get("curiosite") or preferred or by_kind.get("direct") or "")
    return shorten(text, max_words)


PINK = (255, 46, 99)          # rose du medaillon ClipsOfStreams (video/compilation.BRAND)
BAND_ALPHA = 190
CANDIDATE_TOPS = [t / 100 for t in range(8, 78, 2)]   # hauts possibles du bandeau
# Medaillon « Follow » de l'intro, a droite : de 26,5 % a ~46 % du haut
# pendant ses premieres secondes (video/intro_overlay.py).
INTRO_ZONE = (0.255, 0.465)
INTRO_SECONDS = 4.0
MIN_FACE_H = 0.035              # visage plus petit : decor, pas une personne a l'ecran


def render_band(text: str, out_w: int, out_h: int, tag: str = ""):
    """Design « bandeau » choisi par l'utilisateur (design E, 2026-11-01) :
    bande noire translucide sur toute la largeur, filets roses en haut et en
    bas, texte blanc gras, et au-dessus a gauche une pastille rose
    « ● <STREAMER> EN LIVE » si `tag` est donne. Renvoie (image RGBA de
    largeur out_w, hauteur de la pastille au-dessus du bandeau) ou None."""
    text = " ".join((text or "").split())
    if not text:
        return None
    from PIL import Image, ImageDraw, ImageFont

    from core.paths import app_base_dir
    from video.compilation import wrap_text

    fonts = app_base_dir() / "assets" / "fonts"
    scale = min(out_w, out_h) / 1080

    def font(name, size):
        try:
            return ImageFont.truetype(str(fonts / name), round(size * scale))
        except OSError:
            from news_story.post_composer import _font
            return _font(round(size * scale))

    title_font = font("BarlowCondensed-ExtraBoldItalic.ttf", 68)
    tag_font = font("BarlowCondensed-SemiBold.ttf", 40)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = wrap_text(probe, text.upper(), title_font, out_w - round(140 * scale),
                      max_lines=MAX_LINES)
    line_h = round(78 * scale)
    pad, rule = round(26 * scale), max(3, round(8 * scale))
    band_h = line_h * len(lines) + 2 * pad
    pill_h = round(60 * scale) if tag else 0
    image = Image.new("RGBA", (out_w, pill_h + band_h), (0, 0, 0, 0))
    dot_center = None
    draw = ImageDraw.Draw(image)
    top = pill_h
    draw.rectangle([0, top, out_w, top + band_h], fill=(0, 0, 0, BAND_ALPHA))
    draw.rectangle([0, top, out_w, top + rule], fill=PINK + (255,))
    draw.rectangle([0, top + band_h - rule, out_w, top + band_h], fill=PINK + (255,))
    if tag:
        label = f"{tag} EN LIVE".upper()
        x = round(60 * scale)
        tw = draw.textlength(label, font=tag_font)
        dot = round(26 * scale)
        ring = max(2, round(4 * scale))
        hole_r = dot // 2 + ring
        pad, gap = round(16 * scale), round(12 * scale)
        # La pastille se prolonge dans le filet rose du bandeau : son milieu
        # visible est a (pill_h + rule) / 2, pas a pill_h / 2.
        cy = (pill_h + rule) // 2
        cx = x + pad + hole_r
        text_x = cx + hole_r + gap
        draw.rounded_rectangle([x, 0, text_x + tw + round(22 * scale), pill_h + rule],
                               radius=round(16 * scale), fill=PINK + (255,))
        # Rond evide (transparent) dans la pastille ; le point rouge qui
        # grandit et rapetisse y est pose a l'incrustation (dot_frames).
        draw.ellipse([cx - hole_r, cy - hole_r, cx + hole_r, cy + hole_r], fill=(0, 0, 0, 0))
        top_t, bottom_t = draw.textbbox((0, 0), label, font=tag_font)[1::2]
        draw.text((text_x, cy - (top_t + bottom_t) / 2), label, font=tag_font,
                  fill=(255, 255, 255))
        dot_center = (cx, cy, dot)
    y = top + pad
    for line in lines:
        draw.text(((out_w - draw.textlength(line, font=title_font)) / 2, y), line,
                  font=title_font, fill=(255, 255, 255))
        y += line_h
    image.info["dot"] = dot_center       # (x, y, diametre) du point « en direct »
    return image, pill_h


LIVE_RED = (255, 23, 23)
BREATH_PERIOD_S = 1.4       # un battement complet (plus lent que le voyant REC de reference, ~0,65 s)
BREATH_MIN = 0.25           # le point ne disparait jamais tout a fait


BREATH_SMALL = 0.12         # presque un point d'epingle au plus petit
GLOW_SPREAD = 2.6           # cote de l'image du point : la place du halo
FPS = 30


def breath(t: float, period: float = BREATH_PERIOD_S) -> float:
    """0 -> 1 -> 0 en douceur sur une periode (fonction pure)."""
    import math

    return 0.5 - 0.5 * math.cos(2 * math.pi * (t % period) / period)


def dot_frames(diameter: int, period: float = BREATH_PERIOD_S, fps: int = FPS):
    """Une periode d'animation du point « en direct », sur le modele du
    voyant REC montre par l'utilisateur (2026-11-01), plus lent et lumineux a
    sa demande : un point rouge vif PLEIN qui retrecit presque jusqu'a
    disparaitre puis regrossit, entoure d'un halo rouge doux qui suit sa
    taille. Images carrees transparentes, point au centre, bord lisse
    (dessine 4 fois plus grand puis reduit) ; elles bouclent sans saut."""
    from PIL import Image, ImageDraw, ImageFilter

    size = int(diameter * GLOW_SPREAD) // 2 * 2 + 4
    big = size * 4
    frames = []
    for k in range(max(2, round(period * fps))):
        b = breath(k / fps, period)
        radius = diameter / 2 * (BREATH_SMALL + (1 - BREATH_SMALL) * b) * 4
        glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        g = radius * 1.9
        ImageDraw.Draw(glow).ellipse([big / 2 - g, big / 2 - g, big / 2 + g, big / 2 + g],
                                     fill=LIVE_RED + (int(140 + 80 * b),))
        image = glow.filter(ImageFilter.GaussianBlur(max(2.0, radius * 0.7)))
        ImageDraw.Draw(image).ellipse([big / 2 - radius, big / 2 - radius,
                                       big / 2 + radius, big / 2 + radius],
                                      fill=LIVE_RED + (255,))
        frames.append(image.resize((size, size), Image.LANCZOS))
    return frames


def choose_top(face_samples, height: float, preferred: float,
               avoid=(INTRO_ZONE,), avoid_weight: float = 0.3) -> float:
    """Haut du bandeau (fraction de la hauteur) qui ne cache aucun visage
    (fonction pure). `face_samples` : pour chaque image analysee, la liste
    des visages (y, h en fractions, y = haut). Cout d'une position : la part
    des images ou il recouvre un visage (agrandi : cheveux, menton), plus
    `avoid_weight` s'il recouvre une zone a eviter (medaillon de l'intro) ;
    a cout egal, la plus proche de `preferred`."""
    samples = [list(faces) for faces in face_samples] or [[]]

    def covers(top, faces):
        # Marge : un quart de visage au-dessus (cheveux), une moitie dessous
        # (menton, bouche qui parle). Les tout petits visages (portraits de
        # l'interface d'un jeu, public au loin) ne comptent pas.
        bottom = top + height
        return any(top < y + h * 1.5 and y - h * 0.25 < bottom
                   for y, h in faces if h >= MIN_FACE_H)

    best = None
    for top in CANDIDATE_TOPS:
        if top + height > 0.80:          # plus bas : filigrane et legende TikTok
            continue
        cost = sum(covers(top, faces) for faces in samples) / len(samples)
        cost += sum(avoid_weight for lo, hi in avoid if top < hi and lo < top + height)
        key = (round(cost, 3), abs(top - preferred))
        if best is None or key < best[0]:
            best = (key, top)
    return best[1] if best else preferred


def apply(video_path: str, text: str, out_w: int, out_h: int, *, tag: str = "",
          preferred: float = TOP_PERCENT / 100, intro: bool = True, cancel_token=None) -> bool:
    """Pose le bandeau sur un clip DEJA rendu : les visages sont cherches sur
    la video finie (quel que soit le cadrage : recadre, webcam + jeu, webcam
    seule), le bandeau va la ou il n'y en a pas, puis une seconde passe
    ffmpeg l'incruste. Faux (clip inchange) si rien a poser ou en cas
    d'echec -- jamais d'exception qui ferait perdre le clip."""
    import tempfile

    rendered = render_band(text, out_w, out_h, tag)
    if rendered is None:
        return False
    image, _pill_h = rendered
    try:
        from video.face_detector import detect_face_track
        from video.ffmpeg_utils import run_ffmpeg, video_duration

        duration = video_duration(video_path)
        samples = detect_face_track(video_path, 0.0, duration, sample_interval_s=0.5,
                                    max_samples=80, confidence_threshold=0.6, max_faces=6)
        faces = [[(f.y, f.h) for f in (getattr(s, "faces", ()) or ())] for s in samples]
        # Le medaillon ne reste que quelques secondes : le recouvrir coute en
        # proportion de sa duree, comme un visage present le meme temps.
        top = choose_top(faces, image.height / out_h, preferred,
                         avoid=(INTRO_ZONE,) if intro else (),
                         avoid_weight=min(0.3, INTRO_SECONDS / max(duration, 1.0)))
        y = int(round(top * out_h)) // 2 * 2
        dot = image.info.get("dot")
        with tempfile.TemporaryDirectory(prefix="clipfarming_texte_") as tmp:
            png = Path(tmp) / "bandeau.png"
            image.save(png)
            out = Path(tmp) / "avec_texte.mp4"
            inputs = ["-i", str(video_path), "-i", str(png)]
            graph = f"[0:v][1:v]overlay=0:{y}:format=auto[b]"
            if dot:
                # Point rouge qui grandit et rapetisse, comme le voyant d'une
                # camera : une periode d'images, bouclee sur tout le clip.
                frames = dot_frames(dot[2])
                for k, frame in enumerate(frames):
                    frame.save(Path(tmp) / f"point_{k:03d}.png")
                side = frames[0].width
                dx, dy = dot[0] - side // 2, y + dot[1] - side // 2
                inputs += ["-stream_loop", "-1", "-framerate", str(FPS),
                           "-i", str(Path(tmp) / "point_%03d.png")]
                graph += f";[b][2:v]overlay={dx}:{dy}:shortest=1:format=auto[b2]"
                last = "b2"
            else:
                last = "b"
            graph += f";[{last}]format=yuv420p[v]"
            run_ffmpeg([*inputs, "-filter_complex", graph,
                        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "17", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                       "texte en haut", cancel_token=cancel_token)
            Path(out).replace(video_path)
        logger.info(f"Texte en haut : « {text} » a {top:.0%} du haut (aucun visage dessous).")
        return True
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "CancelledError":
            raise
        logger.warning(f"Texte en haut non ajouté : {e}")
        return False
