"""Designation du moment important d'un clip (section 5).

SETUP -> EVENEMENT -> REACTION -> PAYOFF. Le module cherche la phrase qui porte
la REACTION, puis remonte d'une phrase pour garder ce qui l'a provoquee et
descend pour garder ce qui la conclut. Sans ce contexte, une description
construite sur la seule phrase forte serait incomprehensible.

Pourquoi un score dedie plutot que editing/metadata.score_sentence : ces deux
fonctions cherchent des choses opposees. Un TITRE veut la phrase qui annonce le
sujet, et le score existant favorise donc les premieres phrases. Un MOMENT CLE
veut la phrase qui reagit, qui arrive presque toujours plus tard. Reutiliser le
score des titres ici ramenerait systematiquement le debut du clip -- exactement
ce que la section 5 interdit ("ne pas simplement resumer les premieres
secondes").

Regle absolue : les bornes renvoyees sont celles de phrases entieres. Aucune
phrase n'est coupee en son milieu.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.text_utils import contains_phrase, normalize
from radar.analysis.lexicon import Lexicon
from radar.analysis.models import KeyMoment

# Un moment cle ne peut pas etre le clip entier : au-dela, il ne designe plus
# rien. Bornes larges, exprimees en part de la duree du clip.
MAX_SPAN_RATIO = 0.75
MIN_SPAN_S = 1.5


@dataclass(frozen=True)
class SentenceScore:
    index: int
    score: float
    reasons: tuple


def _words(text: str) -> list[str]:
    return [w for w in normalize(text).split() if w]


def score_sentences(sentences, lexicon: Lexicon, silences=()) -> list[SentenceScore]:
    """Note chaque phrase sur ce qui trahit une reaction.

    Tout ce qui est compte ici est present dans le texte : ponctuation reelle,
    marqueurs d'emotion reellement prononces, brievete, et silence qui precede.
    Rien n'est extrapole.
    """
    scored: list[SentenceScore] = []
    total = max(1, len(sentences))
    silence_ends = [round(s.end, 2) for s in silences]
    short_threshold = lexicon.threshold("short_sentence_words")

    for index, sentence in enumerate(sentences):
        words = _words(sentence.text)
        if not words:
            continue
        normalized = normalize(sentence.text)
        reasons: list[str] = []
        score = 0.0

        exclamations = sentence.text.count("!")
        if exclamations:
            score += min(2.0, exclamations * 1.0)
            reasons.append("exclamation")
        if sentence.is_question:
            score += 0.9
            reasons.append("question")

        emotion_hits = 0
        for markers in lexicon.emotions.values():
            for marker in markers:
                if contains_phrase(normalized, marker):
                    emotion_hits += 1
                    break
        if emotion_hits:
            score += min(2.4, emotion_hits * 1.2)
            reasons.append("marqueur émotionnel")

        superlatives = sum(1 for term in lexicon.superlatives if contains_phrase(normalized, term))
        if superlatives:
            score += min(1.2, superlatives * 0.6)
            reasons.append("superlatif")

        if any(contains_phrase(normalized, term) for term in lexicon.strong_expressions):
            score += 1.0
            reasons.append("expression marquée")

        if len(words) <= short_threshold:
            score += 0.6
            reasons.append("phrase courte")

        # Un silence juste avant : ce qui suit est presque toujours la reaction.
        if any(abs(sentence.start - end) < 0.35 for end in silence_ends):
            score += 0.8
            reasons.append("précédé d'un silence")

        # Leger avantage a la seconde moitie : la reaction suit l'evenement. Le
        # poids reste faible pour qu'une reaction en tout debut de clip -- cas
        # frequent d'un clip lance juste apres l'action -- reste atteignable.
        score += 0.5 * (index / total)

        scored.append(SentenceScore(index=index, score=score, reasons=tuple(reasons)))

    scored.sort(key=lambda item: (-item.score, item.index))
    return scored


def _span_of(sentences, first: int, last: int) -> tuple[float, float]:
    return sentences[first].start, sentences[last].end


def _build(sentences, core: SentenceScore, duration: float) -> KeyMoment:
    max_span = max(MIN_SPAN_S, duration * MAX_SPAN_RATIO) if duration > 0 else float("inf")

    first = max(0, core.index - 1)
    last = min(len(sentences) - 1, core.index + 1)

    # Le payoff : une phrase de plus apres la reaction, si la fenetre le permet.
    if last + 1 < len(sentences):
        start, _ = _span_of(sentences, first, last)
        if sentences[last + 1].end - start <= max_span:
            last += 1

    # Reduction si la fenetre depasse : on retire d'abord le setup, jamais la
    # reaction elle-meme, qui est la raison d'etre du moment.
    while first < core.index and _span_of(sentences, first, last)[1] - _span_of(sentences, first, last)[0] > max_span:
        first += 1
    while last > core.index and _span_of(sentences, first, last)[1] - _span_of(sentences, first, last)[0] > max_span:
        last -= 1

    start, end = _span_of(sentences, first, last)
    text = " ".join(s.text.strip() for s in sentences[first:last + 1]).strip()

    setup = sentences[first].text.strip() if first < core.index else ""
    reaction = sentences[core.index].text.strip()
    payoff = sentences[last].text.strip() if last > core.index else ""

    return KeyMoment(
        start=start,
        end=end,
        text=text,
        label=_label(core),
        setup_text=setup,
        reaction_text=reaction,
        payoff_text=payoff,
        score=round(core.score, 2),
    )


def _label(core: SentenceScore) -> str:
    """Ce que le moment a d'identifiable, en reprenant les raisons mesurees.

    Volontairement descriptif ("réaction marquée par une exclamation") et non
    narratif ("le streamer explose de rire") : la seconde formulation
    raconterait une scene que le systeme n'a pas vue.
    """
    if not core.reasons:
        return "passage le plus marquant du clip"
    return "réaction marquée par : " + ", ".join(core.reasons)


def find(sentences, lexicon: Lexicon, duration_s: float = 0.0,
         silences=(), max_alternatives: int = 0) -> KeyMoment | None:
    """Moment cle du clip, ou None si le clip ne contient pas de parole.

    `max_alternatives` n'est renseigne qu'en analyse approfondie (section 18) :
    afficher plusieurs candidats n'a de sens que si l'utilisateur a demande ce
    niveau de detail.
    """
    if not sentences:
        return None

    scored = score_sentences(sentences, lexicon, silences)
    if not scored:
        return None

    duration = duration_s or (sentences[-1].end - sentences[0].start)
    moment = _build(sentences, scored[0], duration)

    if max_alternatives > 0:
        alternatives = []
        for candidate in scored[1:]:
            if abs(candidate.index - scored[0].index) < 2:
                continue    # un voisin immediat designe le meme moment
            other = _build(sentences, candidate, duration)
            alternatives.append({
                "start": other.start,
                "end": other.end,
                "text": other.reaction_text,
                "score": other.score,
            })
            if len(alternatives) >= max_alternatives:
                break
        moment = KeyMoment(
            start=moment.start, end=moment.end, text=moment.text, label=moment.label,
            setup_text=moment.setup_text, reaction_text=moment.reaction_text,
            payoff_text=moment.payoff_text, score=moment.score,
            alternatives=tuple(alternatives),
        )

    return moment
