"""Tri par score, suppression des chevauchements (--min-gap), application du
contexte (--pre-roll/--post-roll) et numerotation finale des clips retenus.

Le score d'un clip reste celui de sa fenetre d'origine (clip_duration) : etendre
pour le contexte ne re-note pas le passage, ca change juste ce qui est montre
autour du hook (section 7 du cahier des charges).
"""
from __future__ import annotations

from dataclasses import replace

from analysis.text_analyzer import TextAnalyzer
from core.logging_setup import get_logger
from core.models import ScoredCandidate
from utils.errors import InsufficientContentError

logger = get_logger()


def _conflicts(a_start: float, a_end: float, b_start: float, b_end: float, min_gap: float) -> bool:
    return not (a_start >= b_end + min_gap or a_end <= b_start - min_gap)


def _apply_context(
    sc: ScoredCandidate,
    pre_roll: float,
    post_roll: float,
    max_overshoot_ratio: float,
    video_duration: float,
    text_analyzer: TextAnalyzer,
) -> ScoredCandidate:
    c = sc.candidate
    base_duration = c.end - c.start
    max_duration = base_duration * (1.0 + max_overshoot_ratio)

    new_start = max(0.0, c.start - pre_roll)
    new_end = min(video_duration, c.end + post_roll)

    if (new_end - new_start) > max_duration:
        excess = (new_end - new_start) - max_duration
        pre_ext = c.start - new_start
        post_ext = new_end - c.end
        total_ext = pre_ext + post_ext
        if total_ext > 1e-6:
            new_start += excess * (pre_ext / total_ext)
            new_end -= excess * (post_ext / total_ext)

    new_text = text_analyzer.window_text(new_start, new_end) or c.text
    new_words = text_analyzer.words_in_window(new_start, new_end) or c.words

    extended_candidate = replace(c, start=new_start, end=new_end, text=new_text, words=new_words)
    return ScoredCandidate(candidate=extended_candidate, scores=sc.scores, reasons=sc.reasons)


def select_clips(
    scored_candidates: list[ScoredCandidate],
    nb_clips: int,
    min_gap: float,
    pre_roll: float,
    post_roll: float,
    max_overshoot_ratio: float,
    video_duration: float,
    text_analyzer: TextAnalyzer,
    apply_context: bool = True,
    ranker=None,
) -> list[ScoredCandidate]:
    """`apply_context=False` laisse les bornes brutes : c'est le cas quand la
    detection intelligente du contexte (editing/context.py) est active, puisque
    c'est elle qui decide alors des bornes. Deux mecanismes d'extension
    superposes se marcheraient dessus.

    `ranker` remplace le seul classement par potentiel viral quand on produit
    plusieurs clips d'un coup (content_factory.selection.DiverseRanker) : il
    choisit l'ordre, mais la regle de chevauchement et l'extension de contexte
    restent ici. Absent -> comportement historique, a l'identique."""
    if not scored_candidates:
        raise InsufficientContentError(
            "Aucun passage exploitable n'a ete detecte dans cette video (pas assez "
            "de parole, ou --clip-duration trop long par rapport a la duree totale)."
        )

    def _is_compatible(candidate, already_selected) -> bool:
        """Regle de chevauchement (--min-gap), unique et partagee : le classeur
        du Content Factory s'en sert aussi, plutot que d'en avoir une copie."""
        return not any(
            _conflicts(candidate.start, candidate.end, s.candidate.start, s.candidate.end, min_gap)
            for s in already_selected
        )

    if ranker is not None:
        selected = ranker.pick(scored_candidates, nb_clips, _is_compatible)
    else:
        # Classement par potentiel viral : pour un ScoreBreakdown ne portant que
        # `total` (code anterieur, stub de test), `viral` vaut deja le Hook Score
        # -- voir ScoreBreakdown.__post_init__ -- donc l'ordre reste inchange.
        ranked = sorted(scored_candidates, key=lambda sc: sc.scores.viral, reverse=True)

        selected = []
        for sc in ranked:
            if not _is_compatible(sc.candidate, selected):
                continue
            selected.append(sc)
            if len(selected) >= nb_clips:
                break

    if not selected:
        raise InsufficientContentError(
            "Impossible de selectionner un seul clip sans chevauchement -- essaie "
            "de reduire --min-gap ou --clip-duration."
        )

    if len(selected) < nb_clips:
        logger.warning(
            f"Seulement {len(selected)}/{nb_clips} clips distincts trouves "
            f"(video trop courte, ou --min-gap/--clip-duration trop restrictifs pour ce contenu)."
        )

    if not apply_context:
        return selected

    with_context = [
        _apply_context(sc, pre_roll, post_roll, max_overshoot_ratio, video_duration, text_analyzer)
        for sc in selected
    ]

    # Numerotation clip_01 = meilleur score, deja garanti par le tri d'origine.
    return with_context
