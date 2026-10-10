"""Le Top news cine du jour en video 9:16, avec voix off.

Demande explicite de l'utilisateur : TikTok met souvent plus en avant une
video qu'un carrousel photo. Les images du Top (daily_top.py) deviennent les
plans de la video ; chacune reste a l'ecran le temps que la voix lise son
texte (titre puis texte sous le titre), avec un lent zoom pour donner du
mouvement. La voix vient de Voice Studio (voice_studio/tts.py) : voix
systeme (Hortense sous Windows) ou voix Piper installee, en local ; ou, au
choix, Chatterbox sur le GPU distant de Hugging Face (Voice Studio ZeroGPU,
plus naturelle, quota gratuit quotidien), avec repli sur la voix locale.
"""
from __future__ import annotations

import re
import tempfile
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

FPS = 30
_TAIL_S = 0.5                 # silence apres chaque texte, avant le plan suivant
_ZOOM_STEP = 0.0005           # zoom par image (~ +8 % en 5 s)
_ZOOM_MAX = 1.10
# Emojis et pictogrammes : illisibles par une voix de synthese.
_EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]")


ZEROGPU = "zerogpu"           # choix de voix : Chatterbox sur GPU distant (Voice Studio ZeroGPU)


def zerogpu_available() -> bool:
    """Le banc d'essai ZeroGPU est-il present (il peut etre supprime) ?"""
    try:
        from voice_studio import zerogpu_service  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


class TopVideoError(Exception):
    """Message lisible par l'utilisateur."""


@dataclass
class Shot:
    image: Path
    text: str


def speakable(text: str) -> str:
    """Texte lisible a voix haute : sans emoji, guillemets francais retires,
    espaces normalises."""
    text = _EMOJI_RE.sub("", text or "")
    text = text.replace("«", "").replace("»", "").replace("…", ".")
    return " ".join(text.split())


def shots_for(cover: Path, slides: list[Path], items: list, day: date, cta: str = "") -> list[Shot]:
    """Un plan pour la couverture, un par news, dans l'ordre du carrousel.
    La phrase d'appel est lue a la fin du dernier plan."""
    from news_story.daily_top import french_date

    shots = [Shot(cover, f"Top news ciné du {french_date(day)}. "
                         f"Voici {len(slides)} infos à ne pas rater.")]
    for i, (path, item) in enumerate(zip(slides, items), 1):
        title = " ".join(item.article.title.split()).rstrip(" .")
        text = f"Numéro {i}. {title}."
        if item.subtitle:
            text += f" {item.subtitle}"
        shots.append(Shot(path, text))
    outro = speakable(cta)
    if outro and len(shots) > 1:
        shots[-1].text = f"{shots[-1].text} {outro}"
    return [Shot(s.image, speakable(s.text)) for s in shots]


def wav_duration(path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate() or 1)


def pick_voice(voice_id: str = ""):
    """La voix demandee si elle existe, sinon la premiere voix francaise
    (Piper avant la voix systeme : plus naturelle), sinon None. « zerogpu »
    est rendu tel quel : la synthese passe alors par le GPU distant."""
    from voice_studio import tts

    if voice_id == ZEROGPU and zerogpu_available():
        return ZEROGPU

    voices = tts.available_voices()
    if voice_id:
        for voice in voices:
            if voice.id == voice_id:
                return voice
    french = [v for v in voices if v.is_french]
    for engine in ("piper", "system"):
        for voice in french:
            if voice.engine == engine:
                return voice
    return french[0] if french else (voices[0] if voices else None)


def shot_args(image: Path, audio: Path, out: Path, duration: float) -> list[str]:
    """Arguments ffmpeg d'un plan : image fixe avec lent zoom centre, voix,
    duree = voix + une demi-seconde. Fonction pure."""
    frames = max(1, round(duration * FPS))
    zoom = (f"scale=2160:3840,zoompan=z='min(zoom+{_ZOOM_STEP},{_ZOOM_MAX})':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS},format=yuv420p")
    return ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(image),
            "-i", str(audio), "-filter_complex", f"[0:v]{zoom}[v];[1:a]apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", str(out)]


def _local_voices(shots: list[Shot], work: Path, voice, rate: float, cancel_token) -> list[Path]:
    from voice_studio import tts

    wavs = []
    for i, shot in enumerate(shots, 1):
        wav = work / f"voix_{i:02d}.wav"
        try:
            tts.synthesize(shot.text, str(wav), voice=voice, rate=rate, cancel_token=cancel_token)
        except tts.TtsError as e:
            raise TopVideoError(f"Voix de synthèse : {e}") from None
        wavs.append(wav)
    return wavs


def _zerogpu_voices(shots: list[Shot], work: Path, on_progress, cancel_token) -> list[Path]:
    """Chatterbox sur le GPU distant de Hugging Face (Voice Studio ZeroGPU),
    en francais, reglages par defaut du banc d'essai. Leve en cas d'echec
    (Space endormi, file d'attente, quota) : l'appelant se replie."""
    from voice_studio import zerogpu_catalogue, zerogpu_service

    params = zerogpu_catalogue.params_for(language="fr")
    wavs = []
    for i, shot in enumerate(shots, 1):
        if on_progress:
            on_progress(f"Voix ZeroGPU… {i}/{len(shots)}")
        wav = work / f"voix_{i:02d}.wav"
        zerogpu_service.generate(shot.text, params, str(wav), cancel_token=cancel_token)
        wavs.append(wav)
    return wavs


def compose_top_video(shots: list[Shot], out_path, *, voice=None, rate: float = 1.05,
                      on_progress=None, cancel_token=None, on_note=None) -> Path:
    """Ecrit la video MP4. Leve TopVideoError / FfmpegError / CancelledError.
    `voice` = « zerogpu » : voix generee en ligne ; si ZeroGPU echoue, TOUTE
    la video est refaite avec la voix locale (jamais deux voix melangees) et
    `on_note` recoit la raison."""
    from utils.errors import CancelledError
    from video.ffmpeg_utils import run_ffmpeg

    if voice is None:
        raise TopVideoError("Aucune voix de synthèse sur cet ordinateur (Voice Studio).")
    out_path = Path(out_path)
    say = on_progress or (lambda *a: None)
    with tempfile.TemporaryDirectory(prefix="clipfarming_top_video_") as workdir:
        work = Path(workdir)
        if voice == ZEROGPU:
            try:
                wavs = _zerogpu_voices(shots, work, lambda m: say(m), cancel_token)
            except CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 -- repli sur la voix locale
                fallback = pick_voice("")
                if fallback is None or fallback == ZEROGPU:
                    raise TopVideoError(f"ZeroGPU indisponible ({e}) et aucune voix locale.") \
                        from None
                logger.warning(f"ZeroGPU indisponible, voix locale utilisee : {e}")
                if on_note:
                    on_note(f"ZeroGPU indisponible ({e}) : voix « {fallback.label} » utilisée.")
                wavs = _local_voices(shots, work, fallback, rate, cancel_token)
        else:
            say("Voix de synthèse…")
            wavs = _local_voices(shots, work, voice, rate, cancel_token)
        parts: list[Path] = []
        for i, (shot, wav) in enumerate(zip(shots, wavs), 1):
            say(f"Montage de la vidéo… plan {i}/{len(shots)}")
            part = work / f"plan_{i:02d}.mp4"
            run_ffmpeg(shot_args(shot.image, wav, part, wav_duration(wav) + _TAIL_S),
                       f"plan {i} de la vidéo du Top", cancel_token=cancel_token)
            parts.append(part)
        listing = work / "plans.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                    "-movflags", "+faststart", str(out_path)],
                   "assemblage de la vidéo du Top", cancel_token=cancel_token)
    logger.info(f"Video du Top ecrite : {out_path}")
    return out_path
