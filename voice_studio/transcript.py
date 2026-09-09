"""Lecture, recherche et mise en forme d'une transcription.

Module PUR : uniquement du texte et des nombres, aucune dependance a Qt, au
reseau ou a Whisper. C'est ici que se decide ce que l'utilisateur voit.

REGLE CENTRALE DE VOICE STUDIO : le texte affiche est celui qui a ete
PRONONCE. Rien n'est resume, reformule, corrige ni supprime -- ni les
repetitions, ni les hesitations, ni les passages juges sans interet. Le mode
"nettoye" ci-dessous ne fait donc que de la mise en page : il ne retire ni
n'ajoute aucun mot, et `same_words()` le verifie.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

VIEW_RAW = "raw"
VIEW_CLEAN = "clean"

# Une pause plus longue que cela separe deux paragraphes dans la vue nettoyee.
PARAGRAPH_GAP_S = 2.0


def format_timestamp(seconds: float) -> str:
    """hh:mm:ss. Les millisecondes ne servent a rien a l'ecran ; les exports,
    eux, gardent la precision reelle."""
    total = int(max(0.0, float(seconds or 0.0)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def segment_label(segment) -> str:
    return f"{format_timestamp(segment.start)} → {format_timestamp(segment.end)}"


def raw_text(transcript, with_timestamps: bool = False) -> str:
    """Le texte tel que la reconnaissance l'a rendu."""
    if transcript is None:
        return ""
    if not with_timestamps:
        return "\n".join(s.text for s in transcript.segments).strip()
    blocks = [f"[{segment_label(s)}]\n{s.text}" for s in transcript.segments]
    return "\n\n".join(blocks).strip()


def words_of(text: str) -> list[str]:
    """Suite des mots d'un texte, accents et casse ignores.

    Sert a prouver qu'une mise en forme n'a rien change au contenu.
    """
    normalized = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.findall(r"[0-9a-zA-Z']+", stripped.lower())


def same_words(before: str, after: str) -> bool:
    """Les deux textes disent-ils exactement les memes mots, dans le meme ordre ?"""
    return words_of(before) == words_of(after)


def _capitalise_first_letter(text: str) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1:]
    return text


def clean_text(transcript) -> str:
    """Vue "nettoyee" : MISE EN PAGE seulement.

    Trois choses, et rien d'autre : les espaces surnumeraires sont resserres,
    la premiere lettre d'un paragraphe passe en majuscule, et les segments
    separes par un vrai silence deviennent des paragraphes distincts. Aucun mot
    n'est retire, ajoute, corrige ni deplace -- une hesitation reste une
    hesitation. La vue brute reste disponible a tout moment, et c'est elle qui
    fait foi.
    """
    if transcript is None or not transcript.segments:
        return ""

    paragraphs: list[list[str]] = []
    current: list[str] = []
    previous_end = None
    for segment in transcript.segments:
        text = re.sub(r"\s+", " ", (segment.text or "")).strip()
        if not text:
            continue
        if previous_end is not None and (segment.start - previous_end) > PARAGRAPH_GAP_S and current:
            paragraphs.append(current)
            current = []
        current.append(text)
        previous_end = segment.end
    if current:
        paragraphs.append(current)

    return "\n\n".join(_capitalise_first_letter(" ".join(p)) for p in paragraphs).strip()


def view_text(transcript, view: str = VIEW_RAW, with_timestamps: bool = False) -> str:
    if view == VIEW_CLEAN:
        return clean_text(transcript)
    return raw_text(transcript, with_timestamps=with_timestamps)


@dataclass(frozen=True)
class Coverage:
    """Jusqu'ou la transcription va, comparee a la duree de la source.

    Une transcription integrale doit couvrir toute la video. Ce calcul est ce
    qui permet de le DIRE plutot que de l'esperer : il additionne la duree
    reellement transcrite et compare la fin du dernier segment a la fin du
    media.
    """

    spoken_s: float = 0.0
    last_end_s: float = 0.0
    media_duration_s: float = 0.0

    @property
    def tail_gap_s(self) -> float:
        """Temps entre le dernier mot transcrit et la fin de la video."""
        if self.media_duration_s <= 0:
            return 0.0
        return max(0.0, self.media_duration_s - self.last_end_s)

    @property
    def reached_end(self) -> bool:
        """La transcription va-t-elle jusqu'au bout ?

        Tolerance de 15 secondes OU 2 % de la duree : une video se termine
        souvent par un generique ou un silence, ou personne ne parle. Au-dela,
        il manque quelque chose et l'interface le dit.
        """
        if self.media_duration_s <= 0:
            return True
        tolerance = max(15.0, self.media_duration_s * 0.02)
        return self.tail_gap_s <= tolerance


def coverage(transcript, media_duration_s: float = 0.0) -> Coverage:
    segments = list(getattr(transcript, "segments", []) or [])
    spoken = sum(max(0.0, s.end - s.start) for s in segments)
    last_end = max((s.end for s in segments), default=0.0)
    return Coverage(spoken_s=spoken, last_end_s=last_end,
                    media_duration_s=float(media_duration_s or 0.0))


@dataclass(frozen=True)
class Match:
    """Une occurrence trouvee dans la transcription."""

    segment_index: int
    start_in_segment: int
    end_in_segment: int


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def search(transcript, query: str) -> list[Match]:
    """Occurrences d'un mot ou d'une expression, accents et casse ignores.

    Chercher "reperer" doit trouver "repérer" : la recherche est faite sur une
    version repliee du texte, mais les positions renvoyees sont celles du texte
    REEL -- le repli conserve le nombre de caracteres, sans quoi le surlignage
    tomberait a cote.
    """
    needle = _fold((query or "").strip())
    if not needle or transcript is None:
        return []

    matches: list[Match] = []
    for index, segment in enumerate(transcript.segments):
        haystack = _fold(segment.text or "")
        start = haystack.find(needle)
        while start != -1:
            matches.append(Match(segment_index=index, start_in_segment=start,
                                 end_in_segment=start + len(needle)))
            start = haystack.find(needle, start + 1)
    return matches


def segments_in_range(transcript, start_s: float, end_s: float) -> list:
    """Segments qui touchent l'intervalle demande (selection d'un passage)."""
    if transcript is None:
        return []
    return [s for s in transcript.segments if s.end > start_s and s.start < end_s]
