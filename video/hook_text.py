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
    # « Saint -Hurigol » -> « Saint-Hurigol » (tiret colle par la transcription)
    text = re.sub(r"(\w) -(\w)", r"\1-\2", text)
    return text.strip(' "«»“”').strip()


def trim_tail(text: str, min_words: int = 3) -> str:
    """Retire les petits mots en fin de phrase (« … et le ») ; "" s'il ne
    reste pas `min_words` mots -- une phrase amputee ne fait pas un titre."""
    words = text.rstrip(" .,;:…").split()
    while words and words[-1].lower().strip(",;:") in _SMALL_WORDS:
        words.pop()
    return " ".join(words) if len(words) >= min_words else ""


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


MIN_SPOKEN_WORDS = 5        # une phrase dite plus courte ne fait pas un titre (« Ouais, je suis »)


def choose_text(titles, max_words: int = MAX_WORDS, preferred: str = "",
                fallback: str = "") -> str:
    """La phrase a afficher : une QUESTION reellement posee dans le clip
    d'abord (elle fait commenter) ; sinon `preferred` -- le titre donne au
    clip sur Twitch, ecrit par un humain et souvent la chute du moment --
    s'il fait au moins 3 mots ; sinon la phrase la plus percutante prononcee
    (5 mots au moins, sans petit mot ampute en fin) ; sinon `fallback`.
    Fonction pure."""
    by_kind = {}
    for title in titles or ():
        kind = title.get("kind") if isinstance(title, dict) else getattr(title, "kind", "")
        text = title.get("text") if isinstance(title, dict) else getattr(title, "text", "")
        if kind and text and kind not in by_kind:
            by_kind[kind] = " ".join(str(text).split())
    preferred = clean(preferred)
    if len(preferred.split()) < 3:
        preferred = ""
    # « curiosite » sans point d'interrogation = une phrase volontairement
    # coupee par le module de titres (editing/metadata.py) : pas pour le
    # bandeau, qui ne prend que les VRAIES questions.
    question = by_kind.get("curiosite") or ""
    if not question.rstrip().endswith("?"):
        question = ""
    for candidate, min_words in ((question, 3), (preferred, 3),
                                 (by_kind.get("direct"), MIN_SPOKEN_WORDS)):
        text = clean(candidate or "")
        if not text:
            continue
        if not text.endswith("?"):
            text = trim_tail(text, min_words)
        elif len(text.split()) < min_words:
            text = ""
        if text:
            return shorten(text, max_words)
    # Rien de solide : un texte VRAI et neutre fourni par l'appelant (le clip
    # du jour : « le clip le plus vu du jour »), jamais un bout de phrase.
    return clean(fallback)


PINK = (255, 46, 99)          # rose du medaillon ClipsOfStreams (video/compilation.BRAND)
BAND_ALPHA = 190
CANDIDATE_TOPS = [t / 100 for t in range(8, 78, 2)]   # hauts possibles du bandeau
# Medaillon « Follow » de l'intro, a droite : de 26,5 % a ~46 % du haut
# pendant ses premieres secondes (video/intro_overlay.py).
INTRO_ZONE = (0.255, 0.465)
INTRO_SECONDS = 4.0
MIN_FACE_H = 0.035              # visage plus petit : decor, pas une personne a l'ecran


def render_band(text: str, out_w: int, out_h: int):
    """Design « bandeau » choisi par l'utilisateur (design E, 2026-11-01) :
    bande noire translucide sur toute la largeur, filets roses en haut et en
    bas, texte blanc gras. Le badge neon se pose au-dessus (apply). Renvoie
    l'image RGBA (largeur out_w) ou None."""
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
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = wrap_text(probe, text.upper(), title_font, out_w - round(140 * scale),
                      max_lines=MAX_LINES)
    line_h = round(78 * scale)
    pad, rule = round(26 * scale), max(3, round(8 * scale))
    band_h = line_h * len(lines) + 2 * pad
    image = Image.new("RGBA", (out_w, band_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    top = 0
    draw.rectangle([0, top, out_w, top + band_h], fill=(0, 0, 0, BAND_ALPHA))
    draw.rectangle([0, top, out_w, top + rule], fill=PINK + (255,))
    draw.rectangle([0, top + band_h - rule, out_w, top + band_h], fill=PINK + (255,))
    y = top + pad
    for line in lines:
        draw.text(((out_w - draw.textlength(line, font=title_font)) / 2, y), line,
                  font=title_font, fill=(255, 255, 255))
        y += line_h
    return image


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


BADGE_OVERLAP = 10              # le badge chevauche le filet rose du bandeau (px a 1080)
BADGE_LEFT = 46


def stack_layout(band_h: int, badge_h: int, scale: float) -> tuple[int, int]:
    """(hauteur totale, decalage du bandeau) quand le badge neon est pose
    au-dessus du bandeau en le chevauchant un peu. Fonction pure."""
    if not badge_h:
        return band_h, 0
    offset = badge_h - round(BADGE_OVERLAP * scale)
    return offset + band_h, offset


def apply(video_path: str, text: str, out_w: int, out_h: int, *, tag: str = "",
          preferred: float = TOP_PERCENT / 100, intro: bool = True, cancel_token=None) -> bool:
    """Pose le bandeau sur un clip DEJA rendu, et au-dessus a gauche, si `tag`
    est donne, le badge neon anime « REPLAY » + « <STREAMER> EN LIVE »
    (video/neon_badge.py). Les visages sont cherches sur la video finie
    (quel que soit le cadrage : recadre, webcam + jeu, webcam seule) et
    l'ensemble va la ou il n'y en a pas, puis une seconde passe ffmpeg
    l'incruste. Faux (clip inchange) si rien a poser ou en cas d'echec --
    jamais d'exception qui ferait perdre le clip."""
    import tempfile

    band = render_band(text, out_w, out_h)
    if band is None:
        return False
    scale = min(out_w, out_h) / 1080
    try:
        from video.face_detector import detect_face_track
        from video.ffmpeg_utils import run_ffmpeg, video_duration

        badge = []
        if tag:
            from video import neon_badge

            badge = neon_badge.frames(tag, scale)
        total_h, band_offset = stack_layout(band.height, badge[0].height if badge else 0, scale)
        duration = video_duration(video_path)
        samples = detect_face_track(video_path, 0.0, duration, sample_interval_s=0.5,
                                    max_samples=80, confidence_threshold=0.6, max_faces=6)
        faces = [[(f.y, f.h) for f in (getattr(s, "faces", ()) or ())] for s in samples]
        # Le medaillon ne reste que quelques secondes : le recouvrir coute en
        # proportion de sa duree, comme un visage present le meme temps.
        top = choose_top(faces, total_h / out_h, preferred,
                         avoid=(INTRO_ZONE,) if intro else (),
                         avoid_weight=min(0.3, INTRO_SECONDS / max(duration, 1.0)))
        y = int(round(top * out_h)) // 2 * 2
        with tempfile.TemporaryDirectory(prefix="clipfarming_texte_") as tmp:
            png = Path(tmp) / "bandeau.png"
            band.save(png)
            out = Path(tmp) / "avec_texte.mp4"
            inputs = ["-i", str(video_path), "-i", str(png)]
            graph = f"[0:v][1:v]overlay=0:{y + band_offset}:format=auto[b]"
            last = "b"
            if badge:
                # Une periode d'animation, bouclee sur tout le clip.
                for k, frame in enumerate(badge):
                    frame.save(Path(tmp) / f"badge_{k:03d}.png")
                inputs += ["-stream_loop", "-1", "-framerate", str(neon_badge.FPS),
                           "-i", str(Path(tmp) / "badge_%03d.png")]
                graph += (f";[b][2:v]overlay={round(BADGE_LEFT * scale)}:{y}"
                          f":shortest=1:format=auto[b2]")
                last = "b2"
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
