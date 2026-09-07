"""Detection intelligente du contexte (fonctionnalite 1).

Un bon clip se lit "Contexte -> Hook -> Developpement -> Payoff", pas
"debut arbitraire -> phrase coupee -> fin arbitraire". Ce module prend les
bornes brutes d'un passage retenu par le scoring et les recale sur la
structure reelle du discours :

- un debut au milieu d'une phrase remonte au debut de cette phrase ;
- une fin au milieu d'une phrase va jusqu'a la fin de cette phrase ;
- une question suivie de sa reponse inclut la reponse (le payoff) ;
- une marge configurable est ajoutee, sans jamais mordre sur la phrase voisine ;
- la duree maximale demandee par l'utilisateur reste prioritaire sur tout le reste.

Module pur : il ne connait ni ffmpeg, ni la video, seulement des phrases
horodatees (editing/sentences.py). Il ne decide pas non plus lui-meme d'appliquer
son resultat : il renvoie une confiance, et `applied` vaut False quand elle est
sous le seuil -- auquel cas les bornes d'origine sont conservees telles quelles,
conformement au principe "mieux vaut ne rien modifier que mal modifier"
(section 13 du cahier des charges).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.text_utils import contains_phrase, normalize
from editing.sentences import (
    SentenceSpan,
    index_containing,
    index_last_ending_at_or_before,
    index_next_starting_at_or_after,
    join_text,
    sentences_in_range,
)

_SNAP_REPORT_THRESHOLD_S = 0.15


@dataclass(frozen=True)
class ContextResult:
    start: float
    end: float
    applied: bool
    confidence: float
    category: str = ""
    reasons: list[str] = field(default_factory=list)
    truncated_by_max_duration: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "confidence": round(self.confidence, 3),
            "category": self.category,
            "reasons": list(self.reasons),
            "truncated_by_max_duration": self.truncated_by_max_duration,
        }


def detect_category(text: str, narrative_markers: dict) -> str:
    """Type narratif dominant du passage ("revelation", "question"...), a partir
    des marqueurs de config/editing.json -- jamais code en dur ici.

    Renvoie "" si aucun marqueur ne ressort : une categorie inventee serait pire
    qu'une absence de categorie.
    """
    if not text or not narrative_markers:
        return ""

    normalized = normalize(text)
    best_key, best_hits = "", 0
    for key, markers in narrative_markers.items():
        # Les fichiers de config de ce projet portent leurs commentaires dans
        # des cles "_comment"/"_notes" dont la valeur est une CHAINE. Sans ce
        # filtre, la boucle ci-dessous parcourt cette chaine caractere par
        # caractere, chacun se retrouve dans le texte, et "_comment" ressort
        # comme categorie de tous les clips (bug reel constate en test end-to-end).
        if key.startswith("_") or not isinstance(markers, list):
            continue
        hits = sum(1 for m in markers if contains_phrase(normalized, normalize(m)))
        if hits > best_hits:
            best_key, best_hits = key, hits
    return best_key


def adjust_clip_bounds(
    *,
    start: float,
    end: float,
    sentences: list[SentenceSpan],
    video_duration: float,
    max_duration: float,
    min_duration: float = 0.0,
    padding_before_s: float = 0.0,
    padding_after_s: float = 0.0,
    max_lookback_s: float = 12.0,
    max_lookahead_s: float = 12.0,
    payoff_max_gap_s: float = 1.5,
    min_confidence: float = 0.0,
    narrative_markers: dict | None = None,
) -> ContextResult:
    """Recale [start, end] sur la structure du discours. Voir le docstring du
    module pour les regles ; `max_duration` n'est jamais depassee."""
    reasons: list[str] = []

    if not sentences:
        # Aucune parole exploitable : il n'y a rien a recaler, et deviner serait
        # exactement ce que la section 13 interdit.
        return ContextResult(start=start, end=end, applied=False, confidence=0.0,
                             reasons=["aucune phrase detectee dans ce passage"])

    new_start, start_on_boundary = _snap_start(start, sentences, max_lookback_s, max_lookahead_s, reasons)
    new_end, end_on_boundary = _snap_end(end, new_start, sentences, max_lookahead_s, reasons)

    if new_end <= new_start:
        return ContextResult(start=start, end=end, applied=False, confidence=0.0,
                             reasons=["bornes incoherentes apres recalage"])

    new_end = _extend_to_payoff(new_start, new_end, sentences, payoff_max_gap_s, max_duration, reasons)

    padded_start, padded_end = _apply_padding(
        new_start, new_end, sentences, padding_before_s, padding_after_s, video_duration
    )

    padded_start, padded_end, truncated = _enforce_max_duration(
        padded_start, padded_end, new_start, new_end, sentences, max_duration, min_duration, reasons
    )
    padded_start, padded_end = _enforce_min_duration(padded_start, padded_end, min_duration, video_duration)
    padded_start, padded_end = _never_cut_a_word(padded_start, padded_end, sentences, max_duration)

    confidence = 1.0
    if not start_on_boundary:
        confidence -= 0.25
    if not end_on_boundary:
        confidence -= 0.25
    if truncated:
        confidence -= 0.30
    confidence = max(0.0, min(1.0, confidence))

    if confidence < min_confidence:
        return ContextResult(
            start=start, end=end, applied=False, confidence=confidence,
            category=detect_category(join_text(sentences_in_range(sentences, start, end)), narrative_markers or {}),
            reasons=reasons + ["confiance insuffisante -- bornes d'origine conservees"],
            truncated_by_max_duration=False,
        )

    category = detect_category(
        join_text(sentences_in_range(sentences, padded_start, padded_end)), narrative_markers or {}
    )
    return ContextResult(
        start=padded_start, end=padded_end, applied=True, confidence=confidence,
        category=category, reasons=reasons, truncated_by_max_duration=truncated,
    )


# ---------------------------------------------------------------- etapes

def _snap_start(start, sentences, max_lookback_s, max_lookahead_s, reasons) -> tuple[float, bool]:
    idx = index_containing(sentences, start)
    if idx is not None:
        sentence = sentences[idx]
        if start - sentence.start <= max_lookback_s:
            if start - sentence.start > _SNAP_REPORT_THRESHOLD_S:
                reasons.append("debut remonte au debut de la phrase")
            return sentence.start, True
        # Phrase trop longue pour etre reprise depuis son debut : demarrer sur
        # la suivante plutot que sur un mot coupe en deux.
        nxt = index_next_starting_at_or_after(sentences, start)
        if nxt is not None and sentences[nxt].start - start <= max_lookahead_s:
            reasons.append("debut decale a la phrase suivante (phrase en cours trop longue)")
            return sentences[nxt].start, True
        return start, False

    # start tombe dans un silence : demarrer sur la phrase suivante evite
    # d'ouvrir le clip sur un blanc.
    nxt = index_next_starting_at_or_after(sentences, start)
    if nxt is not None and sentences[nxt].start - start <= max_lookahead_s:
        return sentences[nxt].start, True
    return start, False


def _snap_end(end, new_start, sentences, max_lookahead_s, reasons) -> tuple[float, bool]:
    idx = index_containing(sentences, end)
    if idx is not None:
        sentence = sentences[idx]
        if sentence.end - end <= max_lookahead_s:
            if sentence.end - end > _SNAP_REPORT_THRESHOLD_S:
                reasons.append("fin prolongee jusqu'a la fin de la phrase")
            return sentence.end, True
        prev = index_last_ending_at_or_before(sentences, end)
        if prev is not None and sentences[prev].end > new_start:
            reasons.append("fin ramenee a la phrase precedente (phrase suivante trop longue)")
            return sentences[prev].end, True
        return end, False

    prev = index_last_ending_at_or_before(sentences, end)
    if prev is not None and sentences[prev].end > new_start:
        return sentences[prev].end, True
    return end, False


def _extend_to_payoff(new_start, new_end, sentences, payoff_max_gap_s, max_duration, reasons) -> float:
    """Une question qui se termine sans sa reponse est un clip frustrant : si la
    phrase suivante enchaine assez vite, on l'inclut -- a condition que la duree
    maximale le permette."""
    last_idx = index_last_ending_at_or_before(sentences, new_end)
    if last_idx is None or not sentences[last_idx].is_question:
        return new_end
    if last_idx + 1 >= len(sentences):
        return new_end

    answer = sentences[last_idx + 1]
    if answer.start - sentences[last_idx].end > payoff_max_gap_s:
        return new_end
    if answer.end - new_start > max_duration:
        return new_end

    reasons.append("reponse incluse apres la question (payoff)")
    return answer.end


def _apply_padding(new_start, new_end, sentences, padding_before_s, padding_after_s, video_duration):
    """Marge de securite, bornee par les phrases voisines : la marge doit tomber
    dans le silence, jamais sur le dernier mot de la phrase d'avant."""
    prev_idx = index_last_ending_at_or_before(sentences, new_start)
    floor = sentences[prev_idx].end if prev_idx is not None else 0.0
    padded_start = max(0.0, floor, new_start - padding_before_s)

    next_idx = index_next_starting_at_or_after(sentences, new_end)
    ceiling = sentences[next_idx].start if next_idx is not None else video_duration
    padded_end = min(video_duration, ceiling, new_end + padding_after_s)

    return padded_start, max(padded_end, new_end)


def _enforce_max_duration(padded_start, padded_end, core_start, core_end, sentences,
                          max_duration, min_duration, reasons):
    """La duree maximale demandee par l'utilisateur prime sur tout le reste
    (contexte, payoff, marges). On rend d'abord les marges, puis on recule sur
    une fin de phrase, et seulement en dernier recours on coupe net."""
    if padded_end - padded_start <= max_duration:
        return padded_start, padded_end, False

    excess = (padded_end - padded_start) - max_duration

    give_before = min(core_start - padded_start, excess)
    padded_start += max(0.0, give_before)
    excess -= max(0.0, give_before)

    if excess > 0:
        give_after = min(padded_end - core_end, excess)
        padded_end -= max(0.0, give_after)
        excess -= max(0.0, give_after)

    if excess <= 0:
        return padded_start, padded_end, False

    limit = padded_start + max_duration
    fit_idx = index_last_ending_at_or_before(sentences, limit)
    if fit_idx is not None and sentences[fit_idx].end - padded_start >= max(min_duration, 0.0) \
            and sentences[fit_idx].end > padded_start:
        reasons.append("fin ramenee a une fin de phrase pour respecter la duree maximale")
        return padded_start, sentences[fit_idx].end, False

    reasons.append("clip tronque pour respecter la duree maximale")
    return padded_start, limit, True


def _enforce_min_duration(start, end, min_duration, video_duration):
    if min_duration <= 0 or end - start >= min_duration:
        return start, end
    end = min(video_duration, start + min_duration)
    if end - start < min_duration:
        start = max(0.0, end - min_duration)
    return start, end


def _never_cut_a_word(start, end, sentences, max_duration):
    """Filet de securite final : aucune borne ne doit tomber a l'interieur d'un
    mot (un mot coupe en deux s'entend immediatement).

    La borne est deplacee du cote qui GARDE le mot entier quand celui-ci est
    deja majoritairement dans le clip, et du cote qui l'EXCLUT sinon. Sans cette
    seconde branche, une borne posee dans un silence par la duree minimale
    aspirerait le premier mot de la phrase suivante -- un fragment de phrase
    parasite, exactement ce que la detection de contexte cherche a eviter.

    Cette regle passe volontairement apres la duree minimale (un clip un peu
    plus court vaut mieux qu'un mot coupe), mais jamais avant la duree
    MAXIMALE : un mot dont l'inclusion la depasserait est exclu.
    """
    words = [w for s in sentences for w in s.words]
    if not words:
        return start, end

    for w in words:
        if w.start < start < w.end:
            inside = w.end - start
            keep_whole = inside >= 0.5 * (w.end - w.start) and (end - w.start) <= max_duration
            start = min(w.start if keep_whole else w.end, end)
            break

    for w in words:
        if w.start < end < w.end:
            inside = end - w.start
            keep_whole = inside >= 0.5 * (w.end - w.start) and (w.end - start) <= max_duration
            end = max(w.end if keep_whole else w.start, start)
            break

    return start, end


# ---------------------------------------------------------------- apres coup

def resolve_overlaps(items, min_gap: float = 0.0, min_duration: float = 0.0):
    """Etendre des clips pour le contexte peut les faire se chevaucher, alors
    que la selection les avait choisis disjoints. On rogne le clip le moins bien
    note contre celui qui l'est mieux, et on le supprime s'il devient trop court
    -- deux clips qui montrent le meme passage sont pires qu'un seul.

    `items` : liste de (start, end, score). Renvoie une liste de meme longueur,
    contenant (start, end) ou None pour un clip supprime.
    """
    resolved: list[tuple[float, float] | None] = [None] * len(items)
    accepted: list[tuple[float, float]] = []

    for i in sorted(range(len(items)), key=lambda k: items[k][2], reverse=True):
        start, end, _score = items[i]
        for acc_start, acc_end in accepted:
            if end <= acc_start - min_gap or start >= acc_end + min_gap:
                continue
            if start < acc_start:
                end = min(end, acc_start - min_gap)
            else:
                start = max(start, acc_end + min_gap)

        if end - start >= min_duration and end > start:
            accepted.append((start, end))
            resolved[i] = (start, end)

    return resolved
