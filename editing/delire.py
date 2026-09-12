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

NI LES INSTANTS NI LES EFFETS NE SONT TIRES AU HASARD.

Les instants sont ceux que editing/captions.py a deja retenus comme marquants
(mots-cles configures, chiffres prononces, montee du niveau audio) -- exactement
la source qu'utilise editing/zoom.py. Aucun moment marquant, aucun effet :
mieux vaut un clip sobre qu'un clip decore au hasard.

L'EFFET, LUI, DECOULE DE CE QUI EST DIT OU FAIT. Un mot de colere ne merite pas
le meme traitement qu'un rire ou qu'un chiffre. Une famille d'effets est donc
associee a chaque SIGNAL (voir `cues`), et le meme signal donne toujours la
meme famille : c'est ce qui rend le montage lisible plutot que decoratif.

COMMENT DEUX CLIPS EVITENT DE SE RESSEMBLER MALGRE CA. Le sens choisit la
FAMILLE, la graine du clip choisit LE MEMBRE de cette famille. Deux clips qui
contiennent tous les deux un eclat de rire ne recevront donc pas forcement le
meme effet, alors qu'un meme clip rejoue donnera exactement le meme montage.
Un effet n'est jamais repete deux fois de suite quand sa famille en propose
plusieurs.

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


# Les SIGNAUX reconnus, par ordre de PRIORITE. Ce qui est DIT passe avant ce
# qui est entendu : un juron crie est d'abord un juron. Le dernier, "defaut",
# n'est pas un signal mais le repli quand rien n'a ete reconnu -- il existe
# pour qu'un moment marquant sans cause identifiee recoive quand meme un effet
# plutot que d'etre ignore.
CUE_ORDER = ("colere", "rire", "surprise", "question", "crie", "chiffre",
             "motcle", "defaut")

# Le lexique et les familles d'effets par defaut. Ils sont surchargeables par
# config/editing.json (bloc `delire.cues`) : ce sont des propositions de
# depart, pas des verites -- personne n'a mesure que le rire appelle la
# saturation plutot que la pixelisation.
DEFAULT_CUES = {
    "colere": {
        "words": ["putain", "merde", "fdp", "connard", "enculé", "bordel",
                  "salope", "batard", "ta gueule", "wesh", "nique"],
        "effects": [GLITCH, DEEPFRY],
    },
    "rire": {
        "words": ["mdr", "ptdr", "lol", "haha", "hahaha", "ahah", "ahaha",
                  "xptdr", "jpp", "mort", "je meurs"],
        "effects": [DEEPFRY, PIXEL],
    },
    "surprise": {
        "words": ["quoi", "hein", "sérieux", "serieux", "what", "wtf", "nan",
                  "impossible", "incroyable", "attends", "comment"],
        "effects": [BLIP, FLASH],
    },
    # Sans lexique : ces trois signaux viennent de la MESURE, pas des mots.
    "question": {"effects": [GLITCH, PIXEL]},
    "crie": {"effects": [FLASH, BLIP]},
    "chiffre": {"effects": [PIXEL, GLITCH]},
    "motcle": {"effects": [GLITCH, VHS]},
    "defaut": {"effects": [GLITCH, VHS, DEEPFRY]},
}


@dataclass(frozen=True)
class Moment:
    """Un instant marquant, AVEC ce qui le rend marquant.

    Les quatre drapeaux viennent de mesures deja faites ailleurs : `keyword` et
    `digit` de editing/captions.py, `loud` et `very_loud` du niveau audio
    compare au seuil du clip, `question` de editing/sentences.py. Ce module
    n'analyse donc ni le son ni le texte lui-meme -- il ne fait que lire des
    signaux.
    """

    t: float
    text: str = ""
    keyword: bool = False
    digit: bool = False
    loud: bool = False
    very_loud: bool = False
    question: bool = False


def _normalize(value: str) -> str:
    """Minuscules, sans accents ni ponctuation de bord : « Sérieux ?! » et
    « serieux » doivent tomber sur la meme entree du lexique."""
    import unicodedata

    cleaned = unicodedata.normalize("NFD", str(value or "").lower())
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip(" \t\n.,;:!?…\"'()[]«»-–—")


def classify(moment, cues: dict | None = None) -> str:
    """Le signal qui explique ce moment, ou "defaut".

    L'ORDRE COMPTE et il est explicite : ce qui est dit avant ce qui est
    entendu. Un « putain ! » hurle est classe en colere, pas en cri -- sinon
    le lexique ne servirait jamais, la montee de volume accompagnant presque
    toujours un mot fort.
    """
    cues = cues or DEFAULT_CUES
    mot = _normalize(getattr(moment, "text", ""))

    for name in CUE_ORDER:
        if name == "defaut":
            break
        entry = cues.get(name) or {}
        lexique = entry.get("words")
        if lexique:
            if mot and any(mot == _normalize(w) for w in lexique):
                return name
            continue
        # Signaux mesures, sans lexique.
        if name == "question" and getattr(moment, "question", False):
            return name
        if name == "crie" and getattr(moment, "very_loud", False):
            return name
        if name == "chiffre" and getattr(moment, "digit", False):
            return name
        if name == "motcle" and getattr(moment, "keyword", False):
            return name
    return "defaut"


def _as_moments(items, clip_start: float) -> list:
    """Accepte des Moment ou de simples instants.

    Le mode degrade (une liste de nombres) reste possible : il donne alors des
    effets de la famille « defaut ». Il existe pour que les appelants anciens
    et les tests n'aient pas a fabriquer un Moment pour verifier une borne.
    """
    out = []
    for item in items or []:
        if isinstance(item, Moment):
            out.append(item)
        else:
            try:
                out.append(Moment(t=float(item)))
            except (TypeError, ValueError):
                continue
    return out


@dataclass(frozen=True)
class Event:
    """Un effet, de `start` a `end`, en secondes de SORTIE."""

    kind: str
    start: float
    end: float
    strength: float = 1.0
    # Le signal qui a decide de cet effet. Garde pour qu'on puisse relire le
    # plan et comprendre POURQUOI chaque effet est la -- un montage qu'on ne
    # sait pas expliquer ne se corrige pas.
    cue: str = "defaut"
    word: str = ""

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

    def cues_used(self) -> tuple:
        return tuple(sorted({event.cue for event in self.events}))

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "events": [{"kind": e.kind, "start": round(e.start, 3),
                        "end": round(e.end, 3), "strength": round(e.strength, 3),
                        "cue": e.cue, "word": e.word}
                       for e in self.events],
            "kinds": list(self.kinds_used()),
            "cues": list(self.cues_used()),
            "covered_s": round(self.covered_s, 3),
            "reasons": list(self.reasons),
        }


def rules_for(level: str) -> dict:
    return dict(_LEVEL_RULES.get(level, _LEVEL_RULES[DEFAULT_LEVEL]))


def _pick_kind(cue: str, allowed: tuple, cues: dict, alea, precedent: str) -> str:
    """L'effet d'un signal : la FAMILLE vient du sens, le MEMBRE de la graine.

    Deux filtres avant de choisir :

    1. LE CRAN D'INTENSITE a le dernier mot. Si le sens appelle un eclair mais
       que le cran « doux » ne l'autorise pas, on reste dans ce que le cran
       permet -- sinon le cran ne voudrait plus rien dire. Quand la famille et
       le cran n'ont aucun effet en commun, on prend celui du cran : mieux vaut
       un effet moins juste qu'un effet interdit.
    2. PAS DEUX FOIS LE MEME DE SUITE quand la famille en propose plusieurs :
       c'est ce qui evite qu'un clip entier de rires soit une seule texture.
    """
    famille = tuple((cues.get(cue) or {}).get("effects") or ())
    candidats = tuple(k for k in famille if k in allowed) or tuple(allowed)
    if not candidats:
        return ""
    if len(candidats) > 1 and precedent in candidats:
        restants = tuple(k for k in candidats if k != precedent)
        if restants:
            candidats = restants
    return candidats[alea.randrange(len(candidats))]


def build_plan(
    highlight_times: list,
    clip_start: float,
    clip_end: float,
    *,
    level: str = DEFAULT_LEVEL,
    seed: int | None = None,
    overrides: dict | None = None,
    cues: dict | None = None,
) -> Plan:
    """Les effets a poser, dans l'ordre, sans chevauchement.

    `highlight_times` est en temps SOURCE (les instants marquants du clip) ;
    les evenements rendus sont en temps de SORTIE, c'est-a-dire relatifs au
    debut du clip -- c'est ce qu'attend l'option `enable` de ffmpeg.

    `highlight_times` accepte des `Moment` (avec leur signal) ou de simples
    nombres. Dans le second cas tous les effets viennent de la famille
    « defaut » : le montage reste borne et reproductible, mais il ne suit plus
    ce qui est dit.

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

    table = cues or DEFAULT_CUES

    # Les moments, ramenes au debut du clip et gardes dans ses bornes. On
    # ecarte les toutes premieres et dernieres fractions de seconde : un effet
    # a l'image 1 ou sur la derniere passe pour un defaut d'encodage.
    marge = 0.25
    moments = []
    for moment in _as_moments(highlight_times, clip_start):
        relatif = round(moment.t - float(clip_start), 3)
        if marge <= relatif <= duration - marge:
            moments.append((relatif, moment))
    moments.sort(key=lambda pair: pair[0])
    if not moments:
        return Plan(level=level, reasons=("aucun moment marquant",))
    instants = [relatif for relatif, _ in moments]

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
    precedent = ""
    compte_par_signal: dict = {}
    for instant, moment in moments:
        if len(events) >= budget:
            break
        if instant - dernier_fin < min_gap:
            continue

        cue = classify(moment, table)
        kind = _pick_kind(cue, kinds, table, alea, precedent)
        if not kind:
            continue
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
                            strength=force, cue=cue,
                            word=str(getattr(moment, "text", "") or "")))
        couvert += end - start
        dernier_fin = end
        precedent = kind
        compte_par_signal[cue] = compte_par_signal.get(cue, 0) + 1

    if not events:
        return Plan(level=level, reasons=("moments trop rapprochés",))

    detail = ", ".join(f"{nom} x{nombre}" for nom, nombre
                       in sorted(compte_par_signal.items()))
    reasons = (f"{len(events)} effet(s) sur {len(instants)} moment(s) marquant(s)",
               f"signaux : {detail}",
               f"{couvert:.2f} s sous effet sur {duration:.1f} s",
               "durée inchangée : sous-titres et audio restent synchrones")
    return Plan(events=tuple(events), level=level, reasons=reasons)
