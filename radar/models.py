"""Vocabulaire commun aux plateformes.

Une seule notion de createur et d'opportunite pour YouTube et Twitch : c'est ce
qui permet a l'onglet "Tous" de comparer les deux sans traduire, et a
l'apprentissage de raisonner sur les deux a la fois.

Le point le plus important de ce fichier est Snapshot. Aucune API, ni YouTube
ni Twitch, ne renvoie une vitesse de progression : elles renvoient un compteur a
l'instant present. Toute detection de tendance repose donc sur NOS PROPRES
releves successifs. Consequence assumee : le premier scan d'un contenu ne peut
jamais montrer de tendance -- il n'y a rien a comparer. Le systeme le dit au
lieu d'afficher une progression nulle, qui serait fausse.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITCH = "twitch"
PLATFORMS = (PLATFORM_YOUTUBE, PLATFORM_TWITCH)

PRIORITY_HIGH = "high"
PRIORITY_NORMAL = "normal"
PRIORITY_LOW = "low"
PRIORITIES = (PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW)

PRIORITY_LABELS = {
    PRIORITY_HIGH: "🔥 Priorité élevée",
    PRIORITY_NORMAL: "⭐ Priorité normale",
    PRIORITY_LOW: "👀 Priorité faible",
}
# Poids du classement. Volontairement resserres : la priorite doit INFLUENCER
# l'ordre, pas l'ecraser. Un streamer en priorite faible qui produit un contenu
# exceptionnel doit pouvoir passer devant (section 11 du Radar Twitch).
PRIORITY_WEIGHTS = {PRIORITY_HIGH: 1.10, PRIORITY_NORMAL: 1.0, PRIORITY_LOW: 0.92}

# Types d'opportunite. Une seule enumeration pour les deux plateformes : un
# Short YouTube et un clip Twitch se comparent, un live ne se compare qu'a
# lui-meme.
KIND_SHORT = "short"
KIND_CLIP = "clip"
KIND_VOD = "vod"
KIND_LIVE = "live"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def capture_stamp() -> str:
    """Horodatage d'un releve, a la milliseconde.

    A la seconde pres, deux releves du meme contenu pris dans la meme seconde
    partageraient la meme cle primaire et le second ecraserait le premier, sans
    erreur -- une perte de donnee silencieuse, et precisement la donnee dont
    depend toute la detection de tendance. Le reste du module reste a la
    seconde : c'est ici, et seulement ici, que la precision compte.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class Creator:
    """Un createur surveille, sur une plateforme donnee.

    `platform_id` est l'identifiant stable de la plateforme (channelId YouTube,
    user id Twitch) et non le nom affiche : un createur peut se renommer, son
    identifiant non. C'est lui qui sert de cle, ce qui evite qu'un changement de
    pseudo cree un doublon ou perde l'historique.
    """

    platform: str
    platform_id: str
    username: str = ""          # @handle YouTube, login Twitch
    display_name: str = ""
    url: str = ""
    avatar_url: str = ""
    follower_count: Optional[int] = None
    priority: str = PRIORITY_NORMAL
    active: bool = True
    added_at: str = field(default_factory=utc_now_iso)
    last_scan_at: str = ""
    # Donnee propre a la plateforme (playlist d'uploads YouTube, categorie
    # Twitch...). Un dict plutot que des colonnes par plateforme : ajouter une
    # troisieme plateforme ne doit pas modifier la table des createurs.
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.platform_id}"

    @property
    def label(self) -> str:
        return self.display_name or self.username or self.platform_id

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Creator":
        return Creator(**_known(Creator, d))


@dataclass
class Snapshot:
    """Statistiques d'un contenu a un instant donne.

    C'est la brique de la detection de tendance : deux instantanes separes dans
    le temps donnent une vitesse. Un seul n'en donne aucune.
    """

    content_id: str
    captured_at: str = field(default_factory=capture_stamp)
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    viewer_count: Optional[int] = None   # Twitch, spectateurs en direct

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        return Snapshot(**_known(Snapshot, d))


@dataclass
class Opportunity:
    """Un contenu detecte : Short YouTube, clip ou live Twitch.

    `content_id` est prefixe par la plateforme pour la meme raison que la cle
    d'un createur : deux plateformes peuvent utiliser le meme identifiant.
    """

    platform: str
    content_id: str
    kind: str
    creator_key: str
    title: str = ""
    url: str = ""
    thumbnail_url: str = ""
    published_at: str = ""
    duration_s: Optional[int] = None
    category: str = ""
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    viewer_count: Optional[int] = None
    discovered_at: str = field(default_factory=utc_now_iso)
    is_live: bool = False
    # Renseigne par radar/scoring.py, jamais par la plateforme.
    radar_score: Optional[float] = None
    score_breakdown: dict = field(default_factory=dict)
    trend: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.content_id}"

    def snapshot(self) -> Snapshot:
        return Snapshot(
            content_id=self.key,
            view_count=self.view_count,
            like_count=self.like_count,
            comment_count=self.comment_count,
            viewer_count=self.viewer_count,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Opportunity":
        return Opportunity(**_known(Opportunity, d))


@dataclass
class ScanResult:
    """Bilan d'un scan, pour l'historique et le tableau de bord."""

    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = ""
    platforms: tuple = ()
    creators_scanned: int = 0
    opportunities_found: int = 0
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["platforms"] = list(self.platforms)
        return d
