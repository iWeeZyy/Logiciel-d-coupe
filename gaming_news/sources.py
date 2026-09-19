"""Sources du Radar Gaming News, lues depuis config/gaming_news.json.

Meme raisonnement que watermark.choices() : une liste declaree dans la config
prend le pas sur le defaut integre, et un defaut integre existe pour que
l'application fonctionne sans configuration personnalisee.
"""
from __future__ import annotations

from gaming_news.models import NewsSource

# Repli si config/gaming_news.json est absent ou vide. Les URLs de flux n'ont
# PAS pu etre verifiees par un acces reseau reel depuis l'environnement de
# developpement (voir config/gaming_news.json, _comment) -- a corriger dans le
# fichier de config, jamais ici, si un site ne renvoie plus rien.
DEFAULT_SOURCES = (
    NewsSource(key="vgc", label="VGC",
              feed_url="https://www.videogameschronicle.com/feed/"),
    NewsSource(key="ign_fr", label="IGN France",
              feed_url="https://fr.ign.com/feed.xml"),
    NewsSource(key="jeuxvideo", label="Jeuxvideo.com",
              feed_url="https://www.jeuxvideo.com/rss/rss.xml"),
    NewsSource(key="gamekult", label="Gamekult",
              feed_url="https://www.gamekult.com/feed.xml"),
)


def load_sources(config: dict | None = None) -> list[NewsSource]:
    """Les sources a scanner, dans l'ordre du catalogue.

    Une entree malformee (cle ou URL de flux manquante) est ecartee plutot que
    de faire echouer tout le chargement -- une faute de frappe dans la config
    ne doit pas priver l'utilisateur des autres sources valides."""
    config = config or {}
    declared = config.get("sources")
    if not isinstance(declared, list) or not declared:
        return list(DEFAULT_SOURCES)

    sources: list[NewsSource] = []
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        feed_url = str(entry.get("feed_url") or "").strip()
        if not (key and feed_url):
            continue
        label = str(entry.get("label") or key).strip()
        sources.append(NewsSource(key=key, label=label, feed_url=feed_url))

    return sources or list(DEFAULT_SOURCES)
