"""Ce que doit savoir faire un adaptateur de plateforme.

Deux methodes seulement : dire s'il est utilisable, et publier. Tout le reste
-- legendes, hashtags, couverture, controles, export -- est fait en amont et ne
regarde pas la plateforme.

`status()` doit etre HONNETE et precis. "Indisponible" sans raison oblige a
chercher au hasard ; ce qui aide, c'est de savoir qu'il manque un identifiant
d'application, une autorisation, ou un type de compte particulier.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformStatus:
    """Etat d'une plateforme, tel que l'interface l'affiche."""

    platform: str
    configured: bool = False      # identifiants d'application presents
    connected: bool = False       # un compte est relie
    account: str = ""
    reason: str = ""              # pourquoi ce n'est pas utilisable
    setup_hint: str = ""          # ce qu'il faut faire

    @property
    def can_publish(self) -> bool:
        return self.configured and self.connected


@dataclass(frozen=True)
class PublishResult:
    """Resultat d'une tentative. `url` n'est renseignee que si la plateforme
    l'a renvoyee -- on ne fabrique pas un lien plausible."""

    ok: bool
    url: str = ""
    error: str = ""


class PlatformAdapter:
    """Interface commune. Les implementations vivent a cote."""

    platform = ""

    def status(self) -> PlatformStatus:      # pragma: no cover - interface
        raise NotImplementedError

    def publish(self, draft, *, on_progress=None, cancel_token=None) -> PublishResult:
        raise NotImplementedError            # pragma: no cover - interface

    def disconnect(self) -> None:            # pragma: no cover - interface
        raise NotImplementedError
