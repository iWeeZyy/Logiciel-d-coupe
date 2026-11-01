"""Compilation des clips les plus vus de la semaine, sans fenetre.

Demande de l'utilisateur : la « 🏆 Compilation des plus vus » du Radar faite
automatiquement chaque semaine, SANS son PC (GitHub Actions, voir
.github/workflows/weekly-top.yml et le README). Lancement :

    python gui_main.py --weekly-top --config ma_config.json --out sortie/

Enchainement, avec la logique de la fenetre (radar/top_compilation.py) :
rafraichissement du Radar pour les streamers choisis (7 derniers jours) ->
les N clips les plus vus, un seul par moment -> telechargement et pipeline de
chaque clip -> montage (numeros, transitions, ecran de fin) et legende .txt.

Droits : en mode manuel, une fenetre « Vérification des droits » demande de
confirmer avant chaque compilation. Personne ne peut la cliquer ici ; elle
est remplacee, pour CE mode seulement, par une liste d'autorisations dans la
configuration : chaque streamer y figure avec la date a laquelle il a donne
son accord. Un streamer absent de la liste, ou inscrit sans date valable,
est refuse et signale -- jamais compile « en attendant ».

Aucun import de Qt : la commande tourne sur un serveur sans ecran.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

CLI_FLAG = "--weekly-top"
DAYS = 7                       # periode du Radar la plus longue (radar/engine.py, « 7j »)
PERIOD = "7j"
PERIOD_LABEL = "cette semaine"
REPORT_NAME = "rapport.txt"


def default_config_path() -> Path:
    from core.paths import app_base_dir

    return app_base_dir() / "config" / "weekly_compilation.json"


def load_config(path: Path | None = None) -> dict:
    path = Path(path or default_config_path())
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise RuntimeError(f"Configuration introuvable : {path}") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Configuration illisible ({path}) : {e}") from None


# ------------------------------------------------------------ autorisations

@dataclass(frozen=True)
class Authorization:
    login: str
    authorized_on: date
    note: str = ""


def normalize_login(value: str) -> str:
    """« Anyme023 », « @anyme023 » ou l'adresse de la chaine -> « anyme023 »."""
    from radar.platforms.twitch import parse_query

    return parse_query(str(value or "")).lower()


def parse_authorizations(entries, today: date | None = None):
    """({login: Authorization}, [avertissements]). Une entree sans login, sans
    date, avec une date illisible ou dans le futur N'AUTORISE RIEN : une
    autorisation se prouve, elle ne se suppose pas."""
    today = today or date.today()
    granted, warnings = {}, []
    for entry in entries or []:
        if not isinstance(entry, dict):
            warnings.append(f"Entrée d'autorisation ignorée (format) : {entry!r}")
            continue
        login = normalize_login(entry.get("login", ""))
        raw = str(entry.get("authorized_on") or "").strip()
        if not login:
            warnings.append(f"Entrée d'autorisation sans streamer : {entry!r}")
            continue
        try:
            when = date.fromisoformat(raw)
        except ValueError:
            warnings.append(f"{login} : date d'autorisation absente ou illisible "
                            f"(« {raw} », attendu AAAA-MM-JJ) — non autorisé.")
            continue
        if when > today:
            warnings.append(f"{login} : date d'autorisation dans le futur ({raw}) — non autorisé.")
            continue
        granted[login] = Authorization(login, when, str(entry.get("note") or ""))
    return granted, warnings


def check_streamers(streamers, authorizations: dict):
    """(autorises [Authorization], refuses [(login, raison)]), ordre conserve,
    doublons retires."""
    allowed, refused, seen = [], [], set()
    for value in streamers or []:
        login = normalize_login(value)
        if not login or login in seen:
            continue
        seen.add(login)
        if login in authorizations:
            allowed.append(authorizations[login])
        else:
            refused.append((login, "absent de la liste d'autorisations "
                                   "(« authorized » dans la configuration)"))
    return allowed, refused


# ------------------------------------------------------------------ rapport

@dataclass
class Report:
    produced: list = field(default_factory=list)     # [(login, [chemins mp4/txt])]
    refused: list = field(default_factory=list)      # [(login, raison)]
    errors: list = field(default_factory=list)       # [message]
    notes: list = field(default_factory=list)        # [message]
    captions: list = field(default_factory=list)     # [(login, texte de la legende)]

    def lines(self) -> list[str]:
        out = []
        for login, files in self.produced:
            out.append(f"✓ {login} : " + ", ".join(Path(f).name for f in files))
        for login, reason in self.refused:
            out.append(f"✗ REFUSÉ {login} : {reason}")
        out += [f"⚠ {message}" for message in self.errors + self.notes]
        return out or ["Rien à faire : aucun streamer dans la configuration."]


def _signal_github(report: Report) -> None:
    """Sous GitHub Actions : refus et erreurs en annotations visibles sur la
    page du run, et le rapport dans son resume."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for login, reason in report.refused:
        print(f"::warning title=Streamer refusé::{login} — {reason}", flush=True)
    for message in report.errors:
        print(f"::warning title=Compilation::{message}", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Compilation des plus vus de la semaine\n\n")
            f.write("\n".join(f"- {line}" for line in report.lines()) + "\n")
            for login, text in report.captions:
                f.write(f"\n### Légende — {login}\n\n```\n{text}\n```\n")


# ------------------------------------------------------- visage a suivre

def face_photos(config: dict, login: str, config_dir) -> list[str]:
    """Photos de reference du streamer (cle « faces » de la configuration,
    chemins relatifs au fichier de configuration) qui existent vraiment."""
    entries = ((config.get("faces") or {}).get(login) or [])
    base = Path(config_dir) if config_dir else Path(".")
    paths = [base / str(e) for e in (entries if isinstance(entries, list) else [entries])]
    found = [str(p) for p in paths if p.is_file()]
    for p in paths:
        if not p.is_file():
            logger.warning(f"Photo de référence introuvable pour {login} : {p}")
    return found


def with_face(production: dict, config: dict, login: str, config_dir) -> dict:
    """Le traitement, avec la photo du streamer pour que le cadrage le suive
    quand il est a l'ecran (video/face_identity.py). Sans photo : inchange."""
    photos = face_photos(config, login, config_dir)
    if not photos:
        return production
    overrides = dict(production.get("editing_overrides") or {})
    framing = overrides.get("framing", True)
    block = dict(framing) if isinstance(framing, dict) else {"enabled": bool(framing)}
    block["subject_photos"] = photos
    overrides["framing"] = block
    return {**production, "editing_overrides": overrides}


# -------------------------------------------------------------------- run

def _make_engine(store, adapter=None):
    from core.config_loader import load_radar_config
    from radar.engine import RadarEngine
    from radar.platforms.twitch import TwitchAdapter

    radar_config = load_radar_config()
    adapter = adapter or TwitchAdapter(content_kinds=("clip",))
    engine = RadarEngine(store, {"twitch": adapter},
                         weights=(radar_config.get("score", {}) or {}).get("weights", {}))
    max_results = int((radar_config.get("scan", {}) or {}).get("max_results_per_creator", 200))
    return engine, adapter, max_results


def run(config: dict, out_dir: Path, work_dir: Path, *, only=None, config_dir=None,
        adapter=None, accept_long: bool = False,
        builder=None, today: date | None = None, now: datetime | None = None,
        on_status=logger.info) -> Report:
    """Une compilation par streamer autorise. Ne leve que si la configuration
    est inutilisable ; l'echec d'un streamer est rapporte, les autres
    continuent (comme un scan du Radar)."""
    from editing.production import default_production
    from radar.creators import CreatorManager
    from radar.store import RadarStore
    from radar.top_compilation import (
        ASK_ABOVE_S,
        MAX_TOTAL_S,
        MIN_CLIPS,
        build,
        fit_count,
        total_length,
        default_title,
        safe_name,
        select,
        write_captions,
    )

    builder = builder or build
    report = Report()
    authorizations, warnings = parse_authorizations(config.get("authorized"), today=today)
    report.notes += warnings
    streamers = config.get("streamers")
    if only:
        # Essai a la main (bouton « Run workflow ») : un seul streamer, qui
        # doit lui aussi etre dans la liste d'autorisations.
        streamers = [only]
    allowed, refused = check_streamers(streamers, authorizations)
    report.refused += refused
    for login, reason in refused:
        logger.warning(f"Streamer refusé : {login} — {reason}")
    if not allowed:
        return report

    count = int(config.get("count", 5))
    parts = int(config.get("parts", 1))
    if not 2 <= count <= 8 or parts not in (1, 2):
        raise RuntimeError("Configuration : « count » entre 2 et 8, « parts » 1 ou 2.")
    production = default_production(**(config.get("production") or {}))

    out_dir, work_dir = Path(out_dir), Path(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Base du Radar propre a ce run : le serveur repart de zero chaque semaine,
    # et sur un PC la base de l'utilisateur n'est jamais touchee.
    store = RadarStore(work_dir / "radar.sqlite3")
    engine, adapter, max_results = _make_engine(store, adapter)
    manager = CreatorManager(store)

    creators = []
    for auth in allowed:
        on_status(f"Streamer : {auth.login} (autorisé le {auth.authorized_on.isoformat()})")
        try:
            creator = adapter.resolve_creator(auth.login)
        except Exception as e:  # noqa: BLE001 -- un streamer en echec n'arrete pas les autres
            report.errors.append(f"{auth.login} : Twitch injoignable ({e})")
            continue
        if creator is None:
            report.errors.append(f"{auth.login} : chaîne Twitch introuvable.")
            continue
        if store.get_creator(creator.key) is None:
            manager.add(creator)
        creators.append((auth, creator))
    if not creators:
        return report

    on_status("Rafraîchissement du Radar (7 derniers jours)…")
    scan = engine.scan(platforms=["twitch"], period=PERIOD, max_results=max_results,
                       reference=now)
    report.errors += list(scan.errors)

    for auth, creator in creators:
        kept, skipped = select(store, creator.key, DAYS, count, now=now)
        for dup, original, reason in skipped:
            report.notes.append(f"{auth.login} : écarté « {dup.title} » ({reason} que "
                                f"« {original.title} »)")
        if len(kept) < 2:
            report.errors.append(f"{auth.login} : {len(kept)} clip(s) différent(s) cette "
                                 "semaine, il en faut au moins 2.")
            continue
        if len(kept) < count:
            report.notes.append(f"{auth.login} : seulement {len(kept)} clips différents.")
        # Top 3, ou plus si les clips sont courts : 2 min 30 au plus, clips
        # jamais coupes. 15 s ou plus au-dela : on demande avant de monter.
        max_seconds = float(config.get("max_seconds", MAX_TOTAL_S))
        kept = fit_count(kept, max_seconds, minimum=int(config.get("min_count", MIN_CLIPS)))
        length = total_length(kept)
        if length >= max_seconds + ASK_ABOVE_S and not accept_long:
            detail = ", ".join(f"{o.title} ({o.duration_s or '?'} s)" for o in kept)
            report.errors.append(
                f"{auth.login} : CONFIRMATION REQUISE — {len(kept)} clips = "
                f"{int(length // 60)} min {int(length % 60):02d} s, plus de "
                f"{int(max_seconds // 60)} min {int(max_seconds % 60):02d} s. "
                f"Clips : {detail}. Relancer avec la confirmation pour produire.")
            continue
        title = default_title(len(kept), creator.label, PERIOD_LABEL)
        for i, o in enumerate(kept, 1):
            on_status(f"  {i}. {o.title} — {o.view_count} vues, {o.duration_s} s")
        try:
            written = builder(kept, with_face(production, config, auth.login, config_dir),
                              out_dir, name=safe_name(title), parts=parts,
                              # Le #1 (le plus vu) EN PREMIER pour retenir des
                              # la premiere seconde (demande du 2026-11-01) :
                              # plus de compte a rebours par defaut.
                              countdown=bool(config.get("countdown", False)),
                              intro=bool(config.get("intro", True)),
                              transitions=bool(config.get("transitions", True)),
                              # Phrase choc sous chaque numero (texte unique) et
                              # « Dimanche prochain tu veux voir quel top ? »
                              clip_titles=bool(config.get("clip_titles", True)),
                              ask_next=bool(config.get("ask_next", True)),
                              on_status=on_status, projects_dir=work_dir / "projets",
                              clips_dir=work_dir / "clips")
        except Exception as e:  # noqa: BLE001 -- rapporte, les autres streamers continuent
            logger.exception(f"Compilation de {auth.login} impossible")
            report.errors.append(f"{auth.login} : compilation impossible ({e})")
            continue
        captions = write_captions(written, title, creator=creator.label,
                                  ask_next=bool(config.get("ask_next", True)))
        for _path, seconds in written:
            line = f"{auth.login} : vidéo de {int(seconds // 60)} min {int(seconds % 60):02d} s"
            too_long = seconds >= max_seconds + ASK_ABOVE_S and not accept_long
            (report.errors if too_long else report.notes).append(
                line + (" — au-delà de la durée maximale" if too_long else ""))
        report.captions += [(auth.login, c.read_text(encoding="utf-8")) for c in captions]
        report.produced.append((auth.login, [p for p, _ in written] + [str(c) for c in captions]))
    return report


def main_cli(argv=None) -> int:
    """Point d'entree de `--weekly-top`. 0 si au moins une compilation a ete
    produite, 1 sinon. Refus et erreurs sont dans rapport.txt (et, sous
    GitHub Actions, en annotations sur la page du run)."""
    import argparse

    parser = argparse.ArgumentParser(prog=f"gui_main.py {CLI_FLAG}")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("compilation_hebdo"))
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--only", default="",
                        help="un seul streamer (essai) ; il doit etre autorise")
    parser.add_argument("--accepter-duree", dest="accept_long", action="store_true",
                        help="produire meme si la compilation depasse la duree maximale")
    args = parser.parse_args([a for a in (argv if argv is not None else sys.argv[1:])
                              if a != CLI_FLAG])
    started = datetime.now(timezone.utc)
    try:
        config = load_config(args.config)
        report = run(config, args.out, args.work_dir or args.out.parent / "_travail",
                     only=args.only or None, accept_long=args.accept_long,
                     config_dir=(args.config or default_config_path()).parent)
    except Exception as e:  # noqa: BLE001 -- tache de fond : on journalise
        logger.error(f"Compilation de la semaine : {e}")
        print(f"::error::{e}" if os.environ.get("GITHUB_ACTIONS") == "true" else str(e))
        return 1
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    report.notes.append(f"Temps de calcul : {int(elapsed // 60)} min {int(elapsed % 60):02d} s")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / REPORT_NAME).write_text("\n".join(report.lines()) + "\n", encoding="utf-8")
    for line in report.lines():
        print(line, flush=True)
    # La legende en clair dans le journal : elle se lit (et se copie) sans
    # telecharger l'artefact.
    for login, text in report.captions:
        print(f"===== LÉGENDE {login} =====\n{text}\n===== FIN LÉGENDE =====", flush=True)
    _signal_github(report)
    return 0 if report.produced else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main_cli())
