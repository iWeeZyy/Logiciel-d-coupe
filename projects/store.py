"""Creation/listing/chargement des projets sur disque.

Un projet = un dossier (celui utilise comme --output du pipeline) + un
project.json cree juste avant de lancer l'analyse, et un results.json cree
par export/exporter.py une fois l'analyse terminee. store.py ne fait aucune
hypothese sur le moteur de traitement -- il lit/ecrit juste ces deux fichiers.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.paths import default_projects_dir
from projects.models import Project, ProjectSummary

# "user_projects", pas "projects" : ce dernier est le nom du paquet Python
# lui-meme (projects/models.py, projects/store.py) -- en mode dev (script,
# pas .exe fige), app_base_dir() pointe sur la racine du depot, donc utiliser
# "projects" ici creerait les dossiers de projets DANS le paquet source.
DEFAULT_PROJECTS_DIR = default_projects_dir()
_MANIFEST_NAME = "project.json"
_RESULTS_NAME = "results.json"


def _slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "projet"


def create_project_folder(name: str, projects_dir: Path = DEFAULT_PROJECTS_DIR) -> Path:
    projects_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"{_slugify(name)}-{timestamp}"

    # Deux projets peuvent etre crees dans la meme seconde (double-clic sur
    # "Generer", ou deux projets lances a la suite) -- on resout la collision
    # plutot que de laisser mkdir(exist_ok=False) planter.
    folder = projects_dir / base
    suffix = 2
    while True:
        try:
            folder.mkdir(parents=True, exist_ok=False)
            return folder
        except FileExistsError:
            folder = projects_dir / f"{base}-{suffix}"
            suffix += 1


def write_manifest(
    folder: Path,
    name: str,
    source_label: str,
    source_kind: str,
    settings_used: dict,
    source_url: Optional[str] = None,
) -> None:
    manifest = {
        "name": name,
        "source_label": source_label,
        "source_kind": source_kind,
        "source_url": source_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings_used": settings_used,
    }
    with open(folder / _MANIFEST_NAME, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> Optional[dict | list]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def average_score(results: list[dict]) -> float | None:
    """Moyenne des potentiels viraux d'un projet, ou None s'il n'y a rien a
    moyenner. results.json est deja lu par l'appelant : aucune lecture de plus.

    On prend `score` (le potentiel viral, deja le champ de tete d'un clip) et
    non une recombinaison locale -- deux definitions du meme chiffre finiraient
    par diverger."""
    values = [r.get("score") for r in results if isinstance(r.get("score"), (int, float))]
    return round(sum(values) / len(values), 1) if values else None


def list_projects(projects_dir: Path = DEFAULT_PROJECTS_DIR) -> list[ProjectSummary]:
    if not projects_dir.exists():
        return []

    summaries: list[ProjectSummary] = []
    for folder in sorted(projects_dir.iterdir()):
        if not folder.is_dir():
            continue
        manifest = _read_json(folder / _MANIFEST_NAME)
        if manifest is None:
            continue  # dossier qui n'est pas un projet reconnu -- ignore plutot que planter
        results = _read_json(folder / _RESULTS_NAME) or []
        language = results[0].get("language") if results else None

        summaries.append(
            ProjectSummary(
                average_score=average_score(results),
                folder=str(folder),
                name=manifest.get("name", folder.name),
                source_label=manifest.get("source_label", ""),
                source_kind=manifest.get("source_kind", "local"),
                created_at=manifest.get("created_at", ""),
                clip_count=len(results),
                language=language,
            )
        )

    summaries.sort(key=lambda s: s.created_at, reverse=True)
    return summaries


def load_project(folder: str | Path) -> Project:
    folder = Path(folder)
    manifest = _read_json(folder / _MANIFEST_NAME) or {}
    results = _read_json(folder / _RESULTS_NAME) or []
    language = results[0].get("language") if results else None

    summary = ProjectSummary(
        folder=str(folder),
        name=manifest.get("name", folder.name),
        source_label=manifest.get("source_label", ""),
        source_kind=manifest.get("source_kind", "local"),
        created_at=manifest.get("created_at", ""),
        clip_count=len(results),
        language=language,
    )
    return Project(
        summary=summary,
        settings_used=manifest.get("settings_used", {}),
        source_url=manifest.get("source_url"),
        clips=results,
    )


def delete_project(folder: str | Path) -> None:
    import shutil

    shutil.rmtree(Path(folder), ignore_errors=True)
