"""Interface commune aux plateformes.

Un adaptateur fait deux choses, et rien d'autre :

    resolve_creator(query)   trouver une chaine a partir d'un nom, d'un handle
                             ou d'une URL, sans jamais l'ajouter
    scan(creator, since)     lister les contenus recents d'un createur

Il ne calcule aucun score, ne stocke rien et ne decide de rien. C'est ce qui
permet de tester tout le reste du Radar sans reseau, et d'ajouter une troisieme
plateforme sans toucher au moteur.

Les erreurs remontent typees (utils/errors.py) pour que l'interface puisse dire
ce qui s'est passe -- cle absente, quota atteint, chaine inexistante, reseau --
plutot qu'afficher une trace.
"""
from __future__ import annotations

from dataclasses import dataclass

from radar.models import Creator, Opportunity


@dataclass(frozen=True)
class PlatformStatus:
    """Etat d'une plateforme : utilisable ou non, et pourquoi.

    L'interface s'en sert pour desactiver un onglet avec une explication plutot
    que de laisser l'utilisateur cliquer sur un scan qui echouera.
    """

    platform: str
    available: bool
    reason: str = ""
    setup_hint: str = ""


class PlatformAdapter:
    """Contrat implemente par chaque plateforme."""

    platform: str = ""

    def status(self) -> PlatformStatus:
        raise NotImplementedError

    def resolve_creator(self, query: str) -> Creator | None:
        raise NotImplementedError

    def scan(self, creator: Creator, since_iso: str, max_results: int = 50) -> list[Opportunity]:
        raise NotImplementedError
