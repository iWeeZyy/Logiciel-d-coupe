"""Traduire un plan de montage delire en filtres ffmpeg.

MODULE PUR : il ne construit que des chaines de caracteres. Aucun appel a
ffmpeg ici, donc tout se verifie sans encoder une seule image -- meme partage
que editing/zoom.py (qui planifie) et video/filter_graph.py (qui traduit).

POURQUOI `enable=between(t,...)` ET PAS UN DECOUPAGE. Poser un effet en
coupant le clip, en filtrant un morceau puis en recollant obligerait a
reencoder par segments et a recalculer tous les temps qui suivent. L'option
`enable` de ffmpeg active un filtre sur un intervalle et le laisse transparent
ailleurs : la duree ne change pas, donc les sous-titres et l'audio restent
cales. Les filtres retenus ici ont TOUS ete verifies comme acceptant cette
option (indicateur « T » dans `ffmpeg -filters`) :

    rgbashift, chromashift, eq, hue, noise, pixelize, negate, unsharp, drawbox

`crop`, `scale` et `zoompan` ne l'acceptent PAS, ce qui explique l'absence de
secousse et de zoom dans ce module : ils sont deja assures ailleurs, ou
couteraient la nettete de tout le clip.

UN POINT DE VIGILANCE. Deux effets qui se chevauchent s'additionnent, et le
resultat n'est plus previsible. C'est le planificateur qui garantit l'absence
de chevauchement ; ici on se contente de refuser un intervalle vide.
"""
from __future__ import annotations

from editing.delire import BLIP, DEEPFRY, FLASH, GLITCH, PIXEL, VHS


def _between(start: float, end: float) -> str:
    """L'intervalle d'activation. `t` est le temps de SORTIE en secondes."""
    return f"enable='between(t,{start:.3f},{end:.3f})'"


def _mix(low: float, high: float, strength: float) -> float:
    """Interpole entre une valeur sage et une valeur franche."""
    strength = max(0.0, min(1.0, float(strength)))
    return low + (high - low) * strength


# Un glitch est decoupe en tranches : voir _glitch.
_GLITCH_SLICES = 3


def _glitch(event) -> list:
    """Separation des canaux : le rouge part d'un cote, le bleu de l'autre.

    POURQUOI PLUSIEURS TRANCHES ET NON UNE SEULE. Un decalage constant sur un
    quart de seconde ressemble a une image mal imprimee ; c'est le TREMBLEMENT
    qui evoque un signal qui lache. Premiere tentative : faire varier le
    decalage avec le temps, `rh='18*sin(t*61)'`. ffmpeg l'a refuse -- verifie
    dans `ffmpeg -h filter=rgbashift` : `rh` est un ENTIER, pas une expression,
    contrairement aux parametres de `crop` ou de `zoompan`. D'ou ce decoupage
    en tranches adjacentes, chacune avec son propre decalage fixe : le resultat
    saute d'une valeur a l'autre, ce qui est exactement l'effet cherche.
    """
    amplitude = int(round(_mix(4, 18, event.strength)))
    if amplitude <= 0:
        return []

    pas = event.duration / _GLITCH_SLICES
    # Des decalages volontairement inegaux : trois valeurs regulieres
    # donneraient un balancement propre, donc un effet qui a l'air voulu par
    # un logiciel plutot que subi par un signal.
    motifs = ((1.0, -0.7), (-0.6, 1.0), (0.8, -0.4))
    filtres = []
    for index in range(_GLITCH_SLICES):
        debut = event.start + pas * index
        fin = event.start + pas * (index + 1) if index < _GLITCH_SLICES - 1 else event.end
        rouge, bleu = motifs[index % len(motifs)]
        filtres.append(
            f"rgbashift=rh={int(round(amplitude * rouge))}"
            f":bh={int(round(amplitude * bleu))}:rv=0:bv=0:{_between(debut, fin)}")
    return filtres


def _deepfry(event) -> list:
    """Saturation et contraste pousses, plus un piqué agressif : l'esthetique
    « image reenregistree quinze fois »."""
    saturation = _mix(1.8, 4.0, event.strength)
    contrast = _mix(1.3, 2.2, event.strength)
    sharpen = _mix(1.5, 4.5, event.strength)
    return [
        f"eq=saturation={saturation:.2f}:contrast={contrast:.2f}:{_between(event.start, event.end)}",
        f"unsharp=5:5:{sharpen:.2f}:5:5:0:{_between(event.start, event.end)}",
    ]


def _vhs(event) -> list:
    """Grain, chrominance decalee, legere douceur : une vieille cassette."""
    grain = int(round(_mix(12, 42, event.strength)))
    shift = int(round(_mix(3, 12, event.strength)))
    return [
        f"noise=alls={grain}:allf=t+u:{_between(event.start, event.end)}",
        f"chromashift=cbh={shift}:crh=-{shift}:{_between(event.start, event.end)}",
    ]


def _pixel(event) -> list:
    taille = int(round(_mix(8, 32, event.strength)))
    return [f"pixelize=w={taille}:h={taille}:{_between(event.start, event.end)}"]


def _flash(event) -> list:
    """Un eclair blanc, en surimpression et non en remplacement : on doit
    devenir ce qui se passe derriere."""
    alpha = _mix(0.35, 0.75, event.strength)
    return [f"drawbox=x=0:y=0:w=iw:h=ih:color=white@{alpha:.2f}:t=fill:"
            f"{_between(event.start, event.end)}"]


def _blip(event) -> list:
    return [f"negate={_between(event.start, event.end)}"]


_BUILDERS = {
    GLITCH: _glitch,
    DEEPFRY: _deepfry,
    VHS: _vhs,
    PIXEL: _pixel,
    FLASH: _flash,
    BLIP: _blip,
}


def build_filters(plan) -> list:
    """Les filtres a inserer, dans l'ordre du plan.

    Rend une liste vide quand il n'y a rien a poser : l'appelant n'a alors
    aucune chaine a raccorder, et le graphe reste identique a ce qu'il etait
    sans montage delire -- c'est ce qui garantit qu'un clip sans effet sort
    exactement comme avant.
    """
    if plan is None or getattr(plan, "is_empty", True):
        return []

    filters = []
    for event in plan.events:
        if event.duration <= 0:
            continue
        builder = _BUILDERS.get(event.kind)
        if builder is None:
            # Un effet inconnu est IGNORE, jamais remplace par un autre : un
            # plan venu d'une version plus recente ne doit pas produire un
            # effet que personne n'a demande.
            continue
        filters.extend(builder(event))
    return filters


def build_chain(plan) -> str:
    """Les memes filtres, prets a etre concatenes, ou "" s'il n'y a rien."""
    return ",".join(build_filters(plan))
