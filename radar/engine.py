"""Orchestration d'un scan (sections 6, 22 ; 4 du Radar Twitch).

Le moteur ne connait aucune API : il enchaine des adaptateurs, calcule les
scores localement et enregistre. Ajouter une plateforme n'implique aucune
modification ici.

Trois exigences de la section 22, tenues par construction :

- ANNULABLE : le jeton d'annulation deja utilise par le pipeline video
  (core/cancellation.py) est verifie entre chaque createur. Pas de deuxieme
  mecanisme d'annulation dans le projet.
- NON BLOQUANT : le moteur est un generateur d'evenements de progression ;
  c'est l'appelant (un QThread cote interface) qui decide du fil d'execution.
  Le moteur ne connait pas Qt.
- ROBUSTE : l'echec d'un createur n'interrompt pas le scan. Une chaine
  supprimee, une erreur reseau ou un quota epuise sont collectes et rapportes a
  la fin, les autres createurs sont scannes normalement.

Un releve est enregistre a CHAQUE scan, meme si le contenu etait deja connu :
c'est ce qui rend la progression mesurable au scan suivant.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from core.logging_setup import get_logger
from radar.models import PRIORITY_NORMAL, Opportunity, ScanResult, utc_now_iso
from radar.scoring import compute_radar_score
from radar.trends import compute_trend
from utils.errors import ClipFarmingError

logger = get_logger()

# Periodes proposees (section 6). En heures.
PERIODS = {
    "6h": 6, "12h": 12, "24h": 24, "3j": 72, "7j": 168,
}
DEFAULT_PERIOD = "24h"


def since_iso(period: str = DEFAULT_PERIOD, reference=None) -> str:
    hours = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
    now = reference or datetime.now(timezone.utc)
    return (now - timedelta(hours=hours)).isoformat()


@dataclass
class ScanProgress:
    """Avancement d'un scan, consomme tel quel par la barre de progression."""

    creator_index: int = 0
    creator_total: int = 0
    creator_label: str = ""
    opportunities: int = 0

    @property
    def fraction(self) -> float:
        return self.creator_index / self.creator_total if self.creator_total else 0.0

    def label(self) -> str:
        return (f"Analyse de {self.creator_total} créateur(s)… "
                f"{self.opportunities} contenu(s) analysé(s)")


class RadarEngine:
    """Enchaine les adaptateurs, score et enregistre."""

    def __init__(self, store, adapters: dict, weights: dict | None = None,
                 params: dict | None = None):
        self.store = store
        self.adapters = adapters          # {plateforme: adaptateur}
        self.weights = weights or {}
        self.params = params or {}

    def available_platforms(self) -> dict:
        """Etat de chaque plateforme, pour que l'interface puisse desactiver un
        onglet avec une explication au lieu d'un scan qui echoue."""
        return {name: adapter.status() for name, adapter in self.adapters.items()}

    def scan(self, platforms=None, period: str = DEFAULT_PERIOD, cancel_token=None,
             on_progress=None, reference=None, max_results: int = 50) -> ScanResult:
        """Scanne les createurs actifs des plateformes demandees."""
        wanted = tuple(platforms or self.adapters.keys())
        result = ScanResult(platforms=wanted)
        threshold = since_iso(period, reference)

        # Menage avant de scanner : ce qu'on ne cherche plus n'a pas a rester en
        # base. Un direct enregistre hier ne redeviendra jamais exact.
        for platform in wanted:
            kinds = self.searched_kinds(platform)
            if kinds:
                removed = self.store.purge_kinds(platform, kinds)
                if removed:
                    logger.info(f"{removed} contenu(s) d'un type non cherche retire(s) "
                                f"de {platform}.")

        creators = [
            creator for creator in self.store.list_creators(active_only=True)
            if creator.platform in wanted and creator.platform in self.adapters
        ]
        progress = ScanProgress(creator_total=len(creators))

        for index, creator in enumerate(creators, start=1):
            if cancel_token is not None:
                cancel_token.check()
            progress.creator_index = index
            progress.creator_label = creator.label
            if on_progress is not None:
                on_progress(progress)

            try:
                found = self.adapters[creator.platform].scan(creator, threshold, max_results)
            except ClipFarmingError as error:
                # L'echec d'un createur ne doit pas emporter tout le scan :
                # une chaine supprimee ou un quota atteint sont rapportes, les
                # autres createurs continuent.
                message = f"{creator.label} : {error}"
                logger.warning(f"Radar -- {message}")
                result.errors.append(message)
                continue

            for opportunity in found:
                self._record(opportunity, creator, reference)
                result.opportunities_found += 1
                progress.opportunities += 1

            creator.last_scan_at = utc_now_iso()
            self.store.upsert_creator(creator)
            result.creators_scanned += 1
            if on_progress is not None:
                on_progress(progress)

        result.finished_at = utc_now_iso()
        self.store.record_scan(result)
        return result

    def _record(self, opportunity: Opportunity, creator, reference=None) -> None:
        """Enregistre le releve, calcule score et tendance, puis stocke.

        L'ordre compte : le releve est ajoute AVANT le calcul de tendance, pour
        que la mesure porte sur l'etat le plus recent, et l'historique du
        createur est lu APRES pour que la comparaison relative inclue ce qui
        vient d'etre vu.
        """
        snapshot = opportunity.snapshot()
        if reference is not None:
            # Un scan doit etre coherent : le releve porte le MEME instant que
            # celui qui sert a noter et a dater. Sans cela, un scan pilote sur
            # une reference fixe (tests, rejeu) melange deux horloges et la
            # progression calculee depend de l'heure a laquelle il tourne.
            snapshot = replace(snapshot, captured_at=reference.isoformat(timespec="milliseconds"))
        self.store.add_snapshot(snapshot)

        history = [
            other for other in self.store.list_opportunities(creator_key=creator.key, limit=60)
            if other.content_id != opportunity.content_id
        ]
        score = compute_radar_score(
            opportunity, history, priority=creator.priority or PRIORITY_NORMAL,
            weights=self.weights, params=self.params, reference=reference,
        )
        trend = compute_trend(self.store.snapshots(opportunity.key))

        opportunity.radar_score = score.total
        opportunity.score_breakdown = score.to_dict()
        opportunity.trend = trend.to_dict()
        self.store.upsert_opportunity(opportunity)

    # ----------------------------------------------------- tableau de bord
    def searched_kinds(self, platform: str):
        """Types de contenu reellement cherches sur cette plateforme, ou None.

        None signifie "l'adaptateur ne declare rien", et tout est alors affiche
        -- c'est le cas de YouTube, qui n'a pas de reglage de ce genre.
        """
        adapter = self.adapters.get(platform)
        kinds = getattr(adapter, "content_kinds", None)
        return tuple(kinds) if kinds else None

    def keeps(self, opportunity) -> bool:
        kinds = self.searched_kinds(getattr(opportunity, "platform", ""))
        return kinds is None or getattr(opportunity, "kind", "") in kinds

    def dashboard(self, period: str = DEFAULT_PERIOD, reference=None) -> dict:
        """Chiffres du bandeau (section 20 du Radar Twitch)."""
        threshold = since_iso(period, reference)
        creators = self.store.list_creators()
        active = [c for c in creators if c.active]
        # Un type qui n'est plus cherche ne doit plus etre compte : le bandeau
        # annoncerait des directs alors que le Radar n'en cherche plus. Les
        # contenus des chaines suspendues sont ecartes par la requete
        # elle-meme, comme dans la liste : les deux doivent dire la meme chose.
        opportunities = [o for o in self.store.list_opportunities(since=threshold, limit=1000)
                         if self.keeps(o)]
        strong = [o for o in opportunities if (o.radar_score or 0) >= 80]
        trending = [o for o in opportunities if (o.trend or {}).get("level") in ("forte", "moderee")]
        live = [o for o in opportunities if o.is_live]
        return {
            "creators_watched": len(active),
            "creators_total": len(creators),
            "live_now": len(live),
            "opportunities": len(opportunities),
            "strong_opportunities": len(strong),
            "trends": len(trending),
        }
