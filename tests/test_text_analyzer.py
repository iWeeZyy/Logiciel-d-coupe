from analysis.text_analyzer import TextAnalyzer
from core.models import Segment, Transcript, Word

KEYWORDS_CONFIG = {
    "strong_keywords": ["secret", "incroyable", "10"],
    "question_starters": ["pourquoi", "comment"],
    "strong_punctuation": ["!", "?"],
    "keyword_weight_overrides": {"secret": 1.5},
}
SCORING_PARAMS = {"short_sentence_word_threshold": 4}


def _make_transcript(text: str, word_specs: list[tuple[str, float, float]]) -> Transcript:
    words = [Word(text=w, start=s, end=e) for w, s, e in word_specs]
    seg = Segment(id=0, start=word_specs[0][1], end=word_specs[-1][2], text=text, words=words)
    return Transcript(language="fr", language_probability=0.99, duration=word_specs[-1][2], full_text=text, segments=[seg])


def test_keyword_detection_counts_and_weights():
    text = "C'est un secret incroyable, un vrai secret."
    words = [("C'est", 0.0, 0.2), ("un", 0.2, 0.3), ("secret", 0.3, 0.6), ("incroyable", 0.6, 1.0),
              ("un", 1.0, 1.1), ("vrai", 1.1, 1.3), ("secret", 1.3, 1.6)]
    transcript = _make_transcript(text, words)
    analyzer = TextAnalyzer(transcript, KEYWORDS_CONFIG, SCORING_PARAMS)

    _, features = analyzer.analyze_window(0.0, 1.6)

    assert features.keyword_hits["secret"] == 2
    assert features.keyword_hits["incroyable"] == 1
    # 2 occurrences de "secret" (poids 1.5) + 1 de "incroyable" (poids 1.0) = 4.0
    assert features.keyword_score_raw == 4.0


def test_question_detection_by_starter_and_mark():
    text = "Pourquoi personne ne le fait. Tu aimes ca?"
    words = [("Pourquoi", 0, 0.5), ("personne", 0.5, 1.0), ("ne", 1.0, 1.1), ("le", 1.1, 1.2),
              ("fait", 1.2, 1.5), ("Tu", 2.0, 2.2), ("aimes", 2.2, 2.5), ("ca?", 2.5, 2.8)]
    transcript = _make_transcript(text, words)
    analyzer = TextAnalyzer(transcript, KEYWORDS_CONFIG, SCORING_PARAMS)

    _, features = analyzer.analyze_window(0.0, 2.8)

    assert features.question_count == 2  # "Pourquoi..." (amorce) + "...ca?" (point d'interrogation)


def test_words_per_second_and_short_sentence_ratio():
    text = "Va. Cours vite. Ceci est une phrase plus longue avec plusieurs mots."
    words = [("Va.", 0, 0.2), ("Cours", 0.2, 0.4), ("vite.", 0.4, 0.6),
              ("Ceci", 0.6, 0.8), ("est", 0.8, 0.9), ("une", 0.9, 1.0), ("phrase", 1.0, 1.2),
              ("plus", 1.2, 1.3), ("longue", 1.3, 1.5), ("avec", 1.5, 1.6), ("plusieurs", 1.6, 1.8),
              ("mots.", 1.8, 2.0)]
    transcript = _make_transcript(text, words)
    analyzer = TextAnalyzer(transcript, KEYWORDS_CONFIG, SCORING_PARAMS)

    _, features = analyzer.analyze_window(0.0, 2.0)

    assert features.word_count == 12
    assert features.words_per_second == 6.0
    # 2 phrases courtes ("Va." = 1 mot, "Cours vite." = 2 mots) sur 3 phrases au total
    assert round(features.short_sentence_ratio, 2) == round(2 / 3, 2)


def test_no_keywords_no_questions_gives_zero():
    text = "Le pain repose tranquillement sur la table de la boulangerie."
    words = [(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]
    transcript = _make_transcript(text, words)
    analyzer = TextAnalyzer(transcript, KEYWORDS_CONFIG, SCORING_PARAMS)

    _, features = analyzer.analyze_window(0.0, words[-1][2])

    assert features.keyword_hits == {}
    assert features.question_count == 0
