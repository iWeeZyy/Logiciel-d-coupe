"""Raccourcissement du titre d'une Story : factuel, ne raccourcit que ce qui
existe deja -- n'invente jamais un mot, ne resume pas, ne reformule pas.
Coupe sur une frontiere de PHRASE quand le texte source en offre une assez
proche de la limite (une phrase complete plutot qu'une clause tronquee),
sinon sur la derniere frontiere de mot -- jamais au milieu d'un mot.

La detection de rumeur est deliberement conservatrice : elle ne se declenche
QUE si le texte source (titre ou resume du flux) le dit deja lui-meme
explicitement ("rumeur", "non confirme", "fuite"...), jamais sur un ton
suppose (le conditionnel "pourrait"/"serait" sert aussi a des titres
parfaitement factuels -- l'utiliser comme signal inventerait un jugement que
la source ne porte pas). C'est une heuristique legere par mots-cles, pas un
modele -- coherent avec la contrainte "aucune IA generative/de classification
lourde" de la specification.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

RUMOR_LABEL = "RUMEUR : "

# Mots-cles qui, dans la presse gaming francophone, marquent deja
# eux-memes un article comme non confirme -- on ne fait que relayer cette
# classification, jamais la deviner depuis le style d'ecriture.
_RUMOR_MARKERS = (
    "rumeur", "rumeurs",
    "non confirme", "non confirmee", "non confirmes", "non confirmees",
    "fuite", "fuites", "leak", "leaks", "leake", "leakee",
    "selon des sources", "d'apres des sources",
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def is_rumor(title: str, summary: str = "") -> bool:
    """True seulement si le titre ou le resume emploie deja un mot marquant
    explicitement l'article comme non confirme."""
    haystack = _normalize(f"{title} {summary}")
    return any(marker in haystack for marker in _RUMOR_MARKERS)


_SENTENCE_ENDERS = (".", "!", "?")

# Une info coupee en pleine clause principale ("Liberty City aurait dû…") se
# lit moins bien qu'une phrase entiere legerement plus courte. On ne prefere
# une frontiere de phrase a la coupure de mot que si elle ne sacrifie pas
# trop de budget -- sinon un titre dont la premiere phrase fait 15
# caracteres se retrouverait tronque bien plus court que necessaire.
_SENTENCE_BOUNDARY_MIN_RATIO = 0.55


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Coupe `text` a `max_chars`, sans jamais couper un mot ni inventer de
    texte. Prefere la derniere frontiere de PHRASE avant la limite (un point,
    un point d'exclamation ou d'interrogation deja present dans le texte
    source) quand elle recupere au moins `_SENTENCE_BOUNDARY_MIN_RATIO` du
    budget -- une phrase complete raconte mieux l'info qu'une clause coupee
    en plein milieu. Sinon retombe sur la derniere frontiere de mot, comme
    avant. Renvoie (texte, a_ete_coupe)."""
    text = text.strip()
    if len(text) <= max_chars:
        return text, False

    cut = text[:max_chars]

    best_sentence_end = -1
    for ender in _SENTENCE_ENDERS:
        pos = cut.rfind(ender)
        if pos > best_sentence_end:
            best_sentence_end = pos
    if best_sentence_end >= max_chars * _SENTENCE_BOUNDARY_MIN_RATIO:
        return cut[:best_sentence_end + 1].strip(), True

    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:-") + "…", True


@dataclass(frozen=True)
class DisplayTitle:
    """Le titre tel qu'il doit apparaitre sur la Story : deja raccourci,
    deja prefixe de "RUMEUR : " si necessaire -- l'appelant n'a plus a
    reappliquer de logique, seulement a l'afficher."""

    text: str
    is_rumor: bool
    truncated: bool


def build_display_title(title: str, summary: str = "", max_chars: int = 90) -> DisplayTitle:
    """Le titre pret a afficher : espaces normalises, raccourci si besoin,
    et prefixe "RUMEUR : " si l'article se presente lui-meme comme tel --
    jamais l'inverse (un article confirme n'est jamais requalifie en
    rumeur, une rumeur n'est jamais presentee comme confirmee)."""
    normalized_title = " ".join(title.split())
    rumor = is_rumor(normalized_title, summary)
    prefix = RUMOR_LABEL if rumor else ""
    budget = max(10, max_chars - len(prefix))
    body, truncated = _truncate(normalized_title, budget)
    return DisplayTitle(text=prefix + body, is_rumor=rumor, truncated=truncated)
