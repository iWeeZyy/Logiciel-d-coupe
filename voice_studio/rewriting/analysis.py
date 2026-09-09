"""Analyse d'un transcript AVANT toute reecriture.

Module PUR : aucun modele de langue, aucun reseau. Tout ce qui est ici se
calcule a partir du texte, ce qui a deux consequences voulues -- c'est
verifiable par des tests, et cela reste disponible meme sans modele installe.

Ce module ne juge pas et n'invente pas. Quand un element n'est pas trouve, il
est ABSENT du resultat, et l'interface le dit ("aucun résultat final détecté")
plutot que de proposer une phrase choisie au hasard.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from voice_studio.rewriting.models import KeyPoint, TranscriptAnalysis

# Marqueurs de resultat final. Cherches dans la DERNIERE partie du texte : une
# phrase de resultat au milieu d'un script est une annonce, pas le resultat.
PAYOFF_MARKERS = [
    "au final", "resultat final", "le resultat", "voila le resultat", "verdict",
    "en conclusion", "pour conclure", "finalement", "au bout du compte",
    "et voila", "le rendu", "apres travaux", "avant apres", "bilan",
]

# Marqueurs d'accroche : une question, une promesse, une surprise.
HOOK_MARKERS = [
    "attendez", "regardez", "imaginez", "vous n'allez pas croire", "incroyable",
    "personne ne", "je vais vous montrer", "je vais te montrer", "aujourd'hui",
    "ce que vous allez voir", "voici comment", "j'ai teste", "j'ai essaye",
]

# Lexique des niches. Volontairement court et lisible : il sert a ORIENTER le
# vocabulaire, jamais a ajouter une information. Une niche mal devinee ne peut
# donc rien casser -- au pire le ton est moins juste.
NICHE_KEYWORDS = {
    "Rénovation immobilière": ["renovation", "chantier", "maison", "travaux", "peinture",
                               "carrelage", "cuisine amenagee", "salle de bain", "isolation",
                               "plaquiste", "immobilier", "appartement", "demolition"],
    "Cuisine": ["recette", "cuisson", "four", "ingredients", "pate", "plat", "gouter",
                "chef", "assaisonnement", "poele", "dessert"],
    "Finance": ["euros", "investir", "bourse", "epargne", "credit", "banque", "rendement",
                "impots", "patrimoine", "interets"],
    "Sport": ["entrainement", "muscu", "seance", "match", "equipe", "performance",
              "course", "athlete", "competition"],
    "Gaming": ["jeu", "stream", "partie", "manette", "joueur", "niveau", "boss",
               "console", "serveur", "twitch"],
    "Technologie": ["ordinateur", "logiciel", "application", "processeur", "ecran",
                    "intelligence artificielle", "telephone", "batterie", "code"],
    "Automobile": ["voiture", "moteur", "roues", "garage", "vitesse", "permis",
                   "carrosserie", "pneus", "essence"],
    "Histoire": ["siecle", "guerre", "roi", "empire", "epoque", "historien",
                 "revolution", "ancien"],
    "Business": ["entreprise", "client", "chiffre d'affaires", "marketing", "vente",
                 "societe", "salarie", "startup"],
    "Bricolage": ["outil", "perceuse", "visser", "bois", "atelier", "fabriquer",
                  "monter", "planche", "jardin"],
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
# Un nombre reellement prononce : entier, decimal, avec espaces ou points de
# milliers, eventuellement suivi d'une unite collee.
_NUMBER_RE = re.compile(r"\d[\d  .,]*\d|\d")
_PROPER_NOUN_RE = re.compile(r"\b[A-ZÀ-Ý][\wÀ-ÿ'’-]{2,}")
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}\s+)?(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|"
    r"octobre|novembre|décembre)(?:\s+\d{4})?\b|\b(?:19|20)\d{2}\b", re.IGNORECASE)

# Nombres ecrits en toutes lettres. Ils servent deux fois : pour ne pas prendre
# "Huit" en debut de phrase pour un nom propre, et pour ne pas croire qu'un
# chiffre a disparu quand il a ete ecrit en lettres.
NUMBER_WORDS = {
    "zero": "0", "un": "1", "une": "1", "deux": "2", "trois": "3", "quatre": "4",
    "cinq": "5", "six": "6", "sept": "7", "huit": "8", "neuf": "9", "dix": "10",
    "onze": "11", "douze": "12", "treize": "13", "quatorze": "14", "quinze": "15",
    "seize": "16", "vingt": "20", "trente": "30", "quarante": "40",
    "cinquante": "50", "soixante": "60", "cent": "100", "cents": "100",
    "mille": "1000", "million": "1000000", "millions": "1000000",
}

MONTHS = {"janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
          "septembre", "octobre", "novembre", "decembre", "lundi", "mardi",
          "mercredi", "jeudi", "vendredi", "samedi", "dimanche"}

# Mots courants qui commencent une phrase : ils ne sont pas des noms propres.
_SENTENCE_STARTERS = {
    "le", "la", "les", "un", "une", "des", "et", "mais", "donc", "alors", "je",
    "tu", "il", "elle", "on", "nous", "vous", "ils", "elles", "ce", "cette",
    "ces", "mon", "ma", "mes", "son", "sa", "ses", "au", "aux", "du", "de",
    "en", "dans", "pour", "avec", "sans", "sur", "sous", "par", "quand",
    "comme", "si", "ou", "que", "qui", "quoi", "voila", "voici", "ici", "la",
    "apres", "avant", "puis", "ensuite", "enfin", "bref", "bon", "ah", "oh",
}


def fold(text: str) -> str:
    """Texte sans accents ni casse, pour les comparaisons."""
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(re.sub(r"\s+", " ", (text or "").strip()))
    return [p.strip() for p in parts if p.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ'’-]+", text or "")


def numbers_in(text: str) -> list[str]:
    """Nombres prononces, normalises pour la comparaison.

    Les espaces et points de milliers sont retires ("50 000" et "50000" sont le
    meme nombre), la virgule decimale est conservee.
    """
    found = []
    for raw in _NUMBER_RE.findall(text or ""):
        cleaned = raw.replace(" ", "").replace(" ", "").replace(".", "").strip(",.")
        if cleaned:
            found.append(cleaned)
    return found


def dates_in(text: str) -> list[str]:
    return [m.group(0) for m in _DATE_RE.finditer(text or "")]


# Terminaisons verbales frequentes. Un mot capitalise en DEBUT de phrase qui
# se termine ainsi est un verbe, pas un nom propre : "Restez", "Attendez",
# "Imaginez". Sans ce filtre, chaque phrase reformulee commencant par un
# imperatif etait signalee comme un nom invente -- et un avertissement qui se
# declenche a tort ne sert plus a rien.
_VERB_ENDINGS = ("ez", "ons", "ent", "er", "ir", "ait", "aient", "era", "eront",
                 "e", "es", "ee", "ees", "is", "it", "ont", "ant")


def _looks_like_a_verb(folded: str) -> bool:
    return any(folded.endswith(ending) for ending in _VERB_ENDINGS)


def proper_nouns_in(text: str) -> list[str]:
    """Noms propres probables.

    Heuristique assumee, et volontairement prudente en DEBUT de phrase : la, une
    majuscule ne prouve rien, puisque toute phrase en porte une. Un mot capitalise
    au MILIEU d'une phrase est un indice bien plus solide. Elle se trompe encore
    parfois ; elle ne sert qu'a SIGNALER, jamais a bloquer.
    """
    found = []
    for sentence in sentences(text):
        for index, word in enumerate(_PROPER_NOUN_RE.findall(sentence)):
            folded = fold(word)
            if folded in _SENTENCE_STARTERS or folded in NUMBER_WORDS or folded in MONTHS:
                continue
            starts_sentence = sentence.lstrip().startswith(word)
            if starts_sentence and _looks_like_a_verb(folded):
                continue
            found.append(word)
    return found


def detect_hook(text: str, max_sentences: int = 3) -> str:
    """Phrase d'accroche : celle qui ouvre, ou la premiere qui promet quelque
    chose. On ne cherche pas plus loin que les toutes premieres phrases -- un
    hook, par definition, est au debut."""
    head = sentences(text)[:max_sentences]
    if not head:
        return ""
    for sentence in head:
        folded = fold(sentence)
        if "?" in sentence or any(marker in folded for marker in HOOK_MARKERS):
            return sentence
    return head[0]


def detect_payoff(text: str, tail_ratio: float = 0.35) -> str:
    """Resultat final, cherche dans la derniere partie du texte.

    Renvoie une chaine vide quand rien ne ressemble a un resultat : beaucoup de
    videos n'en ont pas, et en inventer un serait la pire des reponses.
    """
    all_sentences = sentences(text)
    if not all_sentences:
        return ""
    start = max(0, int(len(all_sentences) * (1 - tail_ratio)))
    for sentence in all_sentences[start:]:
        folded = fold(sentence)
        if any(marker in folded for marker in PAYOFF_MARKERS):
            return sentence
    return ""


def detect_niche(text: str) -> tuple[str, float]:
    """Niche probable et sa force relative.

    La niche n'oriente que le vocabulaire et le ton. Une detection ratee ne
    peut donc pas introduire de contenu faux.
    """
    folded = fold(text)
    scores = {}
    for niche, keywords in NICHE_KEYWORDS.items():
        hits = sum(folded.count(fold(keyword)) for keyword in keywords)
        if hits:
            scores[niche] = hits
    if not scores:
        return "", 0.0
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    return best, round(scores[best] / total, 2) if total else 0.0


def key_points(text: str, limit: int = 20) -> list[KeyPoint]:
    """Informations a preserver : chiffres, dates, noms propres.

    Chaque point garde la phrase d'ou il vient : c'est ce qui permet de
    verifier ensuite qu'il n'a pas ete deforme, et de la montrer.
    """
    points: list[KeyPoint] = []
    seen = set()
    for sentence in sentences(text):
        for number in numbers_in(sentence):
            if number not in seen:
                seen.add(number)
                points.append(KeyPoint(text=number, kind="chiffre", sentence=sentence))
        for date in dates_in(sentence):
            key = fold(date)
            if key not in seen:
                seen.add(key)
                points.append(KeyPoint(text=date, kind="date", sentence=sentence))
        for noun in proper_nouns_in(sentence):
            key = fold(noun)
            if key not in seen and len(noun) > 3:
                seen.add(key)
                points.append(KeyPoint(text=noun, kind="nom", sentence=sentence))
    return points[:limit]


def repeated_sentences(text: str, min_words: int = 5) -> list[str]:
    """Phrases dites plusieurs fois, a l'identique ou presque."""
    counts = Counter()
    originals = {}
    for sentence in sentences(text):
        if len(words(sentence)) < min_words:
            continue
        key = " ".join(words(fold(sentence)))
        counts[key] += 1
        originals.setdefault(key, sentence)
    return [originals[key] for key, count in counts.items() if count > 1]


def sections(text: str) -> dict:
    """Decoupage grossier en debut / milieu / fin.

    Volontairement grossier : pretendre reconnaitre "contexte", "developpement"
    et "teasing" dans n'importe quel transcript serait une invention. Ce
    decoupage sert au modele a situer les elements, pas a etiqueter le contenu.
    """
    all_sentences = sentences(text)
    if not all_sentences:
        return {}
    total = len(all_sentences)
    if total < 3:
        return {"debut": " ".join(all_sentences)}
    first = max(1, total // 6)
    last = max(1, total // 5)
    return {
        "debut": " ".join(all_sentences[:first]),
        "milieu": " ".join(all_sentences[first:total - last]),
        "fin": " ".join(all_sentences[total - last:]),
    }


def analyse(text: str, spoken_duration_s: float = 0.0) -> TranscriptAnalysis:
    """Analyse complete, sans modele de langue."""
    niche, confidence = detect_niche(text)
    return TranscriptAnalysis(
        hook=detect_hook(text),
        payoff=detect_payoff(text),
        niche=niche,
        niche_confidence=confidence,
        key_points=key_points(text),
        numbers=numbers_in(text),
        proper_nouns=sorted(set(proper_nouns_in(text))),
        repeated_sentences=repeated_sentences(text),
        sections=sections(text),
        word_count=len(words(text)),
        spoken_duration_s=float(spoken_duration_s or 0.0),
    )
