"""Fusion de la memoire des clips publies (clips_publies.json) quand deux
workflows l'ecrivent en meme temps (2026-11-11 : le top d'Anyme et le clip de
midi ont commite chacun la leur, le `git pull --rebase` du second a echoue
sur un conflit et sa memoire est restee non enregistree).

Fonction pure + petite commande, sans dependance au reste du logiciel : le
workflow l'appelle depuis le depot appelant. La version distante est gardee
telle quelle ; les entrees locales absentes (meme clip, meme date) y sont
ajoutees a la fin, dans leur ordre."""
from __future__ import annotations

import json
import sys


def _key(entry: dict) -> tuple:
    return (entry.get("id"), entry.get("date"))


def merge(remote: dict, local: dict) -> dict:
    remote = dict(remote or {})
    published = list(remote.get("published") or [])
    seen = {_key(e) for e in published}
    for entry in (local or {}).get("published") or []:
        if _key(entry) not in seen:
            published.append(entry)
            seen.add(_key(entry))
    remote["published"] = published
    return remote


def main(argv=None) -> int:
    """memory_merge.py DISTANTE LOCALE SORTIE"""
    remote_path, local_path, out_path = (argv if argv is not None else sys.argv[1:])[:3]

    def load(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    merged = merge(load(remote_path), load(local_path))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
