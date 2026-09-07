"""Analyse de la transcription (questions, mots-cles, densite, ponctuation) par fenetre.

Les mots-cles et amorces de question viennent de config/hooks_keywords.json --
volontairement pas codes en dur ici, voir le cahier des charges (section 3).
"""
from __future__ import annotations

import re

from core.models import Segment, TextFeatures, Transcript, Word
from core.text_utils import count_words, normalize, split_sentences


class TextAnalyzer:
    def __init__(self, transcript: Transcript, keywords_config: dict, scoring_params: dict):
        self.transcript = transcript
        self.short_sentence_threshold = scoring_params.get("short_sentence_word_threshold", 6)

        self.strong_keywords = keywords_config.get("strong_keywords", [])
        self.question_starters = keywords_config.get("question_starters", [])
        self.strong_punctuation = keywords_config.get("strong_punctuation", ["!", "?", "...", "…"])
        self.weight_overrides = keywords_config.get("keyword_weight_overrides", {})

        self._keyword_patterns = [
            (kw, re.compile(r"\b" + re.escape(normalize(kw)) + r"\b"))
            for kw in self.strong_keywords
        ]
        self._question_starter_norms = [normalize(q) for q in self.question_starters]

    def words_in_window(self, start: float, end: float) -> list[Word]:
        return [w for w in self.transcript.words() if w.start < end and w.end > start]

    def segments_in_window(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.transcript.segments if s.start < end and s.end > start]

    def window_text(self, start: float, end: float) -> str:
        segs = self.segments_in_window(start, end)
        return " ".join(s.text for s in segs).strip()

    def _keyword_hits(self, normalized_text: str) -> tuple[dict[str, int], float]:
        hits: dict[str, int] = {}
        raw_score = 0.0
        for kw, pattern in self._keyword_patterns:
            matches = pattern.findall(normalized_text)
            if matches:
                count = len(matches)
                hits[kw] = count
                raw_score += count * self.weight_overrides.get(kw, 1.0)
        return hits, raw_score

    def _is_question(self, sentence_norm: str, sentence_raw: str) -> bool:
        if "?" in sentence_raw:
            return True
        return any(sentence_norm.startswith(starter) for starter in self._question_starter_norms)

    def analyze_window(self, start: float, end: float) -> tuple[str, TextFeatures]:
        text = self.window_text(start, end)
        words = self.words_in_window(start, end)
        duration = max(end - start, 1e-6)

        normalized_text = normalize(text)
        keyword_hits, keyword_score_raw = self._keyword_hits(normalized_text)

        sentences = split_sentences(text)
        question_count = 0
        sentence_lengths = []
        for sent in sentences:
            sent_norm = normalize(sent)
            if self._is_question(sent_norm, sent):
                question_count += 1
            sentence_lengths.append(count_words(sent))

        avg_sentence_length = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0.0
        short_count = sum(1 for n in sentence_lengths if n <= self.short_sentence_threshold and n > 0)
        short_sentence_ratio = short_count / len(sentence_lengths) if sentence_lengths else 0.0

        strong_punct_count = sum(text.count(p) for p in self.strong_punctuation)

        words_per_second = len(words) / duration

        speech_rate_acceleration = 0.0
        if len(words) >= 4:
            mid_time = start + duration / 2
            first_half = [w for w in words if w.start < mid_time]
            second_half = [w for w in words if w.start >= mid_time]
            first_dur = max(mid_time - start, 1e-6)
            second_dur = max(end - mid_time, 1e-6)
            wps_first = len(first_half) / first_dur
            wps_second = len(second_half) / second_dur
            speech_rate_acceleration = wps_second - wps_first

        features = TextFeatures(
            word_count=len(words),
            words_per_second=words_per_second,
            question_count=question_count,
            keyword_hits=keyword_hits,
            keyword_score_raw=keyword_score_raw,
            avg_sentence_length=avg_sentence_length,
            short_sentence_ratio=short_sentence_ratio,
            strong_punctuation_count=strong_punct_count,
            speech_rate_acceleration=speech_rate_acceleration,
        )
        return text, features
