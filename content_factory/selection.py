"""Selection diversifiee des clips (sections 3, 4 et 5).

La selection historique (analysis/selector.py) classe par potentiel viral et
descend la liste en ecartant les chevauchements. C'est le bon comportement pour
sortir UN clip ; pour dix, elle donne souvent dix variantes du meme moment fort.

Ce module ne remplace pas cette selection : il fournit un CLASSEUR que
select_clips accepte en option. La detection des chevauchements et l'application
du contexte restent la ou elles etaient -- une seule implementation, pas deux.

Principe : a chaque tour, on retient le candidat dont la priorite, diminuee de
sa ressemblance avec ce qui est deja pris, est la plus forte. C'est une
selection gloutonne avec penalite de redondance ; elle ne cherche pas
l'optimum global (probleme combinatoire) mais donne un resultat stable et
explicable, ce qui compte davantage ici.

Module pur.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from content_factory import diversity
from content_factory.priority import PriorityBreakdown, compute_priority
from core.models import ScoredCandidate
from editing.sentences import SentenceSpan

# Paliers de l'entonnoir affiche a l'utilisateur (section 3). Ce sont des
# seuils de LISIBILITE : ils ne filtrent rien, ils racontent ce que le moteur a
# traverse. Un candidat sous le premier seuil reste selectionnable si rien de
# mieux n'existe -- refuser de produire un clip parce qu'un palier d'affichage
# n'est pas atteint serait absurde.
DEFAULT_FUNNEL_THRESHOLDS = {"interesting": 55.0, "strong": 70.0, "best": 80.0}

# Force de la penalite de redondance, 0 (classement par priorite pure) a 1 (un
# candidat parfaitement redondant est ramene a zero).
DEFAULT_DIVERSITY_STRENGTH = 0.55
DEFAULT_TOPIC_WEIGHT = 0.5
DEFAULT_TEMPORAL_WEIGHT = 0.5


@dataclass(frozen=True)
class SelectionFunnel:
    """Ce que le moteur a traverse, du balayage complet aux clips retenus."""

    candidates: int = 0
    interesting: int = 0
    strong: int = 0
    best: int = 0
    final: int = 0

    def to_dict(self) -> dict:
        return {
            "candidats": self.candidates,
            "interessants": self.interesting,
            "fort_potentiel": self.strong,
            "meilleurs": self.best,
            "clips": self.final,
        }

    def lines(self) -> list[str]:
        """Rendu texte, identique en CLI et en interface.

        Les paliers intermediaires vides sont omis : "25 candidats -> 0
        interessants -> 0 a fort potentiel -> 4 clips finaux" est exact mais
        illisible, et laisse croire a une incoherence. Le balayage et le
        resultat, eux, sont toujours affiches -- ce sont les deux seuls chiffres
        qui ne dependent pas d'un seuil d'affichage.
        """
        out = [f"{self.candidates} moments candidats detectes"]
        for count, label in (
            (self.interesting, "moments interessants"),
            (self.strong, "moments a fort potentiel"),
            (self.best, "meilleurs candidats"),
        ):
            if count:
                out.append(f"{count} {label}")
        out.append(f"{self.final} clips finaux")
        return out


@dataclass
class DiverseRanker:
    """Classeur passe a analysis.selector.select_clips.

    Il ne connait ni les chevauchements ni le contexte : select_clips lui fournit
    un predicat de compatibilite et se charge du reste.
    """

    sentences: list[SentenceSpan]
    audio_stats: dict
    video_duration: float
    nb_clips: int
    weights: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    diversity_strength: float = DEFAULT_DIVERSITY_STRENGTH
    topic_weight: float = DEFAULT_TOPIC_WEIGHT
    temporal_weight: float = DEFAULT_TEMPORAL_WEIGHT
    horizon_s: float | None = None
    funnel_thresholds: dict = field(default_factory=dict)

    # Rempli pendant pick(), lu ensuite par le pipeline pour l'affichage et
    # pour results.json.
    funnel: SelectionFunnel = field(default_factory=SelectionFunnel)
    priorities: dict[int, PriorityBreakdown] = field(default_factory=dict)

    def _horizon(self) -> float:
        if self.horizon_s is not None:
            return float(self.horizon_s)
        return diversity.default_horizon(self.video_duration, self.nb_clips)

    def pick(self, scored_candidates, nb_clips, is_compatible) -> list[ScoredCandidate]:
        """`is_compatible(candidate, selected_scored)` -> bool, fourni par
        select_clips (regle de chevauchement --min-gap, inchangee)."""
        thresholds = dict(DEFAULT_FUNNEL_THRESHOLDS)
        thresholds.update(self.funnel_thresholds or {})

        entries = []
        for sc in scored_candidates:
            breakdown = compute_priority(
                sc, self.sentences, self.audio_stats, self.weights, self.params
            )
            self.priorities[id(sc)] = breakdown
            entries.append((sc, breakdown, diversity.topic_signature(sc.candidate.text)))

        totals = [b.total for _, b, _ in entries]
        self.funnel = SelectionFunnel(
            candidates=len(entries),
            interesting=sum(1 for t in totals if t >= thresholds["interesting"]),
            strong=sum(1 for t in totals if t >= thresholds["strong"]),
            best=sum(1 for t in totals if t >= thresholds["best"]),
            final=0,
        )

        horizon = self._horizon()
        strength = max(0.0, min(1.0, self.diversity_strength))
        selected: list[ScoredCandidate] = []
        taken: list[tuple[object, frozenset]] = []
        remaining = list(entries)

        while remaining and len(selected) < nb_clips:
            best_entry, best_value = None, None
            for entry in remaining:
                sc, breakdown, signature = entry
                if not is_compatible(sc.candidate, selected):
                    continue
                penalty = diversity.redundancy(
                    sc.candidate, signature, taken, horizon,
                    self.topic_weight, self.temporal_weight,
                )
                value = breakdown.total * (1.0 - strength * penalty)
                # Departage stable : a valeur egale, le meilleur potentiel viral
                # puis le passage le plus tot dans la video. Sans cela, l'ordre
                # dependrait de l'ordre d'iteration et deux analyses de la meme
                # video pourraient differer.
                key = (value, sc.scores.viral, -sc.candidate.start)
                if best_value is None or key > best_value:
                    best_entry, best_value = entry, key
            if best_entry is None:
                break
            sc, _, signature = best_entry
            selected.append(sc)
            taken.append((sc.candidate, signature))
            remaining.remove(best_entry)

        self.funnel = SelectionFunnel(
            candidates=self.funnel.candidates,
            interesting=self.funnel.interesting,
            strong=self.funnel.strong,
            best=self.funnel.best,
            final=len(selected),
        )
        return selected

    def priority_of(self, scored: ScoredCandidate) -> PriorityBreakdown | None:
        return self.priorities.get(id(scored))
