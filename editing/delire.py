"""Montage delire : des effets francs, poses aux bons endroits et comptes.

MODULE PUR. Il produit une LISTE D'EVENEMENTS (quel effet, de quand a quand,
avec quelle force) ; leur traduction en filtre ffmpeg vit dans
video/delire_filters.py. Meme partage que editing/zoom.py et
video/filter_graph.py.

LE PROBLEME QU'IL RESOUT VRAIMENT n'est pas « comment faire un glitch » -- une
ligne de ffmpeg suffit -- mais « ou, combien, et pas plus ». Un effet toutes
les deux secondes donne une video que personne ne regarde jusqu'au bout. D'ou
trois bornes, comme pour les zooms : un nombre d'evenements par minute, un
ecart minimal entre deux, et une part maximale du clip sous effet.

LES INSTANTS NE SONT PAS TIRES AU HASARD. Ce sont ceux que editing/captions.py
a deja retenus comme marquants (mots-cles, chiffres prononces, montee du
niveau audio) -- exactement la source qu'utilise editing/zoom.py. Aucun moment
marquant, aucun effet : mieux vaut un clip sobre qu'un clip decore au hasard.

CE QUE CE MODULE NE FAIT PAS, et pourquoi c'est un choix :

- Il ne touche PAS a la duree. Pas de ralenti, pas d'arret sur image, pas de
  retour arriere. Ces effets deplacent tout ce qui suit, donc desynchronisent
  les sous-titres, deja calcules en temps absolu, et l'audio. Les ajouter
  demande une carte de correspondance temps source vers temps sortie appliquee
  aux trois a la fois ; c'est une autre etape, pas un oubli.
- Il ne pose PAS de texte. Le texte lisible de cette application passe par un
  fichier ASS (sous-titres), qui gere les polices correctement sur Windows ;
  `drawtext` de ffmpeg exige un chemin de police en dur. Les punchlines a
  l'ecran viendront par la meme voie que les sous-titres.
- Il ne SECOUE pas l'image. Une secousse demande d'agrandir l'image de quelques
  pour cent sur TOUT le clip pour avoir de la marge, donc de l'adoucir partout
  pour trois dixiemes de seconde d'effet. Le prix n'en vaut pas la peine.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# Les effets disponibles. Tous sont activables sur un INTERVALLE par ffmpeg
# (option `enable`), ce qui est verifie : c'est ce qui permet de les poser
# ponctuellement sans changer le reste du clip.
GLITCH = "glitch"          # separation des canaux rouge/vert/bleu
DEEPFRY = "deepfry"        # saturation et contraste pousses a l'extreme
VHS = "vhs"                # grain, decalage de chrominance, legere douceur
PIXEL = "pixel"            # pixelisation
FLASH = "flash"            # eclair blanc
BLIP = "blip"              # inversion des couleurs, tres bref

EFFECTS = (GLITCH, DEEPFRY, VHS, PIXEL, FLASH, BLIP)

# Trois crans, du plus sage au plus charge. Ce ne sont pas trois forces du meme
# effet : chaque cran ouvre aussi de nouveaux effets. « Doux » se contente de
# ce qui reste regardable en boucle ; « maximum » assume l'illisible ponctuel.
LEVELS = ("doux", "moyen", "maximum")
DEFAULT_LEVEL = "moyen"

_LEVEL_RULES = {
    "doux": {
        "kinds": (GLITCH, VHS),
        "events_per_minute": 4.0,
        "min_gap_s": 5.0,
        "max_ratio": 0.10,
        "duration_s": (0.16, 0.30),
        "strength": 0.45,
    },
    "moyen": {
        "kinds": (GLITCH, DEEPFRY, VHS, FLASH),
        "events_per_minute": 8.0,
        "min_gap_s": 2.5,
        "max_ratio": 0.18,
        "duration_s": (0.14, 0.40),
        "strength": 0.70,
    },
    "maximum": {
        "kinds": EFFECTS,
        "events_per_minute": 14.0,
        "min_gap_s": 1.4,
        "max_ratio": 0.28,
        "duration_s": (0.10, 0.50),
        "strength": 1.00,
    },
}

# Un eclair ou une inversion tiennent quelques images, pas une demi-seconde :
# au-dela ils cessent d'etre une ponctuation et deviennent une panne.
_SHORT_EFFECTS = {FLASH: 0.12, BLIP: 0.10}


@dataclass(frozen=True)
class Event:
    """Un effet, de `start` a `end`, en secondes de SORTIE."""

    kind: str
    start: float
    end: float
    strength: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Plan:
    events: tuple = ()
    level: str = DEFAULT_LEVEL
    reasons: tuple = ()

    @property
    def is_empty(self) -> bool:
        return not self.events

    @property
    def covered_s(self) -> float:
        return sum(event.duration for event in self.events)

    def kinds_used(self) -> tuple:
        return tuple(sorted({event.kind for event in self.events}))

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "events": [{"kind": e.kind, "start": round(e.start, 3),
                        "end": round(e.end, 3), "strength": round(e.strength, 3)}
                       for e in self.events],
            "kinds": list(self.kinds_used()),
            "covered_s": round(self.covered_s, 3),
            "reasons": list(self.reasons),
        }


def rules_for(level: str) -> dict:
    return dict(_LEVEL_RULES.get(level, _LEVEL_RULES[DEFAULT_LEVEL]))


def build_plan(
    highlight_times: list,
    clip_start: float,
    clip_end: float,
    *,
    level: str = DEFAULT_LEVEL,
    seed: int | None = None,
    overrides: dict | None = None,
) -> Plan:
    """Les effets a poser, dans l'ordre, sans chevauchement.

    `highlight_times` est en temps SOURCE (les instants marquants du clip) ;
    les evenements rendus sont en temps de SORTIE, c'est-a-dire relatifs au
    debut du clip -- c'est ce qu'attend l'option `enable` de ffmpeg.

    `seed` rend le plan REPRODUCTIBLE : sans lui, deux rendus du meme clip
    n'auraient pas les memes effets, et comparer deux reglages deviendrait
    impossible. Par defaut il derive du debut du clip.
    """
    duration = max(0.0, float(clip_end) - float(clip_start))
    if duration <= 0:
        return Plan(level=level, reasons=("clip vide",))

    rules = rules_for(level)
    rules.update(overrides or {})
    kinds = tuple(rules.get("kinds") or ())
    if not kinds:
        return Plan(level=level, reasons=("aucun effet autorisé",))

    # Les instants, ramenes au debut du clip et gardes dans ses bornes. On
    # ecarte les toutes premieres et dernieres fractions de seconde : un effet
    # a l'image 1 ou sur la derniere passe pour un defaut d'encodage.
    marge = 0.25
    instants = sorted({round(float(t) - float(clip_start), 3)
                       for t in (highlight_times or [])
                       if marge <= float(t) - float(clip_start) <= duration - marge})
    if not instants:
        return Plan(level=level, reasons=("aucun moment marquant",))

    budget = max(1, int(round(float(rules["events_per_minute"]) * duration / 60.0)))
    min_gap = float(rules["min_gap_s"])
    plancher, plafond = (float(v) for v in rules["duration_s"])
    force = float(rules["strength"])
    max_couvert = float(rules["max_ratio"]) * duration

    alea = random.Random(seed if seed is not None
                         else int(round(float(clip_start) * 1000)) or 1)

    events = []
    couvert = 0.0
    dernier_fin = -1e9
    for instant in instants:
        if len(events) >= budget:
            break
        if instant - dernier_fin < min_gap:
            continue

        kind = kinds[alea.randrange(len(kinds))]
        longueur = _SHORT_EFFECTS.get(kind) or alea.uniform(plancher, plafond)
        # Un effet centre sur le mot marquant, jamais apres : l'oeil doit etre
        # deja dessus quand le mot tombe.
        start = max(0.0, instant - longueur * 0.35)
        end = min(duration, start + longueur)
        if end - start <= 0.02:
            continue
        if couvert + (end - start) > max_couvert:
            break

        events.append(Event(kind=kind, start=round(start, 3), end=round(end, 3),
                            strength=force))
        couvert += end - start
        dernier_fin = end

    if not events:
        return Plan(level=level, reasons=("moments trop rapprochés",))

    reasons = (f"{len(events)} effet(s) sur {len(instants)} moment(s) marquant(s)",
               f"{couvert:.2f} s sous effet sur {duration:.1f} s",
               "durée inchangée : sous-titres et audio restent synchrones")
    return Plan(events=tuple(events), level=level, reasons=reasons)
