"""Clip du jour, sans fenetre : le clip le plus vu des dernieres 24 h.

Demande de l'utilisateur (2026-11-01) : un clip seul par jour, sans PC (GitHub
Actions, voir .github/workflows/daily-clip.yml). Sa video la plus vue de la
semaine etait un clip seul avec un texte en haut (11 K vues contre ~800 par
compilation). Lancement :

    python gui_main.py --daily-clip --config ma_config.json --memory clips_publies.json --out sortie/

- Streamers : la MEME configuration et la MEME liste d'autorisations que la
  compilation de la semaine (radar/weekly_top.py) -- un streamer absent ou
  sans date d'accord est refuse, jamais clippe.
- Choix : parmi tous les streamers autorises, le clip le plus vu des
  dernieres 24 h pas encore publie ; a defaut, des 3 derniers jours.
- Traitement : le pipeline habituel avec les reglages de production de la
  configuration (radar/top_compilation.produce_clip), intro et « Texte en
  haut » compris.
- Memoire : les clips deja publies (identifiant Twitch) dans un fichier JSON
  que le workflow garde dans le depot prive -- jamais deux fois le meme.

Aucun import de Qt.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

CLI_FLAG = "--daily-clip"
WINDOWS = (("24h", 1), ("3j", 3))       # periode du Radar, jours
MEMORY_KEEP = 400                       # entrees gardees dans la memoire


# ------------------------------------------------------------------ memoire

def load_memory(path: Path | None) -> list[dict]:
    if not path or not Path(path).is_file():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        logger.warning(f"Mémoire des clips illisible ({path}) : repart de zéro.")
        return []
    return [e for e in (data.get("published") or []) if isinstance(e, dict)]


def save_memory(path: Path, entries: list[dict]) -> None:
    Path(path).write_text(json.dumps({"published": entries[-MEMORY_KEEP:]}, ensure_ascii=False,
                                     indent=2) + "\n", encoding="utf-8")


def _as_clip(entry: dict):
    """Une entree de la memoire, avec ce qu'il faut a same_moment()."""
    from types import SimpleNamespace

    return SimpleNamespace(content_id=entry.get("id"), creator_key=entry.get("creator_key"),
                           title=entry.get("title") or "", published_at=entry.get("published_at"),
                           duration_s=entry.get("duration_s"), extra=entry.get("extra") or {})


def choose(opportunities, memory: list[dict]) -> object | None:
    """Le clip le plus vu pas encore publie (fonction pure) : ni le meme clip,
    ni le meme moment clippe une seconde fois (radar/compilation_pick
    .same_moment). Les directs et ce qui n'est pas un clip sont ignores."""
    from radar.compilation_pick import same_moment

    done = [_as_clip(e) for e in memory]
    candidates = [o for o in opportunities
                  if getattr(o, "kind", "") == "clip" and not getattr(o, "is_live", False)
                  and not any(same_moment(o, d) for d in done)]
    return max(candidates, key=lambda o: o.view_count if o.view_count is not None else -1,
               default=None)


# ------------------------------------------------------------------ legende

def caption(clip_title: str, creator: str) -> str:
    """Legende : le titre du clip, une promesse precise (le clip de demain)
    et les hashtags demandes (#clip #twitch #stream #<createur> #drole)."""
    from video.compilation import HASHTAGS, creator_hashtag

    from video.hook_text import clean

    title = clean(clip_title)            # sans les guillemets du titre Twitch
    head = f"{creator} — {title}" if title else creator
    tags = " ".join([*HASHTAGS, creator_hashtag(creator), "#drole"]).replace("  ", " ")
    return "\n\n".join([f"{head} 🔥", "Abonne-toi pour le clip de demain 👀", tags])


def file_name(creator: str) -> str:
    safe = "".join(ch for ch in (creator or "") if ch.isalnum() or ch == "_") or "Clip"
    return f"Clip_{safe}.mp4"


# -------------------------------------------------------------------- run

def run(config: dict, out_dir: Path, work_dir: Path, *, memory_path: Path | None = None,
        config_dir=None, adapter=None, producer=None, today: date | None = None,
        now: datetime | None = None, on_status=logger.info):
    """Produit le clip du jour. Renvoie (Report, [chemins produits])."""
    from editing.production import default_production
    from radar.creators import CreatorManager
    from radar.store import RadarStore
    from radar.top_compilation import candidates, produce_clip
    from radar.weekly_top import (
        Report,
        _make_engine,
        check_streamers,
        parse_authorizations,
        with_face,
    )

    producer = producer or produce_clip
    report = Report()
    authorizations, warnings = parse_authorizations(config.get("authorized"), today=today)
    report.notes += warnings
    allowed, refused = check_streamers(config.get("streamers"), authorizations)
    report.refused += refused
    if not allowed:
        return report, []

    out_dir, work_dir = Path(out_dir), Path(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    store = RadarStore(work_dir / "radar.sqlite3")
    engine, adapter, max_results = _make_engine(store, adapter)
    manager = CreatorManager(store)
    by_key = {}
    for auth in allowed:
        try:
            creator = adapter.resolve_creator(auth.login)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"{auth.login} : Twitch injoignable ({e})")
            continue
        if creator is None:
            report.errors.append(f"{auth.login} : chaîne Twitch introuvable.")
            continue
        if store.get_creator(creator.key) is None:
            manager.add(creator)
        by_key[creator.key] = (auth, creator)
    if not by_key:
        return report, []

    memory = load_memory(memory_path)
    chosen = None
    for period, days in WINDOWS:
        on_status(f"Rafraîchissement du Radar ({period})…")
        scan = engine.scan(platforms=["twitch"], period=period, max_results=max_results,
                           reference=now)
        report.errors += list(scan.errors)
        chosen = choose([o for o in candidates(store, None, days, now=now)
                         if o.creator_key in by_key], memory)
        if chosen is not None:
            break
    if chosen is None:
        report.notes.append("Aucun nouveau clip ces 3 derniers jours.")
        return report, []

    auth, creator = by_key[chosen.creator_key]
    on_status(f"Clip du jour : {creator.label} — « {chosen.title} » "
              f"({chosen.view_count} vues, {chosen.duration_s} s)")
    production = default_production(**(config.get("production") or {}))
    overrides = dict(production.get("editing_overrides") or {})
    current = overrides.get("hook_text", True)
    if current:
        # Pastille « ● <STREAMER> EN LIVE » au-dessus du bandeau (design E).
        block = dict(current) if isinstance(current, dict) else {"enabled": True}
        block["tag"] = creator.label
        overrides["hook_text"] = block
        production = {**production, "editing_overrides": overrides}
    try:
        path = producer(chosen, with_face(production, config, auth.login, config_dir), None,
                        on_status, projects_dir=work_dir / "projets", clips_dir=work_dir / "clips")
    except Exception as e:  # noqa: BLE001
        logger.exception("Clip du jour impossible")
        report.errors.append(f"{auth.login} : clip impossible ({e})")
        return report, []
    video = out_dir / file_name(creator.label)
    shutil.copyfile(path, video)
    text = caption(chosen.title, creator.label)
    legend = video.with_suffix(".txt")
    legend.write_text(text, encoding="utf-8")
    report.captions.append((auth.login, text))
    report.produced.append((auth.login, [str(video), str(legend)]))
    if memory_path:
        memory.append({"id": chosen.content_id, "creator": auth.login,
                       "creator_key": chosen.creator_key, "title": chosen.title,
                       "views": chosen.view_count, "duration_s": chosen.duration_s,
                       "published_at": chosen.published_at,
                       "extra": {k: (chosen.extra or {}).get(k)
                                 for k in ("source_video_id", "vod_offset")},
                       "date": (now or datetime.now(timezone.utc)).date().isoformat()})
        save_memory(memory_path, memory)
    return report, [video, legend]


def main_cli(argv=None) -> int:
    """Point d'entree de `--daily-clip`. 0 si un clip a ete produit (ou s'il
    n'y avait rien de nouveau), 1 en cas d'erreur. Sous GitHub Actions,
    `produit=true|false` est ecrit dans GITHUB_OUTPUT."""
    import argparse

    from radar.weekly_top import REPORT_NAME, default_config_path, load_config

    parser = argparse.ArgumentParser(prog=f"gui_main.py {CLI_FLAG}")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--memory", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("clip_du_jour"))
    parser.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args([a for a in (argv if argv is not None else sys.argv[1:])
                              if a != CLI_FLAG])
    try:
        config = load_config(args.config)
        report, files = run(config, args.out, args.work_dir or args.out.parent / "_travail_clip",
                            memory_path=args.memory,
                            config_dir=(args.config or default_config_path()).parent)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Clip du jour : {e}")
        print(f"::error::{e}" if os.environ.get("GITHUB_ACTIONS") == "true" else str(e))
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / REPORT_NAME).write_text("\n".join(report.lines()) + "\n", encoding="utf-8")
    for line in report.lines():
        print(line, flush=True)
    for login, text in report.captions:
        print(f"===== LÉGENDE {login} =====\n{text}\n===== FIN LÉGENDE =====", flush=True)
    produced = bool(files)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"produit={'true' if produced else 'false'}\n")
    status = "produit" if produced else ("erreur" if report.errors else "rien")
    print(f"Résultat : {status}", flush=True)
    return 1 if status == "erreur" else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main_cli())
