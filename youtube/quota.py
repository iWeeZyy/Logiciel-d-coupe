"""Suivi local du quota journalier de la YouTube Data API v3.

Le quota est remis a zero a minuit heure du Pacifique (c'est la regle de
Google, pas un choix arbitraire) -- d'ou la dependance a `tzdata` (necessaire
sur Windows, qui n'embarque pas la base de fuseaux horaires IANA que
`zoneinfo` attend ; sans elle, ZoneInfo("America/Los_Angeles") echoue).

Ce compteur est une estimation locale pour avertir *avant* de taper dans le
mur plutot qu'apres -- l'API reste la source de verite finale (une erreur
quotaExceeded peut toujours survenir si le compteur a derive, ex: apres un
crash entre l'appel et l'ecriture du compteur).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.paths import user_data_dir

_PACIFIC = ZoneInfo("America/Los_Angeles")
DEFAULT_QUOTA_PATH = user_data_dir() / ".cache" / "youtube_quota.json"


def _current_pacific_date(now: datetime) -> str:
    return now.astimezone(_PACIFIC).strftime("%Y-%m-%d")


@dataclass
class QuotaState:
    date: str
    used_units: int


class QuotaTracker:
    def __init__(self, daily_limit: int, path: Path = DEFAULT_QUOTA_PATH):
        self.daily_limit = daily_limit
        self.path = path

    def _load(self, now: datetime) -> QuotaState:
        today = _current_pacific_date(now)
        if not self.path.exists():
            return QuotaState(date=today, used_units=0)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") != today:
                return QuotaState(date=today, used_units=0)
            return QuotaState(date=today, used_units=int(data.get("used_units", 0)))
        except (json.JSONDecodeError, KeyError, ValueError):
            return QuotaState(date=today, used_units=0)

    def _save(self, state: QuotaState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"date": state.date, "used_units": state.used_units}, f)

    def used(self, now: datetime | None = None) -> int:
        return self._load(now or datetime.now(_PACIFIC)).used_units

    def remaining(self, now: datetime | None = None) -> int:
        return max(0, self.daily_limit - self.used(now))

    def would_exceed(self, units: int, now: datetime | None = None) -> bool:
        return self.used(now) + units > self.daily_limit

    def record(self, units: int, now: datetime | None = None) -> None:
        now = now or datetime.now(_PACIFIC)
        state = self._load(now)
        state.used_units += units
        self._save(state)
