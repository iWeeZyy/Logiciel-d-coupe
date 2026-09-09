"""Redaction du descriptif, sans modele de langue (sections 6 a 9).

Le projet n'embarque aucun LLM local, et la section 21 interdit toute API
distante. La generation est donc EXTRACTIVE, exactement comme
editing/metadata.py le fait deja pour les titres : le texte produit est fait de
phrases reellement prononcees, de mesures reellement calculees, et de rien
d'autre.

Ce que ce module s'interdit, et pourquoi c'est une decision et non une limite
subie : inventer un evenement, un nom, un chiffre ou une intention produirait un
texte plus flatteur et parfois faux. Un descriptif faux fait perdre plus de
temps qu'un descriptif pauvre -- il faut le verifier, donc reecouter le clip,
donc refaire le travail que l'analyse etait censee eviter.

Les guillemets ne sont pas decoratifs : ils disent au lecteur que la phrase
vient du clip et n'a pas ete reformulee.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.text_utils import ends_sentence, normalize
from editing.metadata import (
    KIND_CURIOSITY,
    KIND_DIRECT,
    KIND_PUNCHY,
    build_metadata,
    strip_leading_fillers,
)
from radar.analysis.models import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, format_timestamp

_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
_HASHTAG_CLEAN_RE = re.compile(r"[^0-9A-Za-zÀ-ÿ]+")

# En dessous, le clip ne contient pas assez de parole pour qu'un descriptif
# textuel veuille dire quelque chose.
MIN_WORDS_FOR_DESCRIPTION = 8

CONTEXT_WARNING = ("Le contexte avant le début du clip semble nécessaire pour "
                   "comprendre complètement la situation.")
NO_SPEECH_WARNING = ("Aucune parole exploitable n'a été transcrite : le descriptif "
                     "ne peut pas être généré à partir du contenu.")
LOW_SPEECH_WARNING = ("Très peu de parole dans ce clip : le descriptif reste "
                      "volontairement minimal.")


@dataclass
class Description:
    summary: str = ""
    editorial: str = ""
    short: str = ""
    social: str = ""
    hashtags: tuple = ()
    title_direct: str = ""
    title_curiosity: str = ""
    title_punchy: str = ""
    topics: tuple = ()
    warnings: tuple = field(default_factory=tuple)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def _quote(text: str) -> str:
    """Citation d'une phrase du clip.

    Le point final est retire, jamais le point d'interrogation ni le point
    d'exclamation : le premier est une convention typographique qui ferait
    doublon avec la ponctuation de la phrase porteuse, les seconds font partie
    de ce qui a ete dit et les supprimer changerait le sens de la citation.
    """
    cleaned = (text or "").strip().strip('"«»').strip()
    while cleaned.endswith("."):
        cleaned = cleaned[:-1].rstrip()
    return f"« {cleaned} »" if cleaned else ""


def _sentence(text: str) -> str:
    text = (text or "").strip()
    if text and not ends_sentence(text):
        text += "."
    return text


def _as_hashtag(value: str) -> str:
    cleaned = _HASHTAG_CLEAN_RE.sub("", (value or "").strip())
    return f"#{cleaned}" if len(cleaned) >= 3 else ""


def _starts_mid_thought(sentences) -> bool:
    """Le clip commence-t-il au milieu d'une idee ?

    Deux indices reels : une premiere phrase qui ne se termine pas par une
    ponctuation (elle a ete coupee), ou une premiere phrase tres courte suivie
    immediatement d'une autre. On ne pretend pas savoir ce qui precede le clip
    -- on signale seulement qu'il en manque probablement.
    """
    if not sentences:
        return False
    first = sentences[0]
    if len(_words(first.text)) <= 3 and len(sentences) > 1:
        return True
    lowered = first.text.strip()
    return bool(lowered) and lowered[0].islower()


def _emotion_clause(emotions) -> str:
    """Une phrase sur l'emotion, uniquement quand elle repose sur des marqueurs
    explicites. La formulation nomme sa source ("les mots employés") : ce qui
    est mesure, ce sont des mots, pas un etat d'esprit."""
    strong = [e for e in emotions if e.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM)]
    if not strong:
        return ""
    names = [e.name for e in strong[:2]]
    joined = " et ".join(names)
    return f"Les mots employés à ce moment marquent {joined}."


def build_summary(sentences, key_moment, profile, emotions) -> str:
    """Resume factuel (section 9) : ouverture, moment marquant, conclusion.

    Trois a cinq lignes au maximum, faites de citations et de mesures.
    """
    if not sentences:
        return ""

    parts: list[str] = []
    opening = strip_leading_fillers(sentences[0].text)
    if len(_words(opening)) >= 3:
        parts.append(f"Le clip s'ouvre sur {_quote(opening)}.")

    if key_moment is not None and key_moment.reaction_text:
        stamp = format_timestamp(key_moment.start)
        parts.append(f"Le passage le plus marqué se situe à {stamp} : "
                     f"{_quote(key_moment.reaction_text)}.")
        if key_moment.payoff_text and key_moment.payoff_text != key_moment.reaction_text:
            parts.append(f"Il enchaîne sur {_quote(key_moment.payoff_text)}.")

    clause = _emotion_clause(emotions)
    if clause:
        parts.append(clause)

    if profile is not None and profile.longest_silence_s >= 1.5:
        parts.append(f"Un silence de {profile.longest_silence_s:.1f} s marque le clip.")

    return " ".join(parts[:5]).strip()


def _short_description(key_moment, ranked_sentence: str) -> str:
    source = ""
    if key_moment is not None and key_moment.reaction_text:
        source = key_moment.reaction_text
    source = source or ranked_sentence
    cleaned = strip_leading_fillers(source).strip()
    if not cleaned:
        return ""
    # strip_leading_fillers nettoie les deux extremites : une question y perd
    # son point d'interrogation, et "Attends quoi ?" devient une affirmation.
    # On le remet quand la phrase d'origine en avait un.
    ending = ""
    for mark in ("?", "!"):
        if source.strip().endswith(mark):
            ending = mark
            break
    words = _words(cleaned)
    if len(words) > 18:
        cleaned = " ".join(words[:18]) + "…"
        ending = ""
    cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned
    if ending:
        return f"{cleaned} {ending}"
    return _sentence(cleaned)


def _social_description(short: str, key_moment, hashtags) -> str:
    lines = []
    if short:
        lines.append(short)
    if key_moment is not None and key_moment.payoff_text and key_moment.payoff_text != key_moment.reaction_text:
        lines.append(_quote(key_moment.payoff_text))
    if hashtags:
        lines.append(" ".join(hashtags))
    return "\n\n".join(lines).strip()


def _hashtags(base, *, creator_label: str, category: str, platform: str,
              lexicon, is_clip: bool) -> tuple:
    """Hashtags du contenu, puis de la provenance.

    Les seconds (#Twitch, le nom du createur, le jeu) sont VERIFIABLES : ils
    viennent des metadonnees renvoyees par la plateforme, pas d'une supposition
    sur le contenu. C'est ce qui autorise a les ajouter alors que le reste du
    module ne rajoute rien.
    """
    out: list[str] = []
    for tag in base:
        tag = tag if tag.startswith("#") else f"#{tag}"
        if tag not in out:
            out.append(tag)

    if is_clip:
        for tag in lexicon.twitch_hashtags:
            if tag not in out:
                out.append(tag)

    for value in (creator_label, category):
        tag = _as_hashtag(value)
        if tag and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)

    return tuple(out[:lexicon.max_hashtags])


def describe(sentences, *, profile, key_moment, emotions, lexicon,
             creator_label: str = "", category: str = "", is_clip: bool = False,
             keyword_terms=()) -> Description:
    """Assemble tous les textes proposes a l'utilisateur.

    Les titres viennent de editing/metadata.build_metadata, deja utilise par le
    pipeline video : trois variantes (direct, curiosite, punchy) extraites du
    contenu reel. Aucune seconde implementation de titre n'a ete ecrite.
    """
    warnings: list[str] = []

    if not sentences:
        return Description(warnings=(NO_SPEECH_WARNING,))

    word_count = sum(len(_words(s.text)) for s in sentences)
    if word_count < MIN_WORDS_FOR_DESCRIPTION:
        summary = build_summary(sentences, key_moment, profile, emotions)
        return Description(summary=summary, warnings=(LOW_SPEECH_WARNING,))

    metadata = build_metadata(list(sentences), keyword_terms=tuple(keyword_terms))
    titles = {t.kind: t.text for t in metadata.titles}

    summary = build_summary(sentences, key_moment, profile, emotions)

    editorial_parts = [metadata.description] if metadata.description else []
    if key_moment is not None and key_moment.reaction_text:
        stamp = format_timestamp(key_moment.start)
        editorial_parts.append(f"Le moment qui donne son intérêt au clip arrive à {stamp} : "
                               f"{_quote(key_moment.reaction_text)}.")
    clause = _emotion_clause(emotions)
    if clause:
        editorial_parts.append(clause)
    editorial = " ".join(p for p in editorial_parts if p).strip()

    best_sentence = metadata.titles[0].source_sentence if metadata.titles else ""
    short = _short_description(key_moment, best_sentence)

    hashtags = _hashtags(metadata.hashtags, creator_label=creator_label, category=category,
                         platform="", lexicon=lexicon, is_clip=is_clip)
    social = _social_description(short, key_moment, hashtags)

    from content_factory.diversity import topic_signature
    topics = tuple(sorted(topic_signature(" ".join(s.text for s in sentences), max_terms=8)))

    if _starts_mid_thought(sentences):
        warnings.append(CONTEXT_WARNING)
    for reason in metadata.reasons:
        if "moins de trois" in normalize(reason):
            warnings.append("Moins de trois titres : aucune autre phrase du clip ne s'y prêtait.")

    return Description(
        summary=summary,
        editorial=editorial,
        short=short,
        social=social,
        hashtags=hashtags,
        title_direct=titles.get(KIND_DIRECT, ""),
        title_curiosity=titles.get(KIND_CURIOSITY, ""),
        title_punchy=titles.get(KIND_PUNCHY, ""),
        topics=topics,
        warnings=tuple(warnings),
    )
