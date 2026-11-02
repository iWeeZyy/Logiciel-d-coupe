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

TOP_PERCENT = 15.0          # haut de la zone visible dans la grille du profil (GRID_SAFE)
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
                "c'est", "je", "tu", "il", "on", "nous", "vous", "tout", "toute"}


def clean(text: str) -> str:
    """Nettoie une phrase transcrite ou un titre : guillemets autour, espace
    parasite avant une apostrophe (« c 'est »), score coupe (« 3 -2 »)."""
    import unicodedata

    # Emojis et symboles : la police du logiciel ne les a pas (carre vide).
    text = "".join(ch for ch in (text or "") if unicodedata.category(ch) not in ("So", "Cs", "Co"))
    text = " ".join(text.split())
    text = re.sub(r"(\w) ?['’] ?(\w)", r"\1'\2", text)
    text = re.sub(r"(\d) ?- ?(\d)", r"\1-\2", text)
    # « Saint -Hurigol » -> « Saint-Hurigol » (tiret colle par la transcription)
    text = re.sub(r"(\w) -(\w)", r"\1-\2", text)
    # « commence a chanter » -> « commence à chanter » : « a » devant un verbe
    # a l'infinitif est toujours la preposition (titre Twitch tape sans accent).
    text = re.sub(r"(?i)\b(a)( [a-zàâçéèêëîïôûùüÿœ]+(?:er|ir|re|oir))\b",
                  lambda m: ("À" if m.group(1) == "A" else "à") + m.group(2), text)
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


def garbled(text: str) -> bool:
    """Phrase a ne jamais afficher (fonction pure) : un mot repete 3 fois ou
    plus (« … parti parti parti parti », transcription d'un cri ou d'une
    musique, constate le 2026-11-02) ou une lettre etiree (« MARTIIIIII »)."""
    words = [w.strip(".,;:!?…").lower() for w in (text or "").split()]
    words = [w for w in words if w]
    if any(words.count(w) >= 3 and w not in _SMALL_WORDS for w in set(words)):
        return True
    return bool(re.search(r"(\w)\1{3,}", text or ""))


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
        if not text or garbled(text):
            continue
        if not text.endswith("?"):
            text = trim_tail(text, min_words)
        elif len(text.split()) < min_words:
            text = ""
        if text:
            text = shorten(text, max_words)
            return text[:1].upper() + text[1:]          # majuscule en debut de phrase
    # Rien de solide : un texte VRAI et neutre fourni par l'appelant (le clip
    # du jour : « le clip le plus vu du jour »), jamais un bout de phrase.
    return clean(fallback)


PINK = (255, 46, 99)          # rose du medaillon ClipsOfStreams (video/compilation.BRAND)
BAND_ALPHA = 190
# Grille du profil (demande du 2026-11-02, « point 4 ») : TikTok et Instagram
# y montrent la video recadree au centre en portrait court (3:4 ou 4:5),
# soit, sur une video 9:16, a peu pres de 15 % a 85 % de la hauteur. Badge,
# numero et bandeau restent dans cette zone pour etre lisibles dans la grille
# (et sous la barre « Abonnements | Pour toi » de TikTok).
GRID_SAFE = (0.15, 0.80)       # bas : 80 %, filigrane et legende TikTok plus bas
CANDIDATE_TOPS = [t / 100 for t in range(15, 78, 2)]  # hauts possibles du bandeau
# Medaillon « Follow » de l'intro, a droite : de 26,5 % a ~46 % du haut
# pendant ses premieres secondes (video/intro_overlay.py).
INTRO_ZONE = (0.255, 0.465)
INTRO_SECONDS = 7.0             # repli si la duree de l'intro ne se lit pas
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
        if top < GRID_SAFE[0] or top + height > GRID_SAFE[1]:
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


def replay_label(name: str) -> str:
    """« REPLAY » + « D'ANYME023 » / « DE SQUEEZIE » (demande du 2026-11-01
    pour les tops : « Replay de (nom du streamer) »)."""
    name = " ".join((name or "").split())
    if not name:
        return ""
    return (f"D'{name}" if name[0].lower() in "aeiouyhéèêàâîôû" else f"DE {name}").upper()


def apply(video_path: str, text: str, out_w: int, out_h: int, *, tag: str = "",
          preferred: float = TOP_PERCENT / 100, intro=None, cancel_token=None,
          badge_label: str | None = None, number: int | None = None,
          avoid_intro_s: float = 0.0, headline: tuple[str, str] | None = None) -> bool:
    """Pose le bandeau sur un clip DEJA rendu, et au-dessus a gauche, si `tag`
    est donne, le badge neon anime « REPLAY » + « <STREAMER> EN LIVE »
    (video/neon_badge.py ; `badge_label` remplace ce texte de droite). Les
    visages sont cherches sur la video finie (quel que soit le cadrage :
    recadre, webcam + jeu, webcam seule) et l'ensemble va la ou il n'y en a
    pas, puis une seconde passe ffmpeg l'incruste. `number` (tops de la
    semaine) : « #N » dans la pastille rose, a droite sur la ligne du badge ;
    sans phrase a afficher, badge et numero restent seuls. `intro`
    (video/intro.py) : le clip a ete rendu SANS elle, elle est posee ici en
    dernier, PAR-DESSUS le bandeau (demande de l'utilisateur, 2026-11-01).
    `headline` : (« TOP 5 », « DES CLIPS DE … CETTE SEMAINE ») -- titre
    ephemere en neon rose, centre au-dessus du badge, eteint apres
    neon_badge.HEADLINE_S (1er clip d'un top).
    `avoid_intro_s` : l'intro sera posee plus tard sur ce clip (compilation)
    pendant ce temps, eviter aussi sa zone. Faux si rien n'a pu etre pose --
    l'intro l'est quand meme (passe de secours) ; jamais d'exception qui
    ferait perdre le clip."""
    import tempfile

    band = render_band(text, out_w, out_h)
    if band is None and not (tag or number):
        _intro_only(video_path, intro, out_w, out_h, cancel_token)
        return False
    scale = min(out_w, out_h) / 1080
    try:
        from video.face_detector import detect_face_track
        from video.ffmpeg_utils import run_ffmpeg, video_duration

        badge = []
        if tag:
            from video import neon_badge

            badge = neon_badge.frames(tag, scale, label=badge_label)
        row_h = badge[0].height if badge else round(82 * scale)
        number_anim = []
        if number:
            from video import neon_badge

            number_anim = neon_badge.number_frames(int(number), scale)
        if band is not None:
            total_h, band_offset = stack_layout(band.height,
                                                row_h if (badge or number_anim) else 0, scale)
        else:
            total_h, band_offset = row_h, 0
        head = []
        if headline:
            from video import neon_badge

            head = neon_badge.headline_frames(headline[0], headline[1], out_w, scale)
        # Hauteur utile du titre : sans sa grande marge transparente (le halo
        # s'y estompe), sauf une marge normale sous le texte.
        head_pad = round((neon_badge.HEADLINE_PAD - neon_badge.HEADLINE_TEXT_PAD) * scale) \
            if head else 0
        head_h = head[0].height - 2 * head_pad if head else 0
        total_h += head_h
        duration = video_duration(video_path)
        samples = detect_face_track(video_path, 0.0, duration, sample_interval_s=0.5,
                                    max_samples=80, confidence_threshold=0.6, max_faces=6)
        faces = [[(f.y, f.h) for f in (getattr(s, "faces", ()) or ())] for s in samples]
        # Le medaillon ne reste que quelques secondes : le recouvrir coute en
        # proportion de sa duree, comme un visage present le meme temps.
        intro_s = _intro_seconds(intro) if intro is not None else float(avoid_intro_s or 0)
        top = choose_top(faces, total_h / out_h, preferred,
                         avoid=(INTRO_ZONE,) if intro_s > 0 else (),
                         avoid_weight=min(0.3, intro_s / max(duration, 1.0)))
        y = (int(round(top * out_h)) + head_h) // 2 * 2       # haut de la ligne du badge
        with tempfile.TemporaryDirectory(prefix="clipfarming_texte_") as tmp:
            out = Path(tmp) / "avec_texte.mp4"
            inputs = ["-i", str(video_path)]
            graph, last = "", "0:v"

            def add(label_in: str, position: str, extra: str = "") -> None:
                nonlocal graph, last
                tag_out = f"l{inputs.count('-i')}"
                graph += (";" if graph else "") + (f"[{last}][{label_in}]overlay={position}"
                                                   f"{extra}:format=auto[{tag_out}]")
                last = tag_out

            if band is not None:
                png = Path(tmp) / "bandeau.png"
                band.save(png)
                inputs += ["-i", str(png)]
                add(f"{inputs.count('-i') - 1}:v", f"0:{y + band_offset}")
            if badge:
                from video import neon_badge

                # Une periode d'animation, bouclee sur tout le clip.
                for k, frame in enumerate(badge):
                    frame.save(Path(tmp) / f"badge_{k:03d}.png")
                inputs += ["-stream_loop", "-1", "-framerate", str(neon_badge.FPS),
                           "-i", str(Path(tmp) / "badge_%03d.png")]
                add(f"{inputs.count('-i') - 1}:v", f"{round(BADGE_LEFT * scale)}:{y}",
                    ":shortest=1")
            if number_anim:
                from video import neon_badge

                for k, frame in enumerate(number_anim):
                    frame.save(Path(tmp) / f"numero_{k:03d}.png")
                inputs += ["-stream_loop", "-1", "-framerate", str(neon_badge.FPS),
                           "-i", str(Path(tmp) / "numero_%03d.png")]
                add(f"{inputs.count('-i') - 1}:v",
                    f"{out_w - round(BADGE_LEFT * scale) - number_anim[0].width}:"
                    f"{y + (row_h - number_anim[0].height) // 2}", ":shortest=1")
            if head:
                from video import neon_badge

                for k, frame in enumerate(head):
                    frame.save(Path(tmp) / f"titre_{k:03d}.png")
                inputs += ["-framerate", str(neon_badge.FPS),
                           "-i", str(Path(tmp) / "titre_%03d.png")]
                add(f"{inputs.count('-i') - 1}:v", f"0:{y - head_h - head_pad}",
                    ":eof_action=pass")
            last = _add_intro(inputs, graph_parts := [graph], last, intro, out_w, out_h, tmp)
            graph = graph_parts[0]
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
        _intro_only(video_path, intro, out_w, out_h, cancel_token)
        return False


def _intro_seconds(intro) -> float:
    """Duree reelle de l'intro (7 s pour celle de ClipsOfStreams), sinon
    INTRO_SECONDS."""
    try:
        from video.ffmpeg_utils import video_duration

        return float(video_duration(intro.video)) if intro is not None else 0.0
    except Exception:  # noqa: BLE001
        return INTRO_SECONDS


def _add_intro(inputs: list, graph_parts: list, last: str, intro, out_w: int, out_h: int,
               workdir) -> str:
    """Ajoute l'intro (et sa phrase) en DERNIER au graphe : au premier plan.
    Renvoie la nouvelle etiquette de sortie."""
    from video.intro_overlay import layers, prepare

    overlay = prepare(intro, out_w, out_h, workdir) if intro is not None else None
    if overlay is None:
        return last
    first = inputs.count("-i")
    for n, (args, filt, position, enable) in enumerate(layers(overlay, out_w, out_h, first)):
        inputs += args
        graph_parts[0] += (f";{filt};[{last}][intro{n}]overlay={position}"
                           f":enable='{enable}':format=auto[i{n}]")
        last = f"i{n}"
    return last


def _intro_only(video_path: str, intro, out_w: int, out_h: int, cancel_token=None) -> None:
    """Secours : pas de bandeau, mais l'intro (retiree du rendu pour passer
    au premier plan) doit quand meme etre la."""
    if intro is None:
        return
    import tempfile

    from video.ffmpeg_utils import run_ffmpeg

    try:
        with tempfile.TemporaryDirectory(prefix="clipfarming_intro_") as tmp:
            inputs = ["-i", str(video_path)]
            parts = ["[0:v]null[b]"]
            last = _add_intro(inputs, parts, "b", intro, out_w, out_h, tmp)
            if last == "b":
                return
            out = Path(tmp) / "avec_intro.mp4"
            run_ffmpeg([*inputs, "-filter_complex", parts[0] + f";[{last}]format=yuv420p[v]",
                        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "17", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                       "intro", cancel_token=cancel_token)
            Path(out).replace(video_path)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "CancelledError":
            raise
        logger.warning(f"Intro non ajoutée : {e}")
