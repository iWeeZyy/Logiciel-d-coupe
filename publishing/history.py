"""Historique local des publications (section 14).

Range dans la base du Radar, pas dans une seconde base : c'est la meme
application, les memes clips, et deux fichiers a sauvegarder valent moins qu'un.
La table est creee a l'ouverture comme les autres.

Une ligne par PLATEFORME et par tentative. Les publications sont independantes :
un echec TikTok n'efface pas une reussite Instagram, et le seul moyen de le
garantir est de ne jamais les ecrire ensemble.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from publishing.models import (
    STATUS_PUBLISHED,
    PublicationRecord,
    utc_now_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
    id          TEXT PRIMARY KEY,
    clip_path   TEXT,
    content_id  TEXT,
    platform    TEXT,
    account     TEXT,
    status      TEXT,
    created_at  TEXT,
    finished_at TEXT,
    url         TEXT,
    caption     TEXT,
    hashtags    TEXT,
    error       TEXT,
    export_dir  TEXT
);

CREATE INDEX IF NOT EXISTS idx_publications_content ON publications (content_id);
CREATE INDEX IF NOT EXISTS idx_publications_date ON publications (created_at DESC);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class PublicationHistory:
    """Acces a l'historique. `path` est injectable pour les tests."""

    def __init__(self, path: Path | None = None):
        if path is None:
            from radar.store import radar_db_path

            path = radar_db_path()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record(self, record: PublicationRecord) -> PublicationRecord:
        """Ecrit ou met a jour une tentative."""
        if not record.id:
            record.id = new_id()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO publications
                   (id, clip_path, content_id, platform, account, status, created_at,
                    finished_at, url, caption, hashtags, error, export_dir)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     url=excluded.url, error=excluded.error,
                     export_dir=excluded.export_dir""",
                (record.id, record.clip_path, record.content_id, record.platform,
                 record.account, record.status, record.created_at, record.finished_at,
                 record.url, record.caption, "\n".join(record.hashtags),
                 record.error, record.export_dir),
            )
        return record

    def finish(self, record: PublicationRecord, status: str, *, url: str = "",
               error: str = "", export_dir: str = "") -> PublicationRecord:
        record.status = status
        record.finished_at = utc_now_iso()
        record.url = url or record.url
        record.error = error
        record.export_dir = export_dir or record.export_dir
        return self.record(record)

    def _rows_to_records(self, rows) -> list:
        out = []
        for row in rows:
            data = dict(row)
            data["hashtags"] = [h for h in (data.get("hashtags") or "").split("\n") if h]
            out.append(PublicationRecord.from_dict(data))
        return out

    def list_all(self, limit: int = 100) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publications ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return self._rows_to_records(rows)

    def for_content(self, content_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publications WHERE content_id = ? ORDER BY created_at DESC",
                (content_id,),
            ).fetchall()
        return self._rows_to_records(rows)

    def published_platforms(self, content_id: str) -> set:
        """Plateformes ou ce contenu a REELLEMENT ete publie.

        Sert au badge de la carte du Radar (section 16). Seules les tentatives
        reussies comptent : afficher "Instagram ✓" apres un echec serait un
        mensonge, et exactement le genre qui fait republier deux fois.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT platform FROM publications WHERE content_id = ? AND status = ?",
                (content_id, STATUS_PUBLISHED),
            ).fetchall()
        return {row["platform"] for row in rows}

    def published_map(self) -> dict:
        """{content_id: {plateformes}} en une requete, pour la liste du Radar."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT content_id, platform FROM publications WHERE status = ?",
                (STATUS_PUBLISHED,),
            ).fetchall()
        out: dict[str, set] = {}
        for row in rows:
            out.setdefault(row["content_id"], set()).add(row["platform"])
        return out
