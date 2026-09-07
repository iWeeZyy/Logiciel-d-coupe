"""Organisation du dossier de sortie et ecriture des metadonnees.

Structure produite (section 10 du cahier des charges) :

    Project/
    ├── clips/        clip_01.mp4, clip_02.mp4...
    ├── thumbnails/   (phase D)
    ├── subtitles/    (phase B)
    ├── metadata/     clip_01.json -- detail complet d'un clip
    ├── project.json  manifeste du projet (projects/store.py)
    └── results.json  index leger de tous les clips

results.json reste l'index unique lu par la page Projets et par la CLI : les
fichiers de metadata/ le completent clip par clip (scores detailles, contexte,
et plus tard titres/miniatures) sans le dupliquer.

Compatibilite : les projets produits avant cette structure ont leurs clips a
plat a la racine, avec un nom portant le score (clip_01_score_87.mp4). Ils
restent lisibles -- le chemin du clip est stocke dans results.json, donc
relatif au dossier du projet dans les deux cas.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.models import ClipResult
from utils.errors import OutputExistsError

CLIPS_DIR = "clips"
THUMBNAILS_DIR = "thumbnails"
SUBTITLES_DIR = "subtitles"
METADATA_DIR = "metadata"

_SUBDIRS = (CLIPS_DIR, THUMBNAILS_DIR, SUBTITLES_DIR, METADATA_DIR)


def clip_stem(index: int) -> str:
    """"clip_01" -- base de nommage partagee par le mp4, les sous-titres, la
    miniature et le fichier de metadonnees d'un meme clip."""
    return f"clip_{index:02d}"


def clip_relative_path(index: int) -> str:
    """Chemin du clip RELATIF au dossier du projet ("clips/clip_01.mp4").

    Le score n'apparait plus dans le nom : il change quand les poids changent,
    alors que le nom de fichier, lui, est reference par results.json, par les
    sous-titres et par les miniatures."""
    return f"{CLIPS_DIR}/{clip_stem(index)}.mp4"


def preflight_check(output_dir: str, overwrite: bool) -> None:
    out = Path(output_dir)
    if not out.exists():
        return
    # Les deux dispositions sont verifiees : la nouvelle (clips/) et l'ancienne
    # (clips a plat), pour ne jamais ecraser sans prevenir un ancien projet.
    existing = list((out / CLIPS_DIR).glob("clip_*.mp4")) + list(out.glob("clip_*.mp4"))
    if existing and not overwrite:
        raise OutputExistsError(
            f"'{output_dir}' contient deja {len(existing)} clip(s) (ex: {existing[0].name}). "
            "Utilise --overwrite pour les remplacer, ou choisis un autre --output."
        )


def ensure_output_dir(output_dir: str) -> None:
    """Cree le dossier du projet et ses sous-dossiers. Les sous-dossiers des
    phases suivantes (thumbnails/, subtitles/) sont crees des maintenant : un
    dossier vide est plus lisible qu'une arborescence qui change de forme selon
    les options actives."""
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    for name in _SUBDIRS:
        (base / name).mkdir(exist_ok=True)


def write_clip_metadata(output_dir: str, clip: ClipResult) -> str:
    path = Path(output_dir) / METADATA_DIR / f"{clip_stem(clip.index)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clip.to_dict(), f, ensure_ascii=False, indent=2)
    return str(path)


def update_clip_metadata(output_dir: str, index: int, metadata: dict) -> None:
    """Enregistre des titres/description modifies a la main.

    Ecrit dans les DEUX endroits qui les portent -- metadata/clip_XX.json et
    l'entree correspondante de results.json -- sinon la page Resultats
    reafficherait l'ancien texte au prochain chargement du projet."""
    base = Path(output_dir)
    clip_file = base / METADATA_DIR / f"{clip_stem(index)}.json"
    if clip_file.exists():
        try:
            data = json.loads(clip_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        data["metadata"] = metadata
        clip_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    results_file = base / "results.json"
    if not results_file.exists():
        return
    try:
        results = json.loads(results_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if 1 <= index <= len(results):
        results[index - 1]["metadata"] = metadata
        results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def write_results(output_dir: str, clip_results: list[ClipResult]) -> str:
    path = Path(output_dir) / "results.json"
    data = [c.to_dict() for c in clip_results]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return str(path)
