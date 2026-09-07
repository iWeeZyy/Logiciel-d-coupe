"""Titres et description automatiques (fonctionnalite 5).

**Extraction, jamais generation.** Sans modele de langue -- et le cahier des
charges impose un traitement 100 % local sans LLM -- la seule facon honnete de
proposer un titre est de reprendre des mots REELLEMENT prononces dans le clip.
Ce module ne fait donc que : choisir la meilleure phrase, la nettoyer de ses
amorces de discours ("et donc", "du coup"...), et la tronquer a une longueur de
titre. Aucun fait, chiffre, citation ou nom n'est ajoute.

Consequence assumee : un titre est bon quand la personne dit une phrase
percutante, et quelconque sinon. C'est le prix du 100 % local, et c'est
preferable a un titre invente qui promettrait ce que le clip ne montre pas.

Quand une variante ne peut pas etre construite honnetement, elle est absente de
la liste plutot que remplie avec du texte de remplissage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.text_utils import ends_sentence, normalize
from editing.sentences import SentenceSpan

KIND_DIRECT = "direct"
KIND_CURIOSITY = "curiosite"
KIND_PUNCHY = "punchy"

# Amorces de discours sans valeur informative en tete de titre. Retirees
# uniquement en DEBUT de phrase : "donc" au milieu d'une phrase porte du sens.
_LEADING_FILLERS = (
    "et", "donc", "alors", "du coup", "en fait", "bah", "ben", "euh", "hmm",
    "mais", "or", "puis", "ensuite", "voila", "bon", "ok", "d'accord", "je veux dire",
)

_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
_TRIM_CHARS = " \t.,;:!?…-–—\"'«»"


@dataclass(frozen=True)
class TitleProposal:
    kind: str
    text: str
    source_sentence: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class ClipMetadata:
    titles: tuple[TitleProposal, ...] = ()
    description: str = ""
    hashtags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "titles": [t.to_dict() for t in self.titles],
            "description": self.description,
            "hashtags": list(self.hashtags),
            "reasons": list(self.reasons),
        }


def _words_of(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def strip_leading_fillers(text: str) -> str:
    """Retire les amorces de discours en tete ("Et donc du coup, ...")."""
    cleaned = text.strip(_TRIM_CHARS)
    changed = True
    while changed and cleaned:
        changed = False
        lowered = normalize(cleaned)
        for filler in _LEADING_FILLERS:
            if lowered == filler:
                return ""
            if lowered.startswith(filler + " "):
                cleaned = cleaned[len(filler):].strip(_TRIM_CHARS)
                changed = True
                break
    return cleaned


def _truncate_words(text: str, max_words: int) -> str:
    """Tronque a un nombre de mots, sans couper un mot en deux. Les points de
    suspension signalent que la phrase continue dans le clip -- ils n'ajoutent
    aucune information, ils en retirent."""
    words = text.split()
    if len(words) <= max_words:
        return text.strip(_TRIM_CHARS)
    return " ".join(words[:max_words]).strip(_TRIM_CHARS) + "…"


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def score_sentence(
    sentence: SentenceSpan,
    keyword_terms: set[str],
    position_ratio: float,
    ideal_words: int = 9,
) -> float:
    """Interet d'une phrase comme titre : mots-cles reels, longueur proche d'un
    titre, phrase complete, et un leger bonus aux premieres phrases (le sujet
    s'annonce en general au debut)."""
    words = _words_of(sentence.text)
    if not words:
        return 0.0

    normalized = normalize(sentence.text)
    keyword_hits = sum(1 for term in keyword_terms if term and term in normalized)
    has_number = any(any(ch.isdigit() for ch in w) for w in words)

    length_penalty = abs(len(words) - ideal_words) / max(ideal_words, 1)
    score = 0.0
    score += min(3.0, keyword_hits) * 1.2
    score += 1.0 if has_number else 0.0
    score += max(0.0, 1.5 - length_penalty * 1.5)
    score += 0.6 if sentence.ends_with_punctuation else 0.0
    score += max(0.0, 0.8 * (1.0 - position_ratio))
    return score


def _candidates(sentences: list[SentenceSpan], keyword_terms: set[str], min_words: int):
    ranked = []
    total = max(1, len(sentences) - 1)
    for i, sentence in enumerate(sentences):
        cleaned = strip_leading_fillers(sentence.text)
        if len(_words_of(cleaned)) < min_words:
            continue
        ranked.append((score_sentence(sentence, keyword_terms, i / total), sentence, cleaned))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def build_metadata(
    sentences: list[SentenceSpan],
    *,
    keyword_terms: tuple[str, ...] | list[str] = (),
    max_title_words: int = 11,
    punchy_max_words: int = 6,
    min_title_words: int = 3,
    max_description_sentences: int = 2,
    max_hashtags: int = 5,
) -> ClipMetadata:
    """Trois propositions de titre et une description, tirees du seul contenu
    reel du clip."""
    terms = {normalize(t) for t in keyword_terms if t}
    ranked = _candidates(sentences, terms, min_title_words)

    if not ranked:
        return ClipMetadata(reasons=("aucune phrase exploitable dans ce clip",))

    titles: list[TitleProposal] = []
    used: set[str] = set()

    def add(kind: str, text: str, source: str) -> None:
        cleaned = _capitalize(text.strip(_TRIM_CHARS))
        key = normalize(cleaned)
        if not cleaned or key in used or len(_words_of(cleaned)) < min_title_words:
            return
        used.add(key)
        titles.append(TitleProposal(kind=kind, text=cleaned, source_sentence=source))

    # Curiosite EN PREMIER quand une vraie question existe : c'est son role
    # naturel, et la traiter apres laisserait le titre direct s'en emparer --
    # la question etant souvent aussi la phrase la mieux notee, la variante
    # curiosite se retrouvait alors vide.
    question = next((s for s in sentences if s.is_question and len(_words_of(s.text)) >= min_title_words), None)
    if question is not None:
        add(KIND_CURIOSITY, _truncate_words(strip_leading_fillers(question.text), max_title_words), question.text)

    # Direct : la meilleure phrase affirmative disponible, telle qu'elle a ete
    # dite. On evite de reprendre la question deja utilisee ci-dessus.
    declaratives = [item for item in ranked if not item[1].is_question] or ranked
    best_score, best_sentence, best_clean = declaratives[0]
    add(KIND_DIRECT, _truncate_words(best_clean, max_title_words), best_sentence.text)

    if question is None:
        # Aucune question dans le clip : on tronque la meilleure phrase avant sa
        # fin. On retire de l'information, on n'en invente pas, et la boucle
        # ouverte vient du clip lui-meme.
        half = max(min_title_words, min(max_title_words, len(_words_of(best_clean)) // 2))
        if len(_words_of(best_clean)) > half + 1:
            add(KIND_CURIOSITY, _truncate_words(best_clean, half), best_sentence.text)

    # Punchy : la phrase courte la plus forte, en majuscules.
    short = [item for item in ranked if len(_words_of(item[2])) <= punchy_max_words]
    source = short[0] if short else ranked[0]
    add(KIND_PUNCHY, _truncate_words(source[2], punchy_max_words).upper(), source[1].text)

    description = _build_description(sentences, ranked, max_description_sentences)
    hashtags = _build_hashtags(sentences, terms, max_hashtags)

    reasons = [f"{len(titles)} titre(s) extrait(s) du contenu reel du clip"]
    if len(titles) < 3:
        reasons.append("moins de trois propositions : aucune phrase supplementaire ne s'y pretait")

    return ClipMetadata(
        titles=tuple(titles), description=description, hashtags=tuple(hashtags), reasons=tuple(reasons)
    )


def _build_description(sentences: list[SentenceSpan], ranked, max_sentences: int) -> str:
    """Une a deux phrases reellement prononcees : la premiere du clip (le
    contexte) puis la mieux notee (le fond), sans jamais les reformuler."""
    chosen: list[str] = []
    seen: set[str] = set()

    for sentence in sentences:
        cleaned = strip_leading_fillers(sentence.text)
        if len(_words_of(cleaned)) >= 4:
            chosen.append(_capitalize(cleaned))
            seen.add(normalize(cleaned))
            break

    for _score, _sentence, cleaned in ranked:
        if len(chosen) >= max_sentences:
            break
        key = normalize(cleaned)
        if key in seen or len(_words_of(cleaned)) < 4:
            continue
        chosen.append(_capitalize(cleaned))
        seen.add(key)

    text = " ".join(chosen).strip()
    if text and not ends_sentence(text):
        text += "."
    return text


def _build_hashtags(sentences: list[SentenceSpan], terms: set[str], limit: int) -> list[str]:
    """Hashtags tires UNIQUEMENT des mots-cles reellement presents dans le clip.

    Rien n'est ajoute pour "faire nombre" : un hashtag sans rapport avec le
    contenu est au mieux inutile, au pire trompeur."""
    text = normalize(" ".join(s.text for s in sentences))
    found = []
    for term in sorted(terms):
        if not term or len(term) < 4 or " " in term:
            continue
        if term in text and term not in found:
            found.append(term)
        if len(found) >= limit:
            break
    return [f"#{t}" for t in found]
