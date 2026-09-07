"""Montage automatique : suppression des silences inutiles et des hesitations
(fonctionnalite 3).

Ne produit pas de video : renvoie une EditList (editing/timeline.py), c'est-a-dire
la liste des morceaux CONSERVES. Tout le reste du pipeline -- sous-titres,
trajectoire de cadrage, zooms -- se recale ensuite sur cette meme EditList,
donc rien ne peut se desynchroniser.

Deux garde-fous, directement issus de la section 13 du cahier des charges :

- une pause n'est coupee que si elle est a la fois SANS PAROLE (aucun mot ne la
  recouvre) et REELLEMENT SILENCIEUSE cote audio ; un rire, une reaction ou une
  respiration marquee ne sont pas des silences ;
- une pause qui semble volontaire est protegee : juste apres une question
  (effet de suspense) ou juste avant un mot mis en evidence.

Et un plafond global : si le montage voulait retirer plus que
`max_removed_ratio` du clip, on n'applique rien du tout -- a ce niveau, ce n'est
plus un nettoyage, c'est une reecriture du rythme du locuteur.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from core.models import Word
from core.text_utils import normalize
from editing.sentences import SentenceSpan
from editing.timeline import EditList

_STRIP_PUNCT = ".,;:!?…«»\"'()[]{}-–— \t"


@dataclass(frozen=True)
class MontagePlan:
    edit_list: EditList
    removed_silences: int = 0
    removed_fillers: int = 0
    applied: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def removed_duration(self) -> float:
        return self.edit_list.removed_duration

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "removed_silences": self.removed_silences,
            "removed_fillers": self.removed_fillers,
            "removed_duration": round(self.removed_duration, 2),
            "reasons": list(self.reasons),
        }


def _protected_ranges(
    sentences: list[SentenceSpan],
    emphasis_times: list[float],
    protect_after_question_s: float,
    protect_before_emphasis_s: float,
) -> list[tuple[float, float]]:
    """Intervalles ou une pause est consideree comme voulue par le locuteur."""
    protected: list[tuple[float, float]] = []
    for sentence in sentences:
        if sentence.is_question and protect_after_question_s > 0:
            protected.append((sentence.end, sentence.end + protect_after_question_s))
    for t in emphasis_times:
        if protect_before_emphasis_s > 0:
            protected.append((t - protect_before_emphasis_s, t))
    return protected


def _overlaps_any(start: float, end: float, ranges: list[tuple[float, float]]) -> bool:
    return any(start < r_end and end > r_start for r_start, r_end in ranges)


def detect_silences(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    *,
    min_silence_s: float = 0.55,
    keep_padding_s: float = 0.12,
    protected: Optional[list[tuple[float, float]]] = None,
    is_silent: Optional[Callable[[float, float], bool]] = None,
) -> list[tuple[float, float]]:
    """Intervalles reellement supprimables entre deux mots.

    `is_silent(start, end)` est fourni par l'appelant a partir de l'analyse
    audio deja faite : sans lui, on se fierait au seul fait que Whisper n'a rien
    transcrit, et un rire ou une musique se retrouverait coupe."""
    if len(words) < 2:
        return []

    protected = protected or []
    gaps: list[tuple[float, float]] = []

    # Uniquement les silences ENTRE deux mots. Le silence de tete et celui de
    # queue sont la marge posee volontairement par la detection de contexte
    # (editing/context.py) : les couper reviendrait a defaire, module par
    # module, ce qu'un autre module vient de decider -- et a redonner des clips
    # qui demarrent pile sur la premiere syllabe.
    boundaries = [(a.end, b.start) for a, b in zip(words, words[1:])]

    for gap_start, gap_end in boundaries:
        if gap_end - gap_start < min_silence_s:
            continue
        cut_start = gap_start + keep_padding_s
        cut_end = gap_end - keep_padding_s
        if cut_end - cut_start <= 0.05:
            continue
        if _overlaps_any(cut_start, cut_end, protected):
            continue
        if is_silent is not None and not is_silent(cut_start, cut_end):
            continue
        gaps.append((cut_start, cut_end))

    return gaps


def detect_fillers(
    words: list[Word],
    filler_terms: tuple[str, ...] | list[str] = (),
    *,
    remove_repetitions: bool = True,
    max_filler_duration_s: float = 1.2,
    padding_s: float = 0.03,
) -> list[tuple[float, float]]:
    """Hesitations ("euh", "hmm"...) et repetitions immediates d'un meme mot.

    Une repetition n'est retiree que si les deux occurrences se suivent
    immediatement : "je je vais" est un faux depart, "tres tres bien" est une
    intention et doit rester -- d'ou la limite de duree et la comparaison sur le
    mot exact uniquement."""
    terms = {normalize(t) for t in filler_terms if t}
    removed: list[tuple[float, float]] = []

    previous_clean = None
    previous_word = None
    for word in words:
        clean = normalize(word.text.strip(_STRIP_PUNCT))
        duration = word.end - word.start

        if clean and clean in terms and duration <= max_filler_duration_s:
            removed.append((word.start - padding_s, word.end + padding_s))
        elif (
            remove_repetitions
            and clean
            and clean == previous_clean
            and previous_word is not None
            and (word.start - previous_word.end) < 0.35
            and duration <= max_filler_duration_s
            and len(clean) <= 6
        ):
            removed.append((previous_word.start - padding_s, previous_word.end + padding_s))

        previous_clean, previous_word = clean, word

    return removed


def build_montage_plan(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    *,
    sentences: Optional[list[SentenceSpan]] = None,
    emphasis_times: Optional[list[float]] = None,
    remove_silences: bool = True,
    remove_fillers: bool = True,
    min_silence_s: float = 0.55,
    keep_padding_s: float = 0.12,
    protect_after_question_s: float = 1.2,
    protect_before_emphasis_s: float = 0.4,
    filler_terms: tuple[str, ...] | list[str] = (),
    remove_repetitions: bool = True,
    max_removed_ratio: float = 0.35,
    is_silent: Optional[Callable[[float, float], bool]] = None,
) -> MontagePlan:
    """EditList du clip apres nettoyage, ou EditList identite si le montage
    n'apporte rien ou en retirerait trop."""
    identity = EditList.identity(clip_start, clip_end)
    total = clip_end - clip_start
    if total <= 0 or not words:
        return MontagePlan(identity, reasons=("aucun mot a analyser",))

    protected = _protected_ranges(
        sentences or [], emphasis_times or [], protect_after_question_s, protect_before_emphasis_s
    )

    silences = detect_silences(
        words, clip_start, clip_end,
        min_silence_s=min_silence_s, keep_padding_s=keep_padding_s,
        protected=protected, is_silent=is_silent,
    ) if remove_silences else []

    fillers = detect_fillers(
        words, filler_terms, remove_repetitions=remove_repetitions
    ) if remove_fillers else []

    removed = [(max(clip_start, a), min(clip_end, b)) for a, b in silences + fillers]
    removed = [(a, b) for a, b in removed if b - a > 0.02]
    if not removed:
        return MontagePlan(identity, reasons=("rien a retirer",))

    plan = EditList.keeping(clip_start, clip_end, removed)
    ratio = plan.removed_duration / total

    if ratio > max_removed_ratio:
        return MontagePlan(
            identity,
            reasons=(
                f"montage abandonne : il retirerait {ratio:.0%} du clip "
                f"(plafond {max_removed_ratio:.0%})",
            ),
        )
    if plan.output_duration <= 0.5:
        return MontagePlan(identity, reasons=("montage abandonne : il ne resterait presque rien",))

    reasons = []
    if silences:
        reasons.append(f"{len(silences)} silence(s) inutile(s) retire(s)")
    if fillers:
        reasons.append(f"{len(fillers)} hesitation(s)/repetition(s) retiree(s)")
    reasons.append(f"{plan.removed_duration:.1f}s retirees sur {total:.1f}s")

    return MontagePlan(
        plan, removed_silences=len(silences), removed_fillers=len(fillers),
        applied=True, reasons=tuple(reasons),
    )
