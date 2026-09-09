"""Sous-titres deja publies avec la video, quand ils sont accessibles.

Pourquoi les preferer a une transcription locale : ils sont immediats, ils ne
demandent ni telechargement de l'audio ni calcul, et quand ils ont ete ecrits
par la chaine ils sont plus fideles qu'une reconnaissance automatique.

Ce que ce module ne fait pas : contourner quoi que ce soit. Il demande a
yt-dlp la piste de sous-titres que YouTube expose publiquement, sans
telecharger la video. Si YouTube n'en expose pas, il n'y a pas de sous-titres,
et Voice Studio le dit au lieu d'aller les chercher ailleurs.

Le decodage WebVTT est ici plutot que dans export/ : ce paquet-la ECRIT des
sous-titres, celui-ci en LIT.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.logging_setup import get_logger
from core.models import Segment, Transcript
from voice_studio.models import SOURCE_AUTO_CAPTIONS, SOURCE_SUBTITLES
from voice_studio.youtube_source import explain_error, watch_url

logger = get_logger()

_TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
    r"\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
# Balises de minutage mot a mot des sous-titres automatiques YouTube.
_TAG_RE = re.compile(r"<[^>]+>")


def parse_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    if len(parts) >= 2:
        seconds += int(parts[-2]) * 60
    if len(parts) >= 3:
        seconds += int(parts[-3]) * 3600
    return seconds


def parse_vtt(content: str) -> list[Segment]:
    """WebVTT -> segments horodates.

    Deux particularites des sous-titres automatiques YouTube sont traitees
    ici, parce qu'elles produisent sinon un texte illisible :

    - chaque ligne est REPETEE dans le bloc suivant (effet de defilement) ;
      un bloc dont le texte reprend integralement le precedent est ignore ;
    - le texte porte des balises de minutage mot a mot (<00:00:01.234><c>) ;
      elles sont retirees, le decoupage restant celui des blocs.
    """
    segments: list[Segment] = []
    previous_text = ""
    for block in re.split(r"\n\s*\n", (content or "").replace("\r\n", "\n")):
        match = _TIME_RE.search(block)
        if not match:
            continue
        lines = [line for line in block.split("\n") if line.strip()]
        # Le corps est tout ce qui suit la ligne de minutage.
        body_lines: list[str] = []
        seen_time = False
        for line in lines:
            if not seen_time:
                if _TIME_RE.search(line):
                    seen_time = True
                continue
            body_lines.append(line)

        text = " ".join(_TAG_RE.sub("", line).strip() for line in body_lines)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text == previous_text:
            continue
        # Defilement : le nouveau bloc commence par tout le bloc precedent.
        if previous_text and text.startswith(previous_text):
            text = text[len(previous_text):].strip()
            if not text:
                continue
        start = parse_seconds(match.group("start"))
        end = parse_seconds(match.group("end"))
        segments.append(Segment(id=len(segments), start=start, end=max(end, start + 0.05),
                                text=text, words=[]))
        previous_text = text
    return segments


def pick_language(available: dict, preferred: str | None) -> str | None:
    """Piste a telecharger : celle demandee, sinon le francais, sinon l'anglais,
    sinon la premiere. On ne prend jamais une langue au hasard sans le dire :
    l'appelant recoit le code retenu et l'affiche."""
    if not available:
        return None
    codes = list(available.keys())

    def _match(prefix: str) -> str | None:
        for code in codes:
            if code == prefix or code.startswith(f"{prefix}-"):
                return code
        return None

    if preferred:
        found = _match(preferred)
        if found:
            return found
    return _match("fr") or _match("en") or codes[0]


def fetch_transcript(video_id: str, out_dir: str, preferred_language: str | None = None,
                     ydl_factory=None) -> Transcript | None:
    """Transcription tiree des sous-titres publies, ou None s'il n'y en a pas.

    Rien n'est telecharge d'autre que le fichier de sous-titres lui-meme
    (`skip_download`). Renvoyer None n'est pas une erreur : c'est le cas normal
    d'une video sans sous-titres, et l'appelant passe alors a Whisper.
    """
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return None
        ydl_factory = yt_dlp.YoutubeDL

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt",
        "outtmpl": str(Path(out_dir) / "%(id)s.%(ext)s"),
    }
    if preferred_language:
        options["subtitleslangs"] = [preferred_language, f"{preferred_language}.*", "fr", "en"]

    try:
        with ydl_factory(options) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=True) or {}
    except Exception as error:
        logger.warning(f"Sous-titres indisponibles pour {video_id} : {error}")
        raise SubtitlesUnavailable(explain_error(str(error))) from error

    published = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    language = pick_language(published, preferred_language)
    source = SOURCE_SUBTITLES
    if language is None:
        language = pick_language(automatic, preferred_language)
        source = SOURCE_AUTO_CAPTIONS
    if language is None:
        return None

    path = _find_subtitle_file(out_dir, video_id, language)
    if path is None:
        return None

    segments = parse_vtt(path.read_text(encoding="utf-8", errors="replace"))
    if not segments:
        return None

    transcript = Transcript(
        language=language.split("-")[0],
        language_probability=1.0,
        duration=float(info.get("duration") or (segments[-1].end if segments else 0.0)),
        full_text=" ".join(s.text for s in segments).strip(),
        segments=segments,
    )
    transcript.voice_studio_source = source          # lu par services.py
    return transcript


def _find_subtitle_file(out_dir: str, video_id: str, language: str) -> Path | None:
    directory = Path(out_dir)
    exact = directory / f"{video_id}.{language}.vtt"
    if exact.is_file():
        return exact
    matches = sorted(directory.glob(f"{video_id}*.vtt"))
    return matches[0] if matches else None


class SubtitlesUnavailable(Exception):
    """YouTube n'a pas pu etre interroge pour les sous-titres."""
