"""Caler le TEXTE DU SCRIPT sur les instants de la voix generee.

DEFAUT REEL A L'ORIGINE DE CE MODULE. Les sous-titres etaient le texte rendu
par Faster-Whisper apres ecoute de la voix generee. Cela suffit pour le
minutage, pas pour le texte : Whisper ecrit ce qu'il a ENTENDU, pas ce que
l'utilisateur a ECRIT. Un mot mal reconnu, une orthographe approchee, un nom
propre defigure -- et, dans le cas signale, une transcription forcee dans une
autre langue que celle de la voix, qui a rendu des sous-titres anglais sur une
narration francaise.

Or le texte, ici, est CONNU : c'est le script. Whisper ne sert qu'a savoir
QUAND chaque mot est prononce. Ce module fait donc la seule chose qui manquait :
il apparie les mots du script avec les mots reconnus, et rend les mots du
SCRIPT portant les instants de la VOIX.

Module PUR : pas de reseau, pas de modele, pas de fichier. Une comparaison de
deux listes de mots, et de l'arithmetique sur des secondes.

CE QU'IL N'INVENTE PAS. Aucun mot n'est ajoute ni supprime : la sortie est
exactement le script, dans son ordre. Pour un mot que Whisper n'a pas reconnu,
l'instant est INTERPOLE entre les deux mots surs qui l'encadrent, au prorata de
la longueur des mots -- et le taux d'appariement est rendu a l'appelant, qui
peut le dire a l'utilisateur plutot que de laisser croire a une precision
qu'on n'a pas.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from core.models import Word
from core.text_utils import normalize

# Duree minimale d'un mot, en secondes. Deux mots ne doivent jamais porter le
# meme instant : le rendu ASS produirait un bloc de duree nulle.
MIN_WORD_S = 0.06

# Duree supposee d'un mot quand il n'y a plus aucun repere apres lui (le script
# continue au-dela de ce que la voix a prononce). Valeur volontairement
# grossiere : elle ne sert qu'a ne pas empiler des mots au meme instant.
FALLBACK_WORD_S = 0.35

_TOKEN_RE = re.compile(r"\S+")
_STRIP = ".,;:!?…«»\"'()[]{}-–—*_/\\"


@dataclass(frozen=True)
class Alignment:
    """Mots du script, minutages de la voix."""

    words: list
    matched: int
    total: int

    @property
    def ratio(self) -> float:
        return (self.matched / self.total) if self.total else 0.0

    @property
    def is_reliable(self) -> bool:
        """Assez de mots reconnus pour que les instants soient ceux de la voix
        et non une interpolation d'un bout a l'autre."""
        return self.ratio >= 0.5


def tokenize(script: str) -> list[str]:
    """Mots du script, ponctuation comprise.

    Decoupe sur les espaces et rien d'autre : « l'arbre, » reste un mot, comme
    Whisper le rend, et le texte remis bout a bout redonne exactement le
    script.
    """
    return _TOKEN_RE.findall(script or "")


def _key(token: str) -> str:
    """Forme comparable d'un mot : sans accent, sans ponctuation, en minuscule."""
    return normalize(token.strip(_STRIP))


def _interpolate(tokens: list[str], start: float, end: float) -> list[tuple[float, float]]:
    """Instants d'une suite de mots sans repere, entre deux bornes connues.

    Repartis au prorata du nombre de CARACTERES et non du nombre de mots : « a »
    et « extraordinairement » ne se prononcent pas en autant de temps, et une
    repartition egale ferait defiler les sous-titres a contretemps.
    """
    if not tokens:
        return []
    span = max(0.0, end - start)
    weights = [max(1, len(t.strip(_STRIP))) for t in tokens]
    total = float(sum(weights))
    times: list[tuple[float, float]] = []
    cursor = start
    for token, weight in zip(tokens, weights):
        share = span * (weight / total) if total else 0.0
        stop = cursor + max(MIN_WORD_S, share)
        times.append((cursor, stop))
        cursor = stop
    return times


def align_script(script: str, words) -> Alignment:
    """Mots du script portant les instants de la voix.

    `words` sont les mots horodates rendus par Faster-Whisper sur le fichier
    audio REELLEMENT genere. La sortie a exactement autant d'elements que le
    script a de mots.
    """
    tokens = tokenize(script)
    heard = list(words or [])
    if not tokens:
        return Alignment(words=[], matched=0, total=0)
    if not heard:
        # Rien d'entendu : on ne peut pas inventer d'instants. On rend le script
        # sans minutage exploitable plutot que de fabriquer une cadence.
        return Alignment(words=[], matched=0, total=len(tokens))

    script_keys = [_key(t) for t in tokens]
    heard_keys = [_key(getattr(w, "text", "")) for w in heard]

    matcher = difflib.SequenceMatcher(None, script_keys, heard_keys, autojunk=False)
    # Instants surs : un mot du script apparie a un mot entendu.
    anchors: dict[int, tuple[float, float]] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            i, j = block.a + offset, block.b + offset
            if not script_keys[i]:
                continue                      # un mot vide (ponctuation seule)
            anchors[i] = (float(heard[j].start), float(heard[j].end))

    matched = len(anchors)
    times: list[tuple[float, float]] = [(0.0, 0.0)] * len(tokens)
    indices = sorted(anchors)

    # Avant le premier repere : on remonte depuis lui, sans jamais passer sous
    # zero ni sous le debut de la voix.
    first = indices[0] if indices else None
    if first is not None and first > 0:
        head_end = anchors[first][0]
        head_start = max(0.0, min(float(heard[0].start), head_end))
        for i, pair in enumerate(_interpolate(tokens[:first], head_start, head_end)):
            times[i] = pair

    for position, i in enumerate(indices):
        times[i] = anchors[i]
        following = indices[position + 1] if position + 1 < len(indices) else None
        if following is None or following == i + 1:
            continue
        gap = tokens[i + 1:following]
        for offset, pair in enumerate(_interpolate(gap, anchors[i][1], anchors[following][0])):
            times[i + 1 + offset] = pair

    # Apres le dernier repere : le script continue au-dela de ce que la voix a
    # dit (ou Whisper s'est arrete plus tot). On prolonge a cadence supposee.
    last = indices[-1] if indices else None
    if last is not None and last < len(tokens) - 1:
        tail = tokens[last + 1:]
        cursor = anchors[last][1]
        for offset, token in enumerate(tail):
            stop = cursor + FALLBACK_WORD_S
            times[last + 1 + offset] = (cursor, stop)
            cursor = stop

    if not indices:
        # Aucun mot apparie : la transcription ne correspond pas du tout au
        # script (langue forcee a cote, voix inaudible...). On etale le script
        # sur la duree reellement parlee, et l'appelant le dira.
        span_start = float(heard[0].start)
        span_end = float(heard[-1].end)
        for i, pair in enumerate(_interpolate(tokens, span_start, span_end)):
            times[i] = pair

    aligned = []
    previous_end = 0.0
    for token, (start, end) in zip(tokens, times):
        start = max(start, previous_end)
        end = max(end, start + MIN_WORD_S)
        aligned.append(Word(text=token, start=start, end=end, probability=None))
        previous_end = end

    return Alignment(words=aligned, matched=matched, total=len(tokens))
