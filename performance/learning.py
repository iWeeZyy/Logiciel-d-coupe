"""Proposition d'ajustement des ponderations (sections 16 et 26).

Ce module ne modifie JAMAIS un reglage. Il propose, explique, et attend. C'est
la contrainte explicite de la section 16 : l'utilisateur doit pouvoir voir la
recommandation, l'accepter, la refuser, et revenir en arriere.

Conservateur par construction (section 26), sur quatre plans :

1. SEUIL DE DONNEES. Rien n'est propose sous le premier palier de fiabilite.
2. AMPLITUDE BORNEE. Un ajustement ne peut deplacer un poids que d'une fraction
   par proposition. Quarante clips ne doivent pas retourner le moteur.
3. ECART MINIMUM. Une difference de correlation trop faible ne declenche rien :
   sans cela, le bruit produirait une proposition a chaque nouvelle saisie.
4. HISTORIQUE. Chaque application est datee et conserve les poids precedents,
   ce qui rend le retour arriere possible a tout moment.

Ce n'est pas un modele d'apprentissage automatique, et le module ne pretend pas
en etre un : c'est un ajustement de ponderations guide par des correlations
observees. La structure est faite pour qu'un vrai modele puisse s'y substituer
plus tard sans toucher au reste (section 21).

Module pur : la persistance est l'affaire de performance/store.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from performance.analyzer import LEVEL_NONE, analyse

# Poids de depart, identiques a ceux de config/editing.json (clip_scores).
BASE_WEIGHTS = {"hook": 0.50, "rewatch": 0.30, "content": 0.20}

# Deplacement maximal d'un poids par proposition, en points de pourcentage.
MAX_SHIFT = 0.08
# Aucun poids ne descend en dessous : un signal ne doit jamais disparaitre
# completement du calcul sur la foi de quelques dizaines de clips.
MIN_WEIGHT = 0.10
# Ecart de correlation en dessous duquel on ne propose rien.
MIN_CORRELATION_GAP = 0.15

_FACTOR_TO_WEIGHT = {
    "Hook Score": "hook",
    "Rewatch Score": "rewatch",
    "Content Score": "content",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class WeightProposal:
    """Une proposition d'ajustement, avec de quoi la juger."""

    current: dict
    proposed: dict
    reason: str
    sample_size: int
    confidence: int

    def to_dict(self) -> dict:
        return asdict(self)

    def changes(self) -> list[tuple[str, float, float]]:
        return [(name, self.current.get(name, 0.0), self.proposed[name])
                for name in self.proposed
                if abs(self.proposed[name] - self.current.get(name, 0.0)) >= 0.005]


def current_weights(profile: dict) -> dict:
    """Ponderations en vigueur : celles du profil, ou celles de depart."""
    stored = (profile or {}).get("weights")
    if isinstance(stored, dict) and stored:
        return {k: float(v) for k, v in stored.items()}
    return dict(BASE_WEIGHTS)


def _normalise(weights: dict) -> dict:
    total = sum(max(0.0, v) for v in weights.values())
    if total <= 0:
        return dict(BASE_WEIGHTS)
    return {k: round(max(0.0, v) / total, 3) for k, v in weights.items()}


def propose(records, profile: dict | None = None, thresholds: dict | None = None) -> WeightProposal | None:
    """Proposition d'ajustement, ou None s'il n'y a rien d'assez net a dire.

    None est un resultat normal et frequent : ne rien proposer vaut mieux que
    proposer du bruit.
    """
    summary = analyse(records, thresholds)
    if summary.level == LEVEL_NONE or not summary.correlations:
        return None

    by_weight = {
        _FACTOR_TO_WEIGHT[c.factor]: c.correlation
        for c in summary.correlations
        if c.factor in _FACTOR_TO_WEIGHT
    }
    if len(by_weight) < 2:
        return None

    strongest = max(by_weight, key=lambda k: by_weight[k])
    weakest = min(by_weight, key=lambda k: by_weight[k])
    gap = by_weight[strongest] - by_weight[weakest]
    if strongest == weakest or gap < MIN_CORRELATION_GAP:
        return None

    current = current_weights(profile)

    # Deplacement proportionnel a l'ecart constate, mais borne : plus la
    # correlation est nette, plus on bouge, sans jamais depasser MAX_SHIFT.
    shift = min(MAX_SHIFT, MAX_SHIFT * min(1.0, gap / 0.4))
    proposed = dict(current)
    take = min(shift, max(0.0, current.get(weakest, 0.0) - MIN_WEIGHT))
    if take <= 0.005:
        return None
    proposed[weakest] = round(current[weakest] - take, 3)
    proposed[strongest] = round(current.get(strongest, 0.0) + take, 3)
    proposed = _normalise(proposed)

    names = {"hook": "Hook Score", "rewatch": "Rewatch Score", "content": "Content Score"}
    reason = (
        f"Les données de vos {summary.sample_size} derniers clips suggèrent que le "
        f"{names[strongest]} est davantage corrélé à vos performances que le "
        f"{names[weakest]}."
    )
    return WeightProposal(
        current=current, proposed=proposed, reason=reason,
        sample_size=summary.sample_size, confidence=summary.confidence,
    )


def apply_proposal(profile: dict, proposal: WeightProposal) -> dict:
    """Profil mis a jour, avec l'ancien etat conserve pour le retour arriere.

    L'historique est plafonne : on garde de quoi revenir en arriere plusieurs
    fois, pas un journal infini.
    """
    profile = dict(profile or {})
    history = list(profile.get("history", []))
    history.append({
        "applied_at": _now(),
        "previous_weights": current_weights(profile),
        "new_weights": dict(proposal.proposed),
        "reason": proposal.reason,
        "sample_size": proposal.sample_size,
    })
    profile["weights"] = dict(proposal.proposed)
    profile["history"] = history[-20:]
    profile["updated_at"] = _now()
    return profile


def can_revert(profile: dict) -> bool:
    return bool((profile or {}).get("history"))


def revert(profile: dict) -> dict:
    """Retour a l'etat precedent. Sans historique, le profil est inchange."""
    profile = dict(profile or {})
    history = list(profile.get("history", []))
    if not history:
        return profile
    last = history.pop()
    profile["weights"] = dict(last["previous_weights"])
    profile["history"] = history
    profile["updated_at"] = _now()
    return profile


@dataclass
class RejectedProposals:
    """Propositions refusees, pour ne pas les represser a chaque ouverture."""

    signatures: list = field(default_factory=list)

    @staticmethod
    def signature(proposal: WeightProposal) -> str:
        return "|".join(f"{k}:{v}" for k, v in sorted(proposal.proposed.items()))


def is_rejected(profile: dict, proposal: WeightProposal) -> bool:
    return RejectedProposals.signature(proposal) in (profile or {}).get("rejected", [])


def reject(profile: dict, proposal: WeightProposal) -> dict:
    """Refuser une proposition la met de cote SANS rien changer aux poids.

    Elle ne sera pas representee tant que les donnees n'auront pas change au
    point d'en produire une differente.
    """
    profile = dict(profile or {})
    rejected = list(profile.get("rejected", []))
    signature = RejectedProposals.signature(proposal)
    if signature not in rejected:
        rejected.append(signature)
    profile["rejected"] = rejected[-20:]
    return profile
