"""Vocabulaire de la publication.

Une publication est decrite en deux temps : un BROUILLON, entierement local et
modifiable, puis un ENREGISTREMENT de ce qui a ete tente et de ce qui en est
sorti. Les deux sont separes parce qu'ils ne vivent pas au meme rythme -- on
retouche un brouillon autant qu'on veut, on n'efface pas un historique.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

PLATFORM_INSTAGRAM = "instagram"
PLATFORM_TIKTOK = "tiktok"
PLATFORMS = (PLATFORM_INSTAGRAM, PLATFORM_TIKTOK)

PLATFORM_LABELS = {
    PLATFORM_INSTAGRAM: "Instagram",
    PLATFORM_TIKTOK: "TikTok",
}

PLATFORM_ICONS = {
    PLATFORM_INSTAGRAM: "📸",
    PLATFORM_TIKTOK: "🎵",
}

# Etats d'une publication (section 14). Ils decrivent ce qui s'est reellement
# passe : "publie" n'est jamais ecrit sans reponse de la plateforme.
STATUS_PENDING = "en_attente"
STATUS_RUNNING = "en_cours"
STATUS_PUBLISHED = "publie"
STATUS_FAILED = "echec"
STATUS_EXPORTED = "exporte"

STATUS_LABELS = {
    STATUS_PENDING: "🕐 En attente",
    STATUS_RUNNING: "⏳ Publication",
    STATUS_PUBLISHED: "✓ Publié",
    STATUS_FAILED: "❌ Échec",
    STATUS_EXPORTED: "📥 Exporté",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class PublicationDraft:
    """Ce qui sera envoye, plateforme par plateforme.

    Les legendes sont SEPAREES et non partagees : une legende de Reel et une
    legende TikTok ne se ressemblent pas, et forcer le meme texte des deux
    cotes obligerait a choisir le plus mauvais compromis (section 5).
    """

    clip_path: str = ""
    cover_path: str = ""
    content_id: str = ""          # cle de l'opportunite du Radar, si elle vient de la
    clip_title: str = ""
    creator_label: str = ""
    duration_s: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None

    instagram_caption: str = ""
    tiktok_caption: str = ""
    hashtags: list = field(default_factory=list)
    mention: str = ""             # jamais devine : voir publishing/captions.py

    platforms: list = field(default_factory=list)

    def caption_for(self, platform: str) -> str:
        return (self.instagram_caption if platform == PLATFORM_INSTAGRAM
                else self.tiktok_caption)

    def full_text_for(self, platform: str) -> str:
        """Legende + mention + hashtags, tel que ce sera publie."""
        parts = [self.caption_for(platform).strip()]
        if self.mention:
            parts.append(self.mention.strip())
        if self.hashtags:
            parts.append(" ".join(self.hashtags))
        return "\n\n".join(part for part in parts if part).strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PublicationDraft":
        return PublicationDraft(**_known(PublicationDraft, d))


@dataclass
class PublicationRecord:
    """Une tentative de publication, sur UNE plateforme.

    Une ligne par plateforme et non une par envoi : les plateformes reussissent
    et echouent independamment, et un echec TikTok ne doit pas effacer une
    reussite Instagram (section 13).
    """

    id: str = ""
    clip_path: str = ""
    content_id: str = ""
    platform: str = ""
    account: str = ""
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: str = ""
    url: str = ""
    caption: str = ""
    hashtags: list = field(default_factory=list)
    error: str = ""
    export_dir: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def platform_label(self) -> str:
        return PLATFORM_LABELS.get(self.platform, self.platform)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PublicationRecord":
        return PublicationRecord(**_known(PublicationRecord, d))
