"""Fenetres glissantes sur la timeline -> liste de Candidate (audio+texte).

45s -> analyse -> score du passage -> passage suivant -> ... (schema du cahier
des charges, section "detection automatique des hooks").
"""
from __future__ import annotations

import numpy as np

from analysis.audio_analyzer import AudioAnalyzer
from analysis.text_analyzer import TextAnalyzer
from core.logging_setup import get_logger
from core.models import Candidate

logger = get_logger()


def generate_candidates(
    audio_analyzer: AudioAnalyzer,
    text_analyzer: TextAnalyzer,
    video_duration: float,
    clip_duration: float,
    stride_ratio: float,
    min_words_in_window: int,
) -> list[Candidate]:
    stride = max(1.0, clip_duration * stride_ratio)
    candidates: list[Candidate] = []

    if video_duration <= clip_duration:
        starts = [0.0]
    else:
        starts = list(np.arange(0.0, video_duration - clip_duration + stride, stride))

    seen_ends = set()
    for start in starts:
        start = float(max(0.0, start))
        end = float(min(start + clip_duration, video_duration))
        if end <= start:
            continue
        # Evite les doublons exacts en fin de liste (arange peut deborder legerement).
        key = (round(start, 2), round(end, 2))
        if key in seen_ends:
            continue
        seen_ends.add(key)

        if end - start < clip_duration * 0.5:
            # Fenetre de fin trop courte pour etre un clip exploitable.
            continue

        text, text_features = text_analyzer.analyze_window(start, end)
        if text_features.word_count < min_words_in_window:
            # Passage quasi silencieux ou sans parole exploitable : ecarte plutot
            # que de lui attribuer un score artificiellement bas mais non nul.
            continue

        audio_features = audio_analyzer.analyze_window(start, end)
        words = text_analyzer.words_in_window(start, end)

        candidates.append(
            Candidate(
                start=start,
                end=end,
                text=text,
                words=words,
                audio=audio_features,
                text_features=text_features,
            )
        )

    logger.info(f"{len(candidates)} fenetres candidates generees (pas={stride:.1f}s).")
    return candidates
