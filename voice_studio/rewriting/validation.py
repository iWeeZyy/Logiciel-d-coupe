"""Verification d'un script reecrit, sans modele de langue.

Deux questions, et deux seulement :

1. Qu'est-ce qui a ete PERDU ? Les chiffres et les noms du transcript qui ne
   se retrouvent pas dans le script.
2. Qu'est-ce qui a ete AJOUTE ? Les chiffres, dates et noms qui apparaissent
   dans le script sans exister dans le transcript.

La seconde est la plus importante : c'est la seule facon, sans lire le texte,
de reperer une information inventee. Elle n'est pas parfaite -- une affirmation
fausse ecrite sans chiffre ni nom passera -- et l'interface ne pretend pas le
contraire.

L'indice de transformation n'est PAS une mesure d'originalite juridique. C'est
la part de groupes de mots du script qui n'existent pas tels quels dans la
source, et rien de plus.
"""
from __future__ import annotations

from voice_studio.rewriting.analysis import (
    NUMBER_WORDS,
    dates_in,
    fold,
    numbers_in,
    proper_nouns_in,
    sentences,
    words,
)
from voice_studio.rewriting.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    RewriteWarning,
)

SHINGLE_SIZE = 4          # groupes de 4 mots : assez long pour etre significatif

# Les nombres en toutes lettres viennent de analysis.py : une seule table dans
# le projet, sinon les deux finiraient par ne plus dire la meme chose.

def _shingles(text: str, size: int = SHINGLE_SIZE) -> set:
    tokens = [fold(w) for w in words(text)]
    if len(tokens) < size:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1)}


def transformation_index(source: str, rewritten: str) -> float:
    """Part des groupes de mots du script absents de la source.

    0 = le texte est repris tel quel, 1 = plus aucun groupe de quatre mots en
    commun. A presenter comme une « différence de formulation estimée ».
    """
    new = _shingles(rewritten)
    if not new:
        return 0.0
    original = _shingles(source)
    return round(len(new - original) / len(new), 3)


def missing_numbers(source: str, rewritten: str) -> list:
    """Chiffres du transcript absents du script, en toutes lettres comprises."""
    written = set(numbers_in(rewritten))
    written |= {NUMBER_WORDS[fold(w)] for w in words(rewritten) if fold(w) in NUMBER_WORDS}
    return [n for n in dict.fromkeys(numbers_in(source)) if n not in written]


def added_numbers(source: str, rewritten: str) -> list:
    original = set(numbers_in(source))
    return [n for n in dict.fromkeys(numbers_in(rewritten)) if n not in original]


def added_dates(source: str, rewritten: str) -> list:
    original = {fold(d) for d in dates_in(source)}
    return [d for d in dict.fromkeys(dates_in(rewritten)) if fold(d) not in original]


def added_names(source: str, rewritten: str) -> list:
    """Noms propres nouveaux.

    Heuristique : elle signale, elle n'accuse pas. Un mot capitalise en debut
    de phrase reformulee peut ressortir a tort ; le message reste au
    conditionnel et la phrase concernee est montree.
    """
    original = {fold(n) for n in proper_nouns_in(source)}
    return [n for n in dict.fromkeys(proper_nouns_in(rewritten)) if fold(n) not in original]


def sentence_containing(text: str, needle: str) -> str:
    folded = fold(needle)
    for sentence in sentences(text):
        if folded in fold(sentence):
            return sentence
    return ""


def check(source: str, rewritten: str, target_words: int = 0,
          tolerance: float = 0.25) -> list:
    """Liste des points a verifier. Vide = rien de suspect trouve."""
    warnings: list[RewriteWarning] = []

    for number in added_numbers(source, rewritten):
        warnings.append(RewriteWarning(
            kind="chiffre_ajoute",
            message=f"Le chiffre « {number} » n'apparaît pas dans le transcript source.",
            excerpt=sentence_containing(rewritten, number)))
    for date in added_dates(source, rewritten):
        warnings.append(RewriteWarning(
            kind="date_ajoutee",
            message=f"La date « {date} » n'apparaît pas dans le transcript source.",
            excerpt=sentence_containing(rewritten, date)))
    for name in added_names(source, rewritten):
        warnings.append(RewriteWarning(
            kind="nom_ajoute",
            message=f"Le nom « {name} » n'apparaît pas dans le transcript source.",
            excerpt=sentence_containing(rewritten, name)))

    lost = missing_numbers(source, rewritten)
    if lost:
        warnings.append(RewriteWarning(
            kind="chiffre_perdu",
            message="Des chiffres du transcript ne sont pas repris : "
                    + ", ".join(lost[:6]) + ("…" if len(lost) > 6 else "")))

    count = len(words(rewritten))
    if not count:
        warnings.append(RewriteWarning(kind="vide", message="Le script produit est vide."))
    elif target_words:
        if count < target_words * (1 - tolerance):
            warnings.append(RewriteWarning(
                kind="trop_court",
                message=f"Script nettement plus court que demandé : {count} mots "
                        f"pour environ {target_words} attendus."))
        elif count > target_words * (1 + tolerance):
            warnings.append(RewriteWarning(
                kind="trop_long",
                message=f"Script nettement plus long que demandé : {count} mots "
                        f"pour environ {target_words} attendus."))
    return warnings


def confidence(warnings: list, index: float, has_hook: bool, has_payoff: bool) -> str:
    """Niveau de confiance, jamais une garantie.

    Ce qui la fait baisser : une information qui semble ajoutee (le plus grave),
    un texte trop proche de la source, ou un transcript dont on n'a su tirer ni
    accroche ni resultat.
    """
    invented = [w for w in warnings if w.kind.endswith("_ajoute") or w.kind.endswith("_ajoutee")]
    if invented or any(w.kind == "vide" for w in warnings):
        return CONFIDENCE_LOW
    if index < 0.35:
        return CONFIDENCE_LOW
    penalties = len([w for w in warnings if w.kind in ("chiffre_perdu", "trop_court", "trop_long")])
    if penalties or not (has_hook and has_payoff) or index < 0.6:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH
