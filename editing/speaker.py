"""Detection du locuteur actif (fonctionnalite 2, cas interview/podcast).

Principe : le visage qui parle est celui dont la bouche bouge EN MEME TEMPS que
le son monte. On correle donc, fenetre par fenetre, l'activite de la zone de
bouche de chaque visage (mesuree par video/face_detector.py) avec l'energie
audio deja calculee par analysis/audio_analyzer.py.

Limite assumee, et c'est pour ca que tout passe par un seuil de confiance :
sans modele dedie (type TalkNet), cette heuristique n'est pas fiable a 100 %.
Elle travaille a l'echelle de la phrase, pas du phoneme -- ce que
l'echantillonnage du detecteur (quelques images par seconde) permet
raisonnablement. Quand rien ne ressort clairement, la fonction ne choisit
personne et l'appelant cadre les deux visages : un mauvais choix de locuteur se
voit immediatement, un cadrage large ne derange personne.

Les visages sont identifies par leur position horizontale (gauche/droite) et
non par leur taille : dans un plan fixe d'interview, la gauche reste la gauche,
alors que l'ordre par taille s'inverse des que quelqu'un se penche.

Le nombre de personnes n'est pas limite a deux : sur un plateau, le locuteur
doit pouvoir etre le troisieme ou le quatrieme visage. Le gagnant est celui qui
devance le mieux place des AUTRES d'au moins `margin` -- avec quatre personnes
il faut donc toujours se detacher du lot, pas seulement d'un voisin choisi
d'avance.
"""
from __future__ import annotations

from dataclasses import dataclass

from video.face_detector import FaceSample


@dataclass(frozen=True)
class SpeakerDecision:
    """Locuteur retenu par fenetre de temps. `face_index` suit l'ordre
    horizontal (0 = le plus a gauche) ; None = aucun choix assez sur."""

    t_start: float
    t_end: float
    face_index: int | None
    confidence: float


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 1e-12 or var_y <= 1e-12:
        return 0.0
    return cov / ((var_x * var_y) ** 0.5)


def order_faces_left_to_right(sample: FaceSample):
    """Visages et activite de bouche associee, ordonnes de gauche a droite."""
    pairs = list(zip(sample.faces, sample.mouth_activity or (0.0,) * len(sample.faces)))
    pairs.sort(key=lambda p: p[0].cx)
    return pairs


def detect_active_speaker(
    samples: list[FaceSample],
    audio_energy: list[float],
    *,
    window_s: float = 3.0,
    min_correlation: float = 0.25,
    margin: float = 0.12,
    min_hold_s: float = 2.0,
) -> list[SpeakerDecision]:
    """Suite de decisions "qui parle" le long du clip.

    `audio_energy` doit avoir la meme longueur que `samples` (une valeur par
    instant echantillonne) -- l'appelant l'obtient de
    AudioAnalyzer.mean_db, aucune analyse audio n'est refaite ici.

    L'hysteresis (`min_hold_s`) evite de sauter d'un visage a l'autre a chaque
    respiration : un cadrage qui zigzague est pire qu'un cadrage imparfait mais
    stable.
    """
    if len(samples) < 4 or len(audio_energy) != len(samples):
        return []

    two_face_samples = sum(1 for s in samples if len(s.faces) >= 2)
    if two_face_samples < len(samples) * 0.5:
        return []

    decisions: list[SpeakerDecision] = []
    current_choice: int | None = None
    last_switch_t = samples[0].t - min_hold_s

    window_start = samples[0].t
    while window_start < samples[-1].t:
        window_end = window_start + window_s
        indices = [i for i, s in enumerate(samples) if window_start <= s.t < window_end]
        if len(indices) < 3:
            window_start = window_end
            continue

        energies = [audio_energy[i] for i in indices]

        # Tous les visages presents d'un bout a l'autre de la fenetre sont
        # candidats, pas seulement les deux plus a gauche : sur un plateau a
        # trois ou quatre personnes, celui qui parle n'etait tout simplement
        # jamais teste. Un visage qui apparait ou disparait en cours de fenetre
        # est ecarte -- sa serie d'activite serait trouee, donc sa correlation
        # avec le son n'aurait aucun sens.
        candidate_count = min(len(samples[i].faces) for i in indices)
        correlations: dict[int, float] = {}
        for face_index in range(candidate_count):
            activity = [order_faces_left_to_right(samples[i])[face_index][1] for i in indices]
            correlations[face_index] = _pearson(activity, energies)

        choice, confidence = current_choice, 0.0
        if len(correlations) >= 2:
            ranked = sorted(correlations, key=correlations.get, reverse=True)
            best, runner_up = ranked[0], ranked[1]
            gap = correlations[best] - correlations[runner_up]
            if correlations[best] >= min_correlation and gap >= margin:
                if best != current_choice and (window_start - last_switch_t) < min_hold_s:
                    pass  # changement trop rapproche : on garde le cadrage en place
                else:
                    if best != current_choice:
                        last_switch_t = window_start
                    choice = best
                    confidence = min(1.0, correlations[best])

        decisions.append(SpeakerDecision(
            t_start=window_start, t_end=window_end, face_index=choice, confidence=confidence
        ))
        current_choice = choice
        window_start = window_end

    return decisions


def speaker_at(decisions: list[SpeakerDecision], t: float) -> int | None:
    for d in decisions:
        if d.t_start <= t < d.t_end:
            return d.face_index
    return None
