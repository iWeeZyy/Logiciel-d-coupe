"""Sous-titres intelligents (fonctionnalite 4) : regroupement des mots, choix
des mots a mettre en evidence, et placement vertical evitant le visage.

Module pur : il ne produit ni fichier .ass, ni .srt -- il decide seulement QUOI
afficher, QUAND, et QUOI mettre en avant. Le rendu vit dans
video/subtitle_renderer.py (ASS incruste) et export/subtitles_export.py
(.srt/.vtt), qui consomment tous les deux les memes CaptionGroup : un seul
decoupage, jamais deux qui divergeraient entre la video et le fichier exporte.

La mise en evidence n'invente rien : elle s'appuie sur les mots-cles deja
configures (config/hooks_keywords.json), sur les chiffres reellement prononces
et sur le niveau audio deja mesure par analysis/audio_analyzer.py. Si aucun mot
ne ressort clairement, aucun n'est mis en avant -- section 13 du cahier des
charges.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from core.models import Word
from core.text_utils import ends_sentence, normalize

_DIGIT_RE = re.compile(r"\d")
_STRIP_PUNCT = ".,;:!?…«»\"'()[]{}-–— \t"

# Poids des signaux de mise en evidence. Aucun ne suffit seul a atteindre 1.0 :
# un mot doit cumuler pour ressortir vraiment.
_KEYWORD_WEIGHT = 0.60
_NUMBER_WEIGHT = 0.55
_LOUDNESS_WEIGHT = 0.35


@dataclass(frozen=True)
class CaptionWord:
    text: str
    start: float
    end: float
    emphasized: bool = False


@dataclass(frozen=True)
class CaptionGroup:
    """Un bloc de sous-titre affiche d'un seul tenant, en temps de SORTIE
    (relatif au debut du clip)."""

    words: tuple[CaptionWord, ...]
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def has_emphasis(self) -> bool:
        return any(w.emphasized for w in self.words)

    @property
    def duration(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------- regroupement

def group_words(
    words: list[Word],
    max_words: int,
    max_chars: int = 0,
    sentence_gap_s: float = 0.6,
    min_words: int = 1,
    break_on_sentence_end: bool = False,
) -> list[list[Word]]:
    """Regroupe des mots en blocs lisibles.

    Coupe quand le bloc est plein, quand le locuteur marque une pause, quand la
    ligne deviendrait trop longue a lire (`max_chars`, 0 = desactive) et --
    seulement si `break_on_sentence_end` -- a la fin d'une phrase.

    `break_on_sentence_end` est desactive par defaut a dessein : les styles
    historiques (progressive, big_text) doivent produire exactement le meme
    decoupage qu'avant l'arrivee des sous-titres intelligents.
    """
    groups: list[list[Word]] = []
    current: list[Word] = []

    for w in words:
        if current:
            gap = w.start - current[-1].end
            too_many = len(current) >= max_words
            too_long = max_chars > 0 and len(current) >= min_words and (
                len(" ".join(x.text.strip() for x in current)) + 1 + len(w.text.strip()) > max_chars
            )
            paused = gap > sentence_gap_s
            finished = break_on_sentence_end and len(current) >= min_words and ends_sentence(current[-1].text)

            if too_many or too_long or paused or finished:
                groups.append(current)
                current = []
        current.append(w)

    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------- mise en evidence

def score_emphasis(
    words: list[Word],
    keyword_terms: tuple[str, ...] | list[str] = (),
    weight_overrides: dict | None = None,
    loudness_db: list[float] | None = None,
    loud_threshold_db: float | None = None,
) -> dict[int, float]:
    """Importance de chaque mot (index -> 0..1), a partir de signaux deja
    disponibles : mots-cles configures, chiffres prononces, niveau audio.

    `loudness_db` et `loud_threshold_db` sont fournis par l'appelant depuis
    analysis/audio_analyzer.py (AudioAnalyzer.mean_db) -- ce module ne touche
    jamais a l'audio lui-meme.
    """
    terms = {normalize(t) for t in keyword_terms if t}
    overrides = {normalize(k): v for k, v in (weight_overrides or {}).items()}

    scores: dict[int, float] = {}
    for i, word in enumerate(words):
        clean = normalize(word.text.strip(_STRIP_PUNCT))
        if not clean:
            continue

        score = 0.0
        if clean in terms:
            score += _KEYWORD_WEIGHT * min(2.0, float(overrides.get(clean, 1.0)))
        if _DIGIT_RE.search(word.text):
            score += _NUMBER_WEIGHT
        if loudness_db is not None and loud_threshold_db is not None and i < len(loudness_db):
            if loudness_db[i] >= loud_threshold_db:
                score += _LOUDNESS_WEIGHT

        if score > 0:
            scores[i] = min(1.0, score)
    return scores


def _pick_emphasis(
    words: list[Word],
    groups: list[list[Word]],
    scores: dict[int, float],
    max_ratio: float,
    min_score: float,
) -> set[int]:
    """Retient les mots reellement mis en avant : au plus un par bloc (sinon la
    ligne entiere clignote et plus rien ne ressort) et au plus `max_ratio` du
    clip. Aucun mot au-dessus du seuil -> aucune mise en evidence, jamais un
    mot choisi par defaut."""
    if not scores or max_ratio <= 0:
        return set()

    budget = max(1, int(math.floor(max_ratio * len(words))))
    candidates = sorted(
        (i for i, s in scores.items() if s >= min_score),
        key=lambda i: scores[i],
        reverse=True,
    )
    if not candidates:
        return set()

    index_of_group: dict[int, int] = {}
    position = 0
    for group_index, group in enumerate(groups):
        for _ in group:
            index_of_group[position] = group_index
            position += 1

    chosen: set[int] = set()
    used_groups: set[int] = set()
    for i in candidates:
        if len(chosen) >= budget:
            break
        group_index = index_of_group.get(i)
        if group_index is None or group_index in used_groups:
            continue
        chosen.add(i)
        used_groups.add(group_index)
    return chosen


# ------------------------------------------------------------ construction

def build_captions(
    words: list[Word],
    *,
    clip_start: float = 0.0,
    max_words_per_group: int = 4,
    min_words_per_group: int = 1,
    max_chars_per_group: int = 0,
    sentence_gap_s: float = 0.6,
    break_on_sentence_end: bool = False,
    emphasis_scores: dict[int, float] | None = None,
    max_emphasis_ratio: float = 0.18,
    min_emphasis_score: float = 0.5,
    min_display_s: float = 0.0,
    gap_s: float = 0.0,
) -> list[CaptionGroup]:
    """Blocs de sous-titres prets a etre rendus, en temps relatif au clip.

    Les regles de duree (duree minimale d'affichage, ecart entre deux blocs)
    sont appliquees ICI et pas dans le rendu : le .ass incruste et le .srt
    exporte doivent afficher exactement la meme chose aux memes instants.
    """
    if not words:
        return []

    raw_groups = group_words(
        words,
        max_words=max_words_per_group,
        max_chars=max_chars_per_group,
        sentence_gap_s=sentence_gap_s,
        min_words=min_words_per_group,
        break_on_sentence_end=break_on_sentence_end,
    )
    emphasized = _pick_emphasis(
        words, raw_groups, emphasis_scores or {}, max_emphasis_ratio, min_emphasis_score
    )

    captions: list[CaptionGroup] = []
    word_index = 0
    for i, group in enumerate(raw_groups):
        start = group[0].start - clip_start
        end = group[-1].end - clip_start
        if end - start < min_display_s:
            end = start + min_display_s
        if i + 1 < len(raw_groups):
            next_start = raw_groups[i + 1][0].start - clip_start
            end = min(end, next_start - gap_s)
        end = max(end, start + 0.05)

        caption_words = []
        for w in group:
            caption_words.append(
                CaptionWord(text=w.text.strip(), start=w.start - clip_start,
                            end=w.end - clip_start, emphasized=word_index in emphasized)
            )
            word_index += 1

        captions.append(CaptionGroup(words=tuple(caption_words), start=start, end=end))

    return captions


# ------------------------------------------------------------ positionnement

def choose_margin_v(
    face_y_frac: float | None,
    *,
    default_margin_v: int,
    text_height_px: int,
    frame_height: int = 1920,
    avoid_half_frac: float = 0.16,
    min_margin_v: int = 140,
) -> int:
    """Marge verticale ASS (distance entre le bas du cadre et le bas du texte)
    qui evite le visage.

    Sans visage detecte, la marge configuree par le style est conservee telle
    quelle : deplacer les sous-titres sur une detection incertaine ferait plus
    de mal que de bien.

    Regle : on garde le placement bas tant que le texte reste sous le visage ;
    si le visage occupe le bas du cadre, le texte passe au-dessus de lui.
    """
    if face_y_frac is None:
        return default_margin_v

    face_top = max(0.0, face_y_frac - avoid_half_frac)
    face_bottom = min(1.0, face_y_frac + avoid_half_frac)

    max_margin_below = int((1.0 - face_bottom) * frame_height) - text_height_px
    if max_margin_below >= min_margin_v:
        return max(min_margin_v, min(default_margin_v, max_margin_below))

    above = int((1.0 - face_top) * frame_height)
    highest_allowed = frame_height - text_height_px - min_margin_v
    return max(min_margin_v, min(above, highest_allowed))
