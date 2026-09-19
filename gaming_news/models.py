"""Vocabulaire du Radar Gaming News : une source (un site de presse gaming
suivi via son flux RSS/Atom) et une Article (une actualite telle que
recuperee de ce flux).

Delibrement separe de radar/models.py (Creator/Opportunity) : une actualite
de presse n'est pas un contenu produit par un createur suivi, rien du
scoring/tendance du Radar existant ne s'y applique.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class NewsSource:
    """Un site de presse gaming suivi, identifie par son flux."""

    key: str
    label: str
    feed_url: str


@dataclass(frozen=True)
class Article:
    """Une actualite telle que recuperee d'un flux RSS/Atom.

    `feed_image_url` est l'image que le flux fournit LUI-MEME, quand il en
    fournit une (media:content, media:thumbnail, enclosure) -- un repli utile
    quand l'article HTML n'expose aucune image extractible (paywall, page
    rendue en JavaScript), mais jamais garanti ni prioritaire : og:image reste
    prefere quand il existe (voir news_story/image_fetcher.py)."""

    source_key: str
    source_label: str
    title: str
    url: str
    published_at: str = ""  # tel que fourni par le flux, jamais reformate ici
    summary: str = ""
    feed_image_url: str = ""

    @property
    def article_id(self) -> str:
        """Identifiant stable pour la deduplication et le cache d'image.

        Jamais l'URL brute telle quelle comme cle affichee/loggee : deux flux
        peuvent republier le meme article avec des parametres de tracking
        differents dans l'URL, le hash absorbe cette variation."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]
