"""Structures de la section Projets (historique des analyses passees).

Un projet est un dossier de sortie (le meme que --output cote CLI) auquel on
adjoint un petit manifeste project.json -- results.json (deja produit par
export/exporter.py) reste l'unique source de verite pour les clips eux-memes,
jamais duplique ici."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProjectSummary:
    """Vue legere pour la liste de la page Projets -- pas besoin de charger
    tous les clips juste pour afficher une carte."""
    folder: str
    name: str
    source_label: str
    source_kind: str  # "local" | "youtube"
    created_at: str  # ISO 8601
    clip_count: int
    language: Optional[str] = None
    # Moyenne des potentiels viraux des clips du projet (section 9 : le tableau
    # de bord doit situer une production d'un coup d'oeil). None quand le projet
    # ne contient aucun clip -- une moyenne de rien n'est pas 0.
    average_score: Optional[float] = None


@dataclass
class Project:
    """Vue complete, chargee quand l'utilisateur ouvre un projet."""
    summary: ProjectSummary
    settings_used: dict
    source_url: Optional[str] = None
    clips: list = field(default_factory=list)  # list[dict] -- forme de results.json, pas re-type ici
