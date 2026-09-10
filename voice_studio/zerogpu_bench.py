"""Le journal du banc d'essai : un essai par ligne, pour pouvoir comparer.

POURQUOI UN FICHIER ET PAS UN AFFICHAGE. La question posee est « ZeroGPU
vaut-il mieux que mon processeur ». Une seule generation ne repond pas : la
file d'attente varie selon l'heure, un Space endormi coute son reveil. Il faut
plusieurs essais, sur plusieurs scripts, et donc les garder.

FORMAT : JSON Lines (un objet JSON complet par ligne). Choisi parce qu'une
ligne s'ajoute sans relire ni reecrire le fichier, et qu'un fichier tronque ne
perd que sa derniere ligne au lieu de devenir illisible. Il s'ouvre dans un
editeur de texte et s'importe dans un tableur.

MODULE PUR AU CALCUL, impur a l'ecriture seulement : `rows()` et `compare()`
ne touchent a rien.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from core.logging_setup import get_logger

logger = get_logger()

FILE_NAME = "zerogpu_benchmark.jsonl"


@dataclass(frozen=True)
class Entry:
    """Un essai. Tous les champs sont MESURES ; aucun n'est estime."""

    when: str = ""
    engine: str = "zerogpu"        # "zerogpu" | "chatterbox" | "piper" | "sapi"
    space: str = ""
    label: str = ""                # nom donne au script par l'utilisateur
    chars: int = 0
    words: int = 0
    chunks: int = 0
    audio_s: float = 0.0
    queue_s: float = 0.0
    gpu_s: float = 0.0
    total_s: float = 0.0
    rtf: Optional[float] = None
    rtf_total: Optional[float] = None
    note: str = ""


def path() -> Path:
    from voice_studio import store

    return Path(store.audio_dir()).parent / FILE_NAME


def entry_from(measure, engine: str = "zerogpu", space: str = "",
               label: str = "", note: str = "") -> Entry:
    return Entry(
        when=time.strftime("%Y-%m-%d %H:%M:%S"),
        engine=engine, space=space, label=label,
        chars=measure.chars, words=measure.words, chunks=measure.chunks,
        audio_s=round(measure.audio_s, 2),
        queue_s=round(measure.queue_s, 2),
        gpu_s=round(measure.gpu_s, 2),
        total_s=round(measure.total_s, 2),
        rtf=round(measure.rtf, 4) if measure.rtf is not None else None,
        rtf_total=round(measure.rtf_total, 4) if measure.rtf_total is not None else None,
        note=note,
    )


def append(entry: Entry) -> str:
    """Ajoute une ligne. Une panne d'ecriture ne fait jamais echouer une
    generation reussie : le journal est un confort, pas le produit."""
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    except OSError as error:                          # pragma: no cover - disque
        logger.warning(f"Journal du banc d'essai non écrit ({error}).")
        return ""
    return str(target)


def rows(text: str) -> list:
    """Analyse le contenu du journal. Une ligne abimee est ignoree, pas fatale."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def load() -> list:
    target = path()
    try:
        return rows(target.read_text(encoding="utf-8")) if target.is_file() else []
    except OSError:                                   # pragma: no cover - disque
        return []


def compare(entries: list) -> dict:
    """Moyenne du RTF par moteur, et nombre d'essais derriere chaque moyenne.

    Le nombre d'essais est rendu avec la moyenne, volontairement : une moyenne
    sur un seul essai n'est pas une moyenne, et l'ecran doit pouvoir le dire.
    """
    grouped: dict = {}
    for row in entries:
        engine = str(row.get("engine") or "?")
        value = row.get("rtf")
        if value is None:
            continue
        grouped.setdefault(engine, []).append(float(value))
    return {
        engine: {"runs": len(values), "rtf": sum(values) / len(values)}
        for engine, values in grouped.items() if values
    }
