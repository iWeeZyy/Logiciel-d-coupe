"""Compilation de clips Twitch deja produits : « Top 5 des moments de… ».

Demande de l'utilisateur (compte ClipsOfStreams) : les clips Twitch font une
minute au plus, trop courts pour une « Partie 1 / Partie 2 » qui donne envie
de s'abonner, et sous la minute qu'exige, a notre connaissance, le programme
de remuneration de TikTok. Plusieurs clips deja produits par le logiciel
(cadrage, sous-titres, filigrane faits) sont donc enchaines en une video :

- un NUMERO par clip (#5, #4… #1 en compte a rebours par defaut), en haut a
  gauche, a la meme hauteur que l'intro « Follow » (qui est a droite) : sous
  la barre de TikTok et sous le titre pose dans l'appli ;
- l'intro « Follow » et sa phrase UNE seule fois, au debut (les clips sources
  doivent etre produits sans elle -- sinon elle reviendrait a chaque clip) ;
- en option, deux PARTIES coupees entre deux clips (jamais au milieu d'un
  clip), la premiere finissant sur « La suite dans la PARTIE 2 » : la raison
  de s'abonner qui manquait a des clips isoles ;
- un ecran de fin incruste sur les dernieres secondes (jamais des secondes
  noires ajoutees : elles feraient partir le spectateur).

Le graphe ffmpeg est construit par des fonctions pures (testees sans
ffmpeg) ; les images (numeros, ecran de fin) sont dessinees par Pillow avec
les polices du logiciel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

OUT_W, OUT_H = 1080, 1920
FPS = 30
BRAND = (255, 46, 99, 255)          # rose neon du medaillon ClipsOfStreams
BADGE_TOP_PERCENT = 26.5            # meme bande que l'intro « Follow » (video/intro_overlay.py)
BADGE_MARGIN_PERCENT = 6.0
BADGE_FADE = 0.2
END_CARD_SECONDS = 3.0
END_CARD_TOP_PERCENT = 58.0         # au-dessus des sous-titres et de la legende TikTok
MIN_MONETIZABLE_S = 61.0            # « plus d'une minute »

# Pas de fleche « → » : la police du logiciel n'a pas ce glyphe (carre vide).
NEXT_PART_TEXT = ("La suite dans la PARTIE 2", "Abonne-toi pour ne pas la rater")
LAST_PART_TEXT = ("Abonne-toi pour la prochaine compil", "")


class CompilationError(Exception):
    """Message lisible par l'utilisateur."""


@dataclass(frozen=True)
class Sticker:
    """Une image deja dessinee, recadree sur son contenu, et sa place."""

    path: str
    x: int
    y: int


def _save_cropped(image, out_path) -> Sticker:
    """Recadre l'image plein cadre sur ce qui est dessine : ffmpeg n'a plus a
    melanger que ce rectangle, pas 1080x1920 pixels a chaque image."""
    box = image.getchannel("A").getbbox() or (0, 0, 2, 2)
    left, top = box[0] - box[0] % 2, box[1] - box[1] % 2
    out_path = Path(out_path)
    image.crop((left, top, box[2], box[3])).save(out_path)
    return Sticker(str(out_path), left, top)


@dataclass(frozen=True)
class Clip:
    path: str
    duration: float
    label: str = ""


@dataclass
class Part:
    index: int                      # 1, 2
    total: int                      # nombre de parties
    clips: list = field(default_factory=list)       # list[Clip]
    numbers: list = field(default_factory=list)     # numero affiche par clip

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)


# ------------------------------------------------------------------ plan

def numbering(count: int, countdown: bool = True) -> list[int]:
    """#5..#1 (compte a rebours : le meilleur moment a la fin, on reste
    jusqu'au bout) ou #1..#5."""
    return list(range(count, 0, -1)) if countdown else list(range(1, count + 1))


def split_point(durations: list[float]) -> int:
    """Indice de coupe (1..n-1) qui equilibre au mieux deux parties, coupees
    entre deux clips."""
    if len(durations) < 2:
        raise CompilationError("Il faut au moins 2 clips pour faire 2 parties.")
    total = sum(durations)
    best, best_gap, running = 1, None, 0.0
    for cut in range(1, len(durations)):
        running += durations[cut - 1]
        gap = abs(total - 2 * running)
        if best_gap is None or gap < best_gap:
            best, best_gap = cut, gap
    return best


def plan(clips: list[Clip], parts: int = 1, countdown: bool = True) -> list[Part]:
    """Parties de la compilation, dans l'ordre des clips donnes. La
    numerotation court sur toute la compilation (la partie 2 reprend la ou
    s'arrete la partie 1)."""
    if len(clips) < 2:
        raise CompilationError("Choisis au moins 2 clips pour une compilation.")
    numbers = numbering(len(clips), countdown)
    if parts <= 1:
        return [Part(1, 1, list(clips), numbers)]
    cut = split_point([c.duration for c in clips])
    return [Part(1, 2, list(clips[:cut]), numbers[:cut]),
            Part(2, 2, list(clips[cut:]), numbers[cut:])]


# ---------------------------------------------------------------- images

def _fonts():
    from news_story.post_composer import _font, _subtitle_font

    return _font, _subtitle_font


def badge_rect(out_w: int = OUT_W, out_h: int = OUT_H) -> tuple[int, int]:
    """Coin haut gauche du numero."""
    return (round(min(out_w, out_h) * BADGE_MARGIN_PERCENT / 100),
            round(out_h * BADGE_TOP_PERCENT / 100) if out_h > out_w
            else round(min(out_w, out_h) * BADGE_MARGIN_PERCENT / 100))


def draw_badge(number: int, out_path, *, caption: str = "", out_w: int = OUT_W,
               out_h: int = OUT_H) -> Sticker:
    """PNG transparent plein cadre : « #3 » dans une pastille rose, avec une
    petite ligne au-dessus (« PARTIE 2 ») si fournie."""
    from PIL import Image, ImageDraw

    title_font, small_font = _fonts()
    image = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x, y = badge_rect(out_w, out_h)
    scale = min(out_w, out_h) / 1080
    if caption:
        font = small_font(round(34 * scale))
        width = draw.textlength(caption, font=font)
        pad = round(12 * scale)
        draw.rounded_rectangle([x, y, x + width + 2 * pad, y + round(48 * scale)],
                               radius=round(14 * scale), fill=(0, 0, 0, 170))
        draw.text((x + pad, y + round(5 * scale)), caption, font=font, fill=(255, 255, 255, 255))
        y += round(58 * scale)
    font = title_font(round(104 * scale))
    text = f"#{number}"
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = round(26 * scale), round(14 * scale)
    box = [x, y, x + (right - left) + 2 * pad_x, y + (bottom - top) + 2 * pad_y]
    draw.rounded_rectangle([box[0] + 4, box[1] + 6, box[2] + 4, box[3] + 6],
                           radius=round(26 * scale), fill=(0, 0, 0, 110))      # ombre
    draw.rounded_rectangle(box, radius=round(26 * scale), fill=BRAND)
    draw.text((x + pad_x - left, y + pad_y - top), text, font=font, fill=(255, 255, 255, 255))
    return _save_cropped(image, out_path)


def draw_end_card(lines: tuple[str, str], out_path, *, out_w: int = OUT_W,
                  out_h: int = OUT_H) -> Sticker:
    """PNG transparent plein cadre : un cartouche centre (grande ligne,
    petite ligne), pose sur les dernieres secondes d'une partie."""
    from PIL import Image, ImageDraw

    title_font, small_font = _fonts()
    image = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = min(out_w, out_h) / 1080
    big, small = (lines[0] or "").upper(), lines[1] or ""
    big_font, small_font_ = title_font(round(76 * scale)), small_font(round(44 * scale))
    max_w = out_w - 2 * round(90 * scale)
    while draw.textlength(big, font=big_font) > max_w and big_font.size > 30:
        big_font = title_font(big_font.size - 4)
    big_w = draw.textlength(big, font=big_font)
    small_w = draw.textlength(small, font=small_font_) if small else 0
    big_h, small_h = big_font.size * 1.15, (small_font_.size * 1.3 if small else 0)
    pad = round(28 * scale)
    width = max(big_w, small_w) + 2 * pad
    height = big_h + small_h + 2 * pad
    left = (out_w - width) / 2
    top = round(out_h * END_CARD_TOP_PERCENT / 100) if out_h > out_w else (out_h - height) / 2
    draw.rounded_rectangle([left, top, left + width, top + height], radius=round(30 * scale),
                           fill=(0, 0, 0, 185), outline=BRAND, width=max(2, round(5 * scale)))
    draw.text(((out_w - big_w) / 2, top + pad), big, font=big_font, fill=(255, 255, 255, 255))
    if small:
        draw.text(((out_w - small_w) / 2, top + pad + big_h), small, font=small_font_,
                  fill=(255, 255, 255, 230))
    return _save_cropped(image, out_path)


# ------------------------------------------------------------ graphe ffmpeg

@dataclass(frozen=True)
class PartInputs:
    """Ce qu'il faut a ffmpeg pour une partie, images deja dessinees."""

    clips: tuple                    # tuple[Clip]
    clip_has_audio: tuple           # tuple[bool]
    badges: tuple                   # tuple[Sticker] : un numero par clip
    end_card: Sticker | None = None
    intro: object = None            # video.intro_overlay.IntroOverlay, ou None


def ffmpeg_args(part: PartInputs, out_path, out_w: int = OUT_W, out_h: int = OUT_H) -> list[str]:
    """Arguments ffmpeg (sans le binaire) d'une partie. Fonction pure.

    Chaque clip est mis au format de sortie (sans deformation), a cadence et
    son communs, puis concatene ; les numeros, l'ecran de fin et l'intro sont
    incrustes ensuite, chacun borne a sa fenetre de temps."""
    clips = part.clips
    total = sum(c.duration for c in clips)
    inputs: list[str] = []
    graph: list[str] = []
    for clip in clips:
        inputs += ["-i", clip.path]
    index = len(clips)
    silent = {}
    for i, has_audio in enumerate(part.clip_has_audio):
        if not has_audio:
            inputs += ["-f", "lavfi", "-t", f"{clips[i].duration:.3f}",
                       "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            silent[i] = index
            index += 1
    labels = []
    for i, clip in enumerate(clips):
        graph.append(f"[{i}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                     f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},"
                     f"format=yuv420p,trim=duration={clip.duration:.3f},setpts=PTS-STARTPTS[v{i}]")
        audio = f"{silent[i]}:a" if i in silent else f"{i}:a"
        graph.append(f"[{audio}]aresample=48000,aformat=channel_layouts=stereo,"
                     f"atrim=duration={clip.duration:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=1[cv][ca]")

    current, step = "cv", 0

    def overlay(source: str, position: str, enable: str, extra: str = "") -> None:
        nonlocal current, step
        graph.append(f"[{current}]{source}overlay={position}{extra}:enable='{enable}'[o{step}]")
        current = f"o{step}"
        step += 1

    def sticker(item: Sticker, start: float, length: float, fade: float, tag: str) -> None:
        """Image posee de `start` a `start + length` seulement : l'entree ne
        dure que ce temps, decalee a sa place (setpts), puis laissee passer."""
        nonlocal index
        inputs.extend(["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", item.path])
        graph.append(f"[{index}:v]format=rgba,fade=t=in:st=0:d={fade}:alpha=1,"
                     f"setpts=PTS+{start:.3f}/TB[{tag}{index}]")
        overlay(f"[{tag}{index}]", f"{item.x}:{item.y}",
                f"between(t,{start:.3f},{start + length - 0.001:.3f})", ":eof_action=pass")
        index += 1

    start = 0.0
    for clip, badge in zip(clips, part.badges):
        sticker(badge, start, clip.duration, BADGE_FADE, "b")
        start += clip.duration
    if part.end_card is not None:
        card_start = max(0.0, total - END_CARD_SECONDS)
        sticker(part.end_card, card_start, total - card_start, 0.25, "e")
    if part.intro is not None:
        from video.intro_overlay import mask_asset_path, overlay_spec, text_overlay_spec

        filt, position, enable = overlay_spec(part.intro, out_w, out_h, index, index + 1)
        inputs += ["-i", part.intro.path, "-i", mask_asset_path()]
        index += 2
        graph.append(f"{filt}[intro]")
        overlay("[intro]", position, enable)
        if part.intro.text_png:
            text_inputs, text_filt, _, text_enable = text_overlay_spec(part.intro)
            inputs += text_inputs
            graph.append(f"[{index}:v]{text_filt}[introtext]")
            overlay("[introtext]", "0:0", text_enable)
            index += 1
    graph.append(f"[{current}]format=yuv420p[vout]")
    return [*inputs, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[ca]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]


# ----------------------------------------------------------------- rendu

def _intro_overlay(workdir: Path, out_w: int, out_h: int):
    """L'intro « Follow » et sa phrase, reglees comme pour les clips
    (config/editing.json, bloc « intro »), ou None si elle manque."""
    import json

    from core.paths import app_base_dir
    from video.ffmpeg_utils import video_duration, video_resolution
    from video.intro import from_config
    from video.intro_overlay import IntroOverlay
    from video.intro_text import render_intro_text

    try:
        config = json.loads((app_base_dir() / "config" / "editing.json")
                            .read_text(encoding="utf-8")).get("intro", {})
    except (OSError, ValueError):
        config = {}
    intro = from_config({**config, "enabled": True})
    if intro is None:
        return None
    src_w, src_h = video_resolution(intro.video)
    overlay = IntroOverlay(path=intro.video, src_w=src_w, src_h=src_h,
                           duration=video_duration(intro.video))
    if intro.text:
        from dataclasses import replace

        png = render_intro_text(overlay, intro.text, out_w, out_h, workdir / "intro_text.png")
        overlay = replace(overlay, text_png=str(png))
    return overlay


def read_clips(paths: list[str]) -> list[Clip]:
    from video.ffmpeg_utils import video_duration

    clips = []
    for path in paths:
        if not Path(path).is_file():
            raise CompilationError(f"Clip introuvable : {path}")
        clips.append(Clip(path=str(path), duration=video_duration(str(path)),
                          label=Path(path).stem))
    return clips


def compose(paths: list[str], out_dir, *, name: str = "compilation", parts: int = 1,
            countdown: bool = True, intro: bool = True, on_progress=None,
            cancel_token=None) -> list[tuple[Path, float]]:
    """Ecrit la (ou les) partie(s) dans `out_dir` ; renvoie [(fichier, duree)].
    Leve CompilationError / FfmpegError / CancelledError."""
    import tempfile

    from video.ffmpeg_utils import has_audio_stream, run_ffmpeg

    say = on_progress or (lambda message: None)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_parts = plan(read_clips(paths), parts, countdown)
    written = []
    with tempfile.TemporaryDirectory(prefix="clipfarming_compilation_") as tmp:
        work = Path(tmp)
        intro_overlay = _intro_overlay(work, OUT_W, OUT_H) if intro else None
        for part in plan_parts:
            caption = f"PARTIE {part.index}" if part.total > 1 else ""
            badges = tuple(draw_badge(n, work / f"badge_{part.index}_{k}.png", caption=caption)
                           for k, n in enumerate(part.numbers))
            last = part.index == part.total
            card = draw_end_card(LAST_PART_TEXT if last else NEXT_PART_TEXT,
                                 work / f"end_{part.index}.png")
            inputs = PartInputs(
                clips=tuple(part.clips),
                clip_has_audio=tuple(has_audio_stream(c.path) for c in part.clips),
                badges=badges, end_card=card,
                # Intro au debut de CHAQUE partie : chacune est une video a part
                # entiere, vue par des gens qui n'ont pas vu l'autre.
                intro=intro_overlay)
            suffix = f" - partie {part.index}" if part.total > 1 else ""
            out = out_dir / f"{name}{suffix}.mp4"
            say(f"Montage de la partie {part.index}/{part.total}…" if part.total > 1
                else "Montage de la compilation…")
            run_ffmpeg(ffmpeg_args(inputs, out), f"compilation, partie {part.index}",
                       cancel_token=cancel_token)
            written.append((out, part.duration))
    logger.info(f"Compilation ecrite : {[str(p) for p, _ in written]}")
    return written


def caption_text(title: str, part_index: int = 1, part_total: int = 1) -> str:
    """Legende proposee pour TikTok (modifiable)."""
    title = " ".join((title or "").split()) or "Les meilleurs moments de stream"
    lines = [f"{title} 🔥" + (f" (Partie {part_index}/{part_total})" if part_total > 1 else "")]
    if part_total > 1 and part_index < part_total:
        lines.append("La suite dans la partie 2, abonne-toi pour ne pas la rater 👀")
    else:
        lines.append("Abonne-toi pour la prochaine compil 👀")
    lines.append("Lequel est ton préféré ? 👇")
    lines.append("#stream #twitch #clips")
    return "\n\n".join(lines)
