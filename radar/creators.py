"""Gestion de la liste des createurs surveilles (sections 2 a 5, et 2 a 3 du
Radar Twitch).

Regles qui ne sont pas negociables cote produit, et donc appliquees ici plutot
que dans l'interface :

- PAS DE DOUBLON. La cle est "plateforme:identifiant_de_plateforme", et non le
  pseudo : un createur qui se renomme ne doit ni creer un doublon ni perdre son
  historique.
- DESACTIVER PLUTOT QUE SUPPRIMER. Retirer un createur de la surveillance ne
  supprime jamais les donnees deja collectees (section 4). La suppression
  n'enleve que la ligne de la liste ; opportunites, releves et favoris restent.
- AJOUT TOUJOURS CONFIRME. Ce module RESOUT une chaine et renvoie un candidat ;
  il n'ajoute rien de lui-meme. La confirmation est a l'appelant (section 3, et
  section 17 : ne jamais ajouter automatiquement un createur).

Module sans reseau : la resolution d'une chaine est deleguee a l'adaptateur de
plateforme, injecte par l'appelant. C'est ce qui permet de tester toute cette
logique sans APIs.
"""
from __future__ import annotations

from dataclasses import dataclass

from radar.models import PRIORITIES, PRIORITY_NORMAL, Creator, utc_now_iso

MAX_NOTE_LENGTH = 500


class CreatorAlreadyWatched(Exception):
    """Le createur est deja dans la liste (section 3)."""

    def __init__(self, creator: Creator):
        self.creator = creator
        super().__init__(f"⚠️ Ce créateur est déjà surveillé : {creator.label}")


@dataclass(frozen=True)
class ResolvedCandidate:
    """Chaine trouvee, en attente de confirmation par l'utilisateur."""

    creator: Creator
    already_watched: bool = False

    def summary(self) -> str:
        parts = [self.creator.display_name or self.creator.platform_id]
        if self.creator.username:
            parts.append(f"@{self.creator.username}")
        if self.creator.follower_count is not None:
            parts.append(f"{self.creator.follower_count:,}".replace(",", " ") + " abonnés")
        return "  •  ".join(parts)


class CreatorManager:
    """Liste des createurs, au-dessus de RadarStore."""

    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------ lecture
    def all(self, platform: str | None = None, active_only: bool = False) -> list[Creator]:
        return self.store.list_creators(platform=platform, active_only=active_only)

    def get(self, key: str) -> Creator | None:
        return self.store.get_creator(key)

    def exists(self, platform: str, platform_id: str) -> bool:
        return self.store.get_creator(f"{platform}:{platform_id}") is not None

    # -------------------------------------------------------------- ajout
    def resolve(self, platform_adapter, query: str) -> ResolvedCandidate | None:
        """Cherche une chaine et renvoie un candidat, SANS l'ajouter.

        `query` accepte indifferemment un nom, un @handle ou une URL : c'est
        l'adaptateur de plateforme qui sait les distinguer, parce que la forme
        des URL n'est pas la meme d'une plateforme a l'autre.
        """
        creator = platform_adapter.resolve_creator(query)
        if creator is None:
            return None
        return ResolvedCandidate(
            creator=creator,
            already_watched=self.exists(creator.platform, creator.platform_id),
        )

    def add(self, creator: Creator, priority: str = PRIORITY_NORMAL) -> Creator:
        """Ajoute apres confirmation. Leve si deja surveille."""
        existing = self.store.get_creator(creator.key)
        if existing is not None:
            raise CreatorAlreadyWatched(existing)
        creator.priority = priority if priority in PRIORITIES else PRIORITY_NORMAL
        creator.added_at = creator.added_at or utc_now_iso()
        creator.active = True
        self.store.upsert_creator(creator)
        return creator

    # ---------------------------------------------------------- modification
    def set_priority(self, key: str, priority: str) -> Creator | None:
        if priority not in PRIORITIES:
            return None
        creator = self.store.get_creator(key)
        if creator is None:
            return None
        creator.priority = priority
        self.store.upsert_creator(creator)
        return creator

    def set_active(self, key: str, active: bool) -> Creator | None:
        """Active ou suspend la surveillance. Les donnees deja collectees ne
        sont jamais touchees : c'est tout l'interet d'avoir un etat plutot
        qu'une suppression."""
        creator = self.store.get_creator(key)
        if creator is None:
            return None
        creator.active = bool(active)
        self.store.upsert_creator(creator)
        return creator

    def rename(self, key: str, display_name: str) -> Creator | None:
        creator = self.store.get_creator(key)
        if creator is None:
            return None
        creator.display_name = display_name.strip() or creator.display_name
        self.store.upsert_creator(creator)
        return creator

    def reorder(self, keys: list[str]) -> None:
        self.store.set_creator_positions(keys)

    def remove(self, key: str) -> None:
        """Retire de la liste. L'historique reste consultable (section 4)."""
        self.store.delete_creator(key)

    def mark_scanned(self, key: str) -> None:
        creator = self.store.get_creator(key)
        if creator is not None:
            creator.last_scan_at = utc_now_iso()
            self.store.upsert_creator(creator)
