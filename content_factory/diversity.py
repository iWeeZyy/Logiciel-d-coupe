"""Diversite de la selection (sections 4 et 5 du cahier des charges).

Le probleme concret : sur une video de deux heures, les dix meilleurs scores
viennent souvent du meme quart d'heure -- un passage fort "contamine" les
fenetres voisines, qui partagent ses mots et son energie. Prendre le top 10 brut
donne dix variantes du meme moment.

Deux axes de diversite, mesures separement parce qu'ils ne disent pas la meme
chose :

- TEMPORELLE : deux passages eloignes dans la video parlent rarement de la meme
  chose. C'est le signal le moins cher et le plus fiable.
- THEMATIQUE : deux passages peuvent etre eloignes ET redondants (un intervenant
  qui repete son argument). On compare donc aussi le vocabulaire porteur de
  sens, apres normalisation (accents, casse) par core.text_utils.normalize, deja utilise
  par la moderation de texte -- pas une deuxieme normalisation maison.

Aucun modele de langue : pas de plongements, pas de clustering. Un recouvrement
de vocabulaire est une mesure grossiere mais explicable, et c'est ce que la
contrainte "100 % local" permet honnetement.

Module pur.
"""
from __future__ import annotations

from core.text_utils import normalize

# Mots vides francais : ils sont partages par tous les passages et ecrasent
# toute comparaison de vocabulaire si on les garde.
#
# Trois familles, et les deux dernieres ne sont pas evidentes :
# - les mots grammaticaux habituels ;
# - les FORMES CONJUGUEES des auxiliaires et semi-auxiliaires (vais, vas, va,
#   allons, peut, veux, sais...) : "je vais vous parler de X" et "je vais vous
#   parler de Y" ne partagent aucun sujet, mais partageaient trois mots sur
#   quatre tant que ces formes comptaient (constate par un test) ;
# - les VERBES DE DISCOURS (parler, dire, montrer, expliquer, raconter) : ils
#   annoncent un sujet sans jamais en etre un, et sont donc du bruit pur pour
#   comparer deux sujets.
_STOPWORDS = frozenset("""
a ai aux au avec avoir bien c ca ce cela ces cet cette ceux chaque comme d dans
de des du elle elles en encore est et etait ete etre eux fait faire il ils j je
la le les leur lui l m ma mais me meme mes moi mon n ne nos notre nous on ou
par pas peu plus pour qu que qui quoi s sa sans se ses si sont sur t ta te tes
toi ton tous tout toute toutes tu un une vos votre vous y etc alors donc car
oui non ah oh eh bah ben euh hein voila vraiment tres trop peut etre deja
suis es sommes etes sont serai sera seront as avons avez ont avait avaient
vais vas va allons allez vont allait aller
peux peut pouvons pouvez peuvent pouvoir veux veut voulons voulez veulent
vouloir sais sait savons savez savent savoir dois doit devons devez doivent
devoir fais faisons faites font faisait
dire dit dis disons dites disent parler parle parles parlons parlez parlent
montrer montre montrer expliquer explique raconter raconte voir vois voit
voyons voyez voient
""".split())

# En dessous de cette longueur, un mot n'est pas discriminant.
_MIN_WORD_LENGTH = 4


def topic_signature(text: str, max_terms: int = 40) -> frozenset[str]:
    """Vocabulaire porteur de sens d'un passage, normalise.

    `max_terms` borne la signature : sans plafond, un passage long parait
    proche de tout le monde simplement parce qu'il contient plus de mots.
    Les termes sont pris dans l'ordre d'apparition -- le debut d'un passage
    porte son sujet mieux que sa fin.
    """
    seen: list[str] = []
    for raw in normalize(text or "").split():
        word = raw.strip(".,;:!?\"'()[]")
        if len(word) < _MIN_WORD_LENGTH or word in _STOPWORDS or word.isdigit():
            continue
        if word not in seen:
            seen.append(word)
        if len(seen) >= max_terms:
            break
    return frozenset(seen)


def topic_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Recouvrement de vocabulaire, 0 (rien en commun) a 1 (identique).

    Jaccard plutot qu'un simple compte d'intersection : deux passages courts
    partageant trois mots ne se ressemblent pas autant que deux passages dont
    trois mots sont TOUT le vocabulaire.
    """
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def temporal_similarity(
    a_start: float, a_end: float, b_start: float, b_end: float, horizon_s: float
) -> float:
    """Proximite temporelle, 0 (au-dela de l'horizon) a 1 (bord a bord).

    `horizon_s` est la distance a partir de laquelle deux passages sont
    consideres comme appartenant a des moments differents de la video. Elle
    depend de la duree totale et non d'une constante : cinq minutes separent
    deux sujets dans un podcast de deux heures, mais couvrent la moitie d'une
    video de dix minutes.
    """
    if horizon_s <= 0:
        return 0.0
    gap = max(0.0, b_start - a_end) if b_start >= a_end else max(0.0, a_start - b_end)
    return max(0.0, 1.0 - gap / horizon_s)


def redundancy(
    candidate,
    signature: frozenset[str],
    selected: list[tuple[object, frozenset[str]]],
    horizon_s: float,
    topic_weight: float,
    temporal_weight: float,
) -> float:
    """Redondance d'un candidat vis-a-vis de ce qui est DEJA retenu, 0 a 1.

    On prend le maximum et non la moyenne : ressembler beaucoup a un seul clip
    deja selectionne suffit a etre redondant, meme si l'on differe de tous les
    autres.
    """
    if not selected:
        return 0.0

    total_weight = max(1e-6, topic_weight + temporal_weight)
    worst = 0.0
    for other, other_signature in selected:
        topic = topic_similarity(signature, other_signature)
        temporal = temporal_similarity(
            candidate.start, candidate.end, other.start, other.end, horizon_s
        )
        combined = (topic_weight * topic + temporal_weight * temporal) / total_weight
        worst = max(worst, combined)
    return worst


def default_horizon(video_duration: float, nb_clips: int) -> float:
    """Horizon temporel deduit de la video et du nombre de clips demandes.

    Idee : si l'on veut dix clips repartis dans deux heures, deux clips separes
    de moins d'un dixieme de la video occupent la meme zone. Le plancher evite
    qu'une video courte rende toute selection impossible.
    """
    if nb_clips <= 1 or video_duration <= 0:
        return 0.0
    return max(30.0, video_duration / nb_clips)
