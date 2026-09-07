"""Zooms dynamiques discrets sur les moments forts (fonctionnalite 3).

Un zoom leger sur une punchline attire l'oeil ; le meme effet repete toutes les
trois secondes donne le mal de mer. Ce module construit donc une enveloppe de
zoom bornee sur tous les axes : amplitude maximale, nombre d'evenements par
clip, ecart minimal entre deux, et duree fixe (montee / maintien / descente).

Les instants candidats ne sont pas devines : ce sont ceux des mots deja
identifies comme importants par editing/captions.py (mots-cles configures,
chiffres prononces, montee du niveau audio). Aucun mot marquant -> aucun zoom.

Module pur : il produit des points (temps, facteur de zoom) ; leur traduction en
filtre ffmpeg vit dans video/filter_graph.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoomKeyframe:
    t: float
    zoom: float


@dataclass(frozen=True)
class ZoomTrack:
    keyframes: tuple[ZoomKeyframe, ...] = ()
    events: int = 0
    reasons: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.keyframes

    def to_dict(self) -> dict:
        return {"events": self.events, "reasons": list(self.reasons)}


def build_zoom_track(
    highlight_times: list[float],
    clip_start: float,
    clip_end: float,
    *,
    max_zoom: float = 1.08,
    attack_s: float = 0.25,
    hold_s: float = 0.5,
    release_s: float = 0.4,
    min_gap_s: float = 4.0,
    max_events: int = 4,
) -> ZoomTrack:
    """Enveloppe de zoom en temps SOURCE.

    Les instants sont pris dans l'ordre chronologique et espaces d'au moins
    `min_gap_s` : on prefere quelques zooms bien repartis a une rafale sur le
    seul passage dense du clip.
    """
    if not highlight_times or max_zoom <= 1.0 or max_events <= 0:
        return ZoomTrack(reasons=("aucun moment fort identifie",))

    duration = attack_s + hold_s + release_s
    chosen: list[float] = []
    for t in sorted(highlight_times):
        if t < clip_start or t + duration > clip_end:
            continue
        if chosen and (t - chosen[-1]) < min_gap_s:
            continue
        chosen.append(t)
        if len(chosen) >= max_events:
            break

    if not chosen:
        return ZoomTrack(reasons=("moments forts trop rapproches ou trop pres des bords",))

    keyframes: list[ZoomKeyframe] = [ZoomKeyframe(clip_start, 1.0)]
    for t in chosen:
        keyframes.append(ZoomKeyframe(t, 1.0))
        keyframes.append(ZoomKeyframe(t + attack_s, max_zoom))
        keyframes.append(ZoomKeyframe(t + attack_s + hold_s, max_zoom))
        keyframes.append(ZoomKeyframe(t + duration, 1.0))
    keyframes.append(ZoomKeyframe(clip_end, 1.0))

    # Les bornes ajoutees peuvent doublonner avec un evenement colle au debut ou
    # a la fin : on garde un seul point par instant, dans l'ordre.
    unique: list[ZoomKeyframe] = []
    for kf in sorted(keyframes, key=lambda k: k.t):
        if unique and abs(kf.t - unique[-1].t) < 1e-6:
            unique[-1] = kf
        else:
            unique.append(kf)

    return ZoomTrack(
        keyframes=tuple(unique),
        events=len(chosen),
        reasons=(f"{len(chosen)} zoom(s) leger(s) sur les moments forts",),
    )
