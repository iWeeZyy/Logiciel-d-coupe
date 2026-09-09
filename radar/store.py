"""Base locale du Radar (section 21).

SQLite et non JSON, contrairement au reste du projet : l'historique des releves
grossit a chaque scan (un enregistrement par contenu et par scan) et devra etre
interroge par plage de dates. Relire et reecrire un JSON entier a chaque scan
deviendrait couteux au bout de quelques semaines. SQLite est dans la
bibliotheque standard -- aucune dependance ajoutee.

Tout est local, dans le dossier de donnees de l'utilisateur. Rien n'est envoye
nulle part : la seule sortie reseau du Radar est l'appel aux APIs officielles
pour RECUPERER des metadonnees.

Aucune donnee sensible n'est stockee : identifiants publics de chaines, titres,
statistiques publiques. Les jetons d'API ne passent jamais par cette base.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core.paths import user_data_dir
from radar.models import Creator, Opportunity, ScanResult, Snapshot

# 2 : ajout de clip_analyses (analyse de contenu d'un clip). La migration est
# automatique et sans perte -- chaque table est creee IF NOT EXISTS et aucune
# colonne existante n'a change -- donc une base deja remplie gagne simplement la
# nouvelle table a la premiere ouverture.
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    key           TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,
    platform_id   TEXT NOT NULL,
    username      TEXT,
    display_name  TEXT,
    url           TEXT,
    avatar_url    TEXT,
    follower_count INTEGER,
    priority      TEXT NOT NULL DEFAULT 'normal',
    active        INTEGER NOT NULL DEFAULT 1,
    position      INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT,
    last_scan_at  TEXT,
    extra         TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
    key            TEXT PRIMARY KEY,
    platform       TEXT NOT NULL,
    content_id     TEXT NOT NULL,
    kind           TEXT NOT NULL,
    creator_key    TEXT NOT NULL,
    title          TEXT,
    url            TEXT,
    thumbnail_url  TEXT,
    published_at   TEXT,
    duration_s     INTEGER,
    category       TEXT,
    view_count     INTEGER,
    like_count     INTEGER,
    comment_count  INTEGER,
    viewer_count   INTEGER,
    discovered_at  TEXT,
    is_live        INTEGER NOT NULL DEFAULT 0,
    radar_score    REAL,
    score_breakdown TEXT,
    trend          TEXT,
    extra          TEXT
);
CREATE INDEX IF NOT EXISTS idx_opportunities_creator ON opportunities(creator_key);
CREATE INDEX IF NOT EXISTS idx_opportunities_published ON opportunities(published_at);

-- Un enregistrement par contenu et par scan : c'est cette table, et elle
-- seule, qui rend une vitesse de progression calculable.
CREATE TABLE IF NOT EXISTS snapshots (
    content_id    TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    view_count    INTEGER,
    like_count    INTEGER,
    comment_count INTEGER,
    viewer_count  INTEGER,
    PRIMARY KEY (content_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_content ON snapshots(content_id, captured_at);

CREATE TABLE IF NOT EXISTS favorites (
    key         TEXT PRIMARY KEY,
    added_at    TEXT,
    note        TEXT,
    -- Statistiques AU MOMENT de la mise en favori : elles ne doivent pas
    -- bouger quand le contenu evolue, sinon on perd la raison du choix.
    snapshot    TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    started_at   TEXT PRIMARY KEY,
    finished_at  TEXT,
    platforms    TEXT,
    creators_scanned INTEGER,
    opportunities_found INTEGER,
    errors       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Une analyse par contenu : la cle primaire est le contenu lui-meme, donc une
-- reanalyse REMPLACE la precedente au lieu d'empiler des doublons (section 12).
-- Les champs sortis du JSON sont ceux qui servent a decider si une analyse peut
-- etre reutilisee (section 17) : les interroger ne doit pas demander de
-- desserialiser toutes les analyses de la base.
CREATE TABLE IF NOT EXISTS clip_analyses (
    content_id        TEXT PRIMARY KEY,
    platform          TEXT,
    analysis_level    TEXT,
    model_used        TEXT,
    media_fingerprint TEXT,
    analyzed_at       TEXT,
    confidence        TEXT,
    payload           TEXT
);

CREATE INDEX IF NOT EXISTS idx_analyses_date ON clip_analyses (analyzed_at DESC);
"""


def radar_db_path() -> Path:
    return user_data_dir() / "radar" / "radar.sqlite3"


def _dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


class RadarStore:
    """Acces a la base. `path` est injectable pour les tests."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else radar_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # OR REPLACE et non OR IGNORE : le script ci-dessus vient d'etre
            # applique, la base est donc bien a cette version. La laisser
            # annoncer une version anterieure serait faux.
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------- createurs
    def upsert_creator(self, creator: Creator, position: int | None = None) -> None:
        with self._connect() as conn:
            if position is None:
                row = conn.execute(
                    "SELECT position FROM creators WHERE key = ?", (creator.key,)
                ).fetchone()
                if row is not None:
                    position = row["position"]
                else:
                    top = conn.execute("SELECT COALESCE(MAX(position), -1) AS p FROM creators").fetchone()
                    position = top["p"] + 1
            conn.execute(
                """INSERT INTO creators (key, platform, platform_id, username, display_name,
                       url, avatar_url, follower_count, priority, active, position,
                       added_at, last_scan_at, extra)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                       username=excluded.username, display_name=excluded.display_name,
                       url=excluded.url, avatar_url=excluded.avatar_url,
                       follower_count=excluded.follower_count, priority=excluded.priority,
                       active=excluded.active, position=excluded.position,
                       last_scan_at=excluded.last_scan_at, extra=excluded.extra""",
                (creator.key, creator.platform, creator.platform_id, creator.username,
                 creator.display_name, creator.url, creator.avatar_url, creator.follower_count,
                 creator.priority, int(creator.active), position, creator.added_at,
                 creator.last_scan_at, _dumps(creator.extra)),
            )

    def get_creator(self, key: str) -> Creator | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creators WHERE key = ?", (key,)).fetchone()
        return self._creator_from_row(row) if row else None

    def list_creators(self, platform: str | None = None, active_only: bool = False) -> list[Creator]:
        query = "SELECT * FROM creators"
        clauses, params = [], []
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if active_only:
            clauses.append("active = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        # Priorite d'abord, puis l'ordre choisi par l'utilisateur.
        query += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, position"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._creator_from_row(row) for row in rows]

    def set_creator_positions(self, keys: list[str]) -> None:
        with self._connect() as conn:
            for index, key in enumerate(keys):
                conn.execute("UPDATE creators SET position = ? WHERE key = ?", (index, key))

    def delete_creator(self, key: str) -> None:
        """Retire le createur de la LISTE. Ses opportunites, releves et favoris
        restent : supprimer un createur de la surveillance ne doit pas effacer
        l'historique deja constitue (section 4)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM creators WHERE key = ?", (key,))

    @staticmethod
    def _creator_from_row(row) -> Creator:
        data = dict(row)
        data["active"] = bool(data.get("active", 1))
        data["extra"] = _loads(data.get("extra"))
        data.pop("key", None)
        data.pop("position", None)
        return Creator.from_dict(data)

    # ---------------------------------------------------- opportunites
    def upsert_opportunity(self, opportunity: Opportunity) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO opportunities (key, platform, content_id, kind, creator_key,
                       title, url, thumbnail_url, published_at, duration_s, category,
                       view_count, like_count, comment_count, viewer_count, discovered_at,
                       is_live, radar_score, score_breakdown, trend, extra)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                       title=excluded.title, thumbnail_url=excluded.thumbnail_url,
                       duration_s=excluded.duration_s, category=excluded.category,
                       view_count=excluded.view_count, like_count=excluded.like_count,
                       comment_count=excluded.comment_count, viewer_count=excluded.viewer_count,
                       is_live=excluded.is_live, radar_score=excluded.radar_score,
                       score_breakdown=excluded.score_breakdown, trend=excluded.trend,
                       extra=excluded.extra""",
                (opportunity.key, opportunity.platform, opportunity.content_id, opportunity.kind,
                 opportunity.creator_key, opportunity.title, opportunity.url,
                 opportunity.thumbnail_url, opportunity.published_at, opportunity.duration_s,
                 opportunity.category, opportunity.view_count, opportunity.like_count,
                 opportunity.comment_count, opportunity.viewer_count, opportunity.discovered_at,
                 int(opportunity.is_live), opportunity.radar_score,
                 _dumps(opportunity.score_breakdown), _dumps(opportunity.trend),
                 _dumps(opportunity.extra)),
            )

    def list_opportunities(self, platform: str | None = None, creator_key: str | None = None,
                           since: str | None = None, limit: int = 500) -> list[Opportunity]:
        query = "SELECT * FROM opportunities"
        clauses, params = [], []
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if creator_key:
            clauses.append("creator_key = ?")
            params.append(creator_key)
        if since:
            clauses.append("published_at >= ?")
            params.append(since)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(radar_score, -1) DESC, published_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._opportunity_from_row(row) for row in rows]

    def get_opportunity(self, key: str) -> Opportunity | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM opportunities WHERE key = ?", (key,)).fetchone()
        return self._opportunity_from_row(row) if row else None

    @staticmethod
    def _opportunity_from_row(row) -> Opportunity:
        data = dict(row)
        data["is_live"] = bool(data.get("is_live", 0))
        for name in ("score_breakdown", "trend", "extra"):
            data[name] = _loads(data.get(name))
        data.pop("key", None)
        return Opportunity.from_dict(data)

    # -------------------------------------------------------- releves
    def add_snapshot(self, snapshot: Snapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO snapshots
                   (content_id, captured_at, view_count, like_count, comment_count, viewer_count)
                   VALUES (?,?,?,?,?,?)""",
                (snapshot.content_id, snapshot.captured_at, snapshot.view_count,
                 snapshot.like_count, snapshot.comment_count, snapshot.viewer_count),
            )

    def snapshots(self, content_id: str, limit: int = 50) -> list[Snapshot]:
        """Releves d'un contenu, du plus ancien au plus recent."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE content_id = ? ORDER BY captured_at DESC LIMIT ?",
                (content_id, limit),
            ).fetchall()
        return [Snapshot.from_dict(dict(row)) for row in reversed(rows)]

    # -------------------------------------------------------- favoris
    def add_favorite(self, opportunity: Opportunity, note: str = "") -> None:
        from radar.models import utc_now_iso
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO favorites (key, added_at, note, snapshot) VALUES (?,?,?,?)",
                (opportunity.key, utc_now_iso(), note, _dumps(opportunity.to_dict())),
            )

    def remove_favorite(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM favorites WHERE key = ?", (key,))

    def is_favorite(self, key: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM favorites WHERE key = ?", (key,)
            ).fetchone() is not None

    def list_favorites(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM favorites ORDER BY added_at DESC").fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["snapshot"] = _loads(data.get("snapshot"))
            out.append(data)
        return out

    # ------------------------------------------------------ historique
    def record_scan(self, result: ScanResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scans
                   (started_at, finished_at, platforms, creators_scanned,
                    opportunities_found, errors)
                   VALUES (?,?,?,?,?,?)""",
                (result.started_at, result.finished_at, json.dumps(list(result.platforms)),
                 result.creators_scanned, result.opportunities_found,
                 json.dumps(result.errors, ensure_ascii=False)),
            )

    def list_scans(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            try:
                data["platforms"] = json.loads(data.get("platforms") or "[]")
                data["errors"] = json.loads(data.get("errors") or "[]")
            except json.JSONDecodeError:
                data["platforms"], data["errors"] = [], []
            out.append(data)
        return out

    # ------------------------------------------------------- analyses
    def save_analysis(self, analysis) -> None:
        """Enregistre ou remplace l'analyse d'un contenu.

        Remplacement plutot qu'historique de versions : une analyse est
        entierement recalculee a partir du meme media, deux versions
        successives du meme clip n'apprennent rien de plus et encombreraient la
        liste (section 12, "ne pas multiplier inutilement les doublons").
        """
        payload = analysis.to_dict()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO clip_analyses
                   (content_id, platform, analysis_level, model_used,
                    media_fingerprint, analyzed_at, confidence, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(content_id) DO UPDATE SET
                     platform=excluded.platform,
                     analysis_level=excluded.analysis_level,
                     model_used=excluded.model_used,
                     media_fingerprint=excluded.media_fingerprint,
                     analyzed_at=excluded.analyzed_at,
                     confidence=excluded.confidence,
                     payload=excluded.payload""",
                (analysis.content_id, analysis.platform, analysis.analysis_level,
                 analysis.model_used, analysis.media_fingerprint, analysis.analyzed_at,
                 analysis.confidence, _dumps(payload)),
            )

    def get_analysis(self, content_id: str):
        from radar.analysis.models import ClipAnalysis
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM clip_analyses WHERE content_id = ?", (content_id,)
            ).fetchone()
        if row is None:
            return None
        payload = _loads(row["payload"])
        return ClipAnalysis.from_dict(payload) if payload else None

    def has_analysis(self, content_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM clip_analyses WHERE content_id = ?", (content_id,)
            ).fetchone()
        return row is not None

    def analyzed_ids(self) -> set:
        """Contenus deja analyses, en une requete.

        La liste des opportunites peut compter des centaines de lignes ; une
        requete par carte pour savoir s'il faut afficher "✓ Analysé" serait une
        requete de trop, repetee a chaque rafraichissement.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT content_id FROM clip_analyses").fetchall()
        return {row["content_id"] for row in rows}

    def delete_analysis(self, content_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM clip_analyses WHERE content_id = ?", (content_id,))

    def list_analyses(self, limit: int = 50) -> list:
        from radar.analysis.models import ClipAnalysis
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM clip_analyses ORDER BY analyzed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            payload = _loads(row["payload"])
            if payload:
                out.append(ClipAnalysis.from_dict(payload))
        return out
