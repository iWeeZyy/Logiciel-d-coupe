"""Mise en forme des fiches du Radar.

Module PUR : uniquement des chaines, aucune dependance a Qt ni au reseau, donc
verifiable sans ouvrir de fenetre.
"""
from __future__ import annotations

from datetime import datetime, timezone

ORDER_SCORE = "score"
ORDER_RECENT = "recent"
ORDER_VIEWS = "views"

ORDER_LABELS = {
    ORDER_SCORE: "Score radar",
    ORDER_RECENT: "Plus récents",
    ORDER_VIEWS: "Plus vus",
}


def parse_iso(stamp: str):
    """Date ISO renvoyee par une plateforme, ou None si elle est illisible."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_duration(seconds) -> str:
    """m:ss, ou h:mm:ss au-dela d'une heure. Chaine vide si la duree est
    inconnue -- jamais "0:00", qui se lirait comme un clip vide."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_age(published_at: str, now=None) -> str:
    """Anciennete lisible : « il y a 3 h », « il y a 2 j », puis la date.

    L'anciennete parle plus qu'une date pour du contenu du jour, mais au-dela
    d'une semaine c'est l'inverse -- « il y a 23 j » ne dit rien, une date si.
    """
    moment = parse_iso(published_at)
    if moment is None:
        return ""
    now = now or datetime.now(timezone.utc)
    delta = (now - moment).total_seconds()
    if delta < 0:
        return moment.strftime("%d/%m/%Y")
    if delta < 60:
        return "à l'instant"
    if delta < 3600:
        return f"il y a {int(delta // 60)} min"
    if delta < 86400:
        return f"il y a {int(delta // 3600)} h"
    if delta < 7 * 86400:
        return f"il y a {int(delta // 86400)} j"
    return moment.strftime("%d/%m/%Y")


def is_readable_category(value: str) -> bool:
    """Une categorie faite uniquement de chiffres est un identifiant technique.

    /clips renvoie un `game_id` numerique et non le nom du jeu. Tant qu'il n'est
    pas resolu, l'afficher revient a montrer un nombre qui ne dit rien -- et a
    en faire un hashtag. Les fiches enregistrees avant la resolution des noms
    en portent encore un : ce garde-fou les laisse passer sans le nombre.
    """
    value = (value or "").strip()
    return bool(value) and not value.isdigit()


def sort_key(order: str):
    """Cle de tri appliquee cote interface, identique a l'ordre SQL.

    Les deux existent parce que l'onglet « Tous » fusionne deux plateformes
    interrogees separement : chacune arrive triee, la liste fusionnee ne l'est
    plus.
    """
    if order == ORDER_RECENT:
        return lambda o: (o.published_at or "", o.radar_score or 0)
    if order == ORDER_VIEWS:
        return lambda o: (o.view_count if o.view_count is not None else -1,
                          o.published_at or "")
    return lambda o: (o.radar_score or 0, o.published_at or "")
