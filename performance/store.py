"""Persistance locale des donnees de performance (section 23).

Tout reste sur la machine : trois fichiers JSON sous le dossier de donnees de
l'utilisateur (%LOCALAPPDATA%\\ClipFarming\\performance en .exe, le depot en
developpement). Rien n'est envoye nulle part, jamais.

    clips.json             caracteristiques mesurees a la production
    performances.json      statistiques saisies par l'utilisateur
    learning_profile.json  ponderations et profil (phase suivante)

Trois fichiers et non un seul : ils n'ont ni le meme cycle de vie ni le meme
auteur. Reinitialiser l'apprentissage (section 24) ne doit pas effacer les
caracteristiques mesurees, et une saisie de performance ne doit pas pouvoir
corrompre la fiche technique d'un clip.

Ecriture atomique : fichier temporaire puis remplacement. Une coupure de
courant pendant l'ecriture laisserait sinon un JSON tronque, c'est-a-dire tout
l'historique perdu.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import user_data_dir
from performance.models import (
    SCHEMA_VERSION,
    ClipFeatures,
    ClipPerformance,
    PerformanceRecord,
)

logger = get_logger()

CLIPS_FILE = "clips.json"
PERFORMANCES_FILE = "performances.json"
PROFILE_FILE = "learning_profile.json"


def performance_dir() -> Path:
    return user_data_dir() / "performance"


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as error:
        # Un fichier illisible ne doit pas empecher de produire des clips : on
        # le signale et on repart d'un historique vide plutot que de planter.
        logger.warning(f"Donnees de performance illisibles ({path}) : {error}")
        return {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, **payload}
    handle, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


class PerformanceStore:
    """Acces aux donnees locales. `directory` est injectable pour les tests."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else performance_dir()

    # ------------------------------------------------ caracteristiques
    def load_features(self) -> dict[str, ClipFeatures]:
        raw = _read(self.directory / CLIPS_FILE).get("clips", {})
        return {cid: ClipFeatures.from_dict(d) for cid, d in raw.items()}

    def save_features(self, features: list[ClipFeatures]) -> None:
        """Ajoute ou remplace des fiches. Une reproduction du meme clip ecrase
        l'ancienne fiche : ce sont les caracteristiques du fichier qui existe
        maintenant qui comptent."""
        current = self.load_features()
        for item in features:
            current[item.clip_id] = item
        _write(self.directory / CLIPS_FILE,
               {"clips": {cid: f.to_dict() for cid, f in current.items()}})

    # ------------------------------------------------------ performances
    def load_performances(self) -> dict[str, ClipPerformance]:
        raw = _read(self.directory / PERFORMANCES_FILE).get("performances", {})
        return {cid: ClipPerformance.from_dict(d) for cid, d in raw.items()}

    def save_performance(self, performance: ClipPerformance) -> None:
        """Enregistre une saisie. Une fiche entierement vide SUPPRIME la saisie
        precedente plutot que d'enregistrer du vide : c'est ce que fait un
        utilisateur qui efface tous les champs pour annuler une erreur."""
        current = self.load_performances()
        if performance.is_empty():
            current.pop(performance.clip_id, None)
        else:
            current[performance.clip_id] = performance
        _write(self.directory / PERFORMANCES_FILE,
               {"performances": {cid: p.to_dict() for cid, p in current.items()}})

    def delete_performance(self, clip_id: str) -> None:
        current = self.load_performances()
        if current.pop(clip_id, None) is not None:
            _write(self.directory / PERFORMANCES_FILE,
                   {"performances": {cid: p.to_dict() for cid, p in current.items()}})

    # ----------------------------------------------------------- profil
    def load_profile(self) -> dict:
        return _read(self.directory / PROFILE_FILE)

    def save_profile(self, profile: dict) -> None:
        _write(self.directory / PROFILE_FILE, profile)

    # ------------------------------------------------------------ vues
    def records(self) -> list[PerformanceRecord]:
        """Tous les clips connus, avec leurs performances quand elles existent."""
        performances = self.load_performances()
        return [
            PerformanceRecord(features=f, performance=performances.get(cid))
            for cid, f in sorted(self.load_features().items())
        ]

    def records_with_performance(self) -> list[PerformanceRecord]:
        return [r for r in self.records() if r.has_performance]

    # ---------------------------------------------------------- gestion
    def export_all(self) -> dict:
        """Tout l'historique en un seul objet, pour une sauvegarde ou un
        transfert vers une autre machine (section 23)."""
        return {
            "schema_version": SCHEMA_VERSION,
            "clips": {cid: f.to_dict() for cid, f in self.load_features().items()},
            "performances": {cid: p.to_dict() for cid, p in self.load_performances().items()},
            "learning_profile": self.load_profile(),
        }

    def reset_learning(self) -> None:
        """Reinitialise l'APPRENTISSAGE seulement (section 24).

        Les caracteristiques mesurees et les performances saisies sont
        conservees : ce sont des faits, pas des reglages. Effacer le travail de
        saisie de l'utilisateur parce qu'il veut repartir de ponderations
        neutres serait une perte injustifiee.
        """
        (self.directory / PROFILE_FILE).unlink(missing_ok=True)

    def reset_all(self) -> None:
        """Efface tout l'historique local, saisies comprises."""
        for name in (CLIPS_FILE, PERFORMANCES_FILE, PROFILE_FILE):
            (self.directory / name).unlink(missing_ok=True)
