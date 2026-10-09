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
_YT = "https://www.youtube.com/feeds/videos.xml?channel_id="
TRAILER_FILTER = "bande[- ]?annonce|teaser|trailer"

DEFAULT_SOURCES = (
    NewsSource(key="vgc", label="VGC",
              feed_url="https://www.videogameschronicle.com/feed/"),
    NewsSource(key="ign_fr", label="IGN France",
              feed_url="https://fr.ign.com/feed.xml"),
    NewsSource(key="jeuxvideo", label="Jeuxvideo.com",
              feed_url="https://www.jeuxvideo.com/rss/rss.xml"),
    NewsSource(key="gamekult", label="Gamekult",
              feed_url="https://www.gamekult.com/feed.xml"),
    # Aucun flux RSS disponible sur ce site : lu depuis sa page d'actualites.
    NewsSource(key="breakflip", label="Breakflip",
              feed_url="https://www.breakflip.com/actualites/", kind="breakflip"),
    # Cinema & series -- flux verifies le 2026-10-07 (Premiere et Le Film
    # Francais n'exposent pas de flux RSS exploitable).
    # featured_url : page dont l'ordre dit ce qui est « a la une » (verifie
    # le 2026-10-09) -- ces articles passent en tete du fil.
    NewsSource(key="allocine", label="AlloCiné",
              feed_url="https://www.allocine.fr/rss/news.xml", theme="cinema",
              featured_url="https://www.allocine.fr/"),
    NewsSource(key="ecranlarge", label="Écran Large",
              feed_url="https://www.ecranlarge.com/rss/", theme="cinema",
              featured_url="https://www.ecranlarge.com/"),
    NewsSource(key="cinechronicle", label="CinéChronicle",
              feed_url="https://www.cinechronicle.com/feed/", theme="cinema",
              featured_url="https://www.cinechronicle.com/"),
    NewsSource(key="numerama_cinema", label="Numerama Ciné",
              feed_url="https://www.numerama.com/pop-culture/cinema/feed/", theme="cinema",
              featured_url="https://www.numerama.com/pop-culture/cinema/"),
    # Bandes-annonces : flux des chaines YouTube (verifies le 2026-10-08).
    # FilmsActu ne publie que des bandes-annonces ; les studios publient
    # aussi extraits et Shorts, d'ou le filtre sur le titre.
    # Top bandes-annonces AlloCine, dans l'ordre du top : en tete du fil.
    NewsSource(key="allocine_top_trailers", label="AlloCiné · Top bandes-annonces",
              feed_url="https://www.allocine.fr/video/bandes-annonces/", kind="allocine_trailers",
              theme="trailers"),
    NewsSource(key="yt_filmsactu", label="FilmsActu",
              feed_url=_YT + "UC_i8X3p8oZNaik8X513Zn1Q", theme="trailers"),
    NewsSource(key="yt_allocine", label="AlloCiné (YouTube)",
              feed_url=_YT + "UCwXc5G-RAKu9oC2yO4cXmuw", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_netflix_fr", label="Netflix France",
              feed_url=_YT + "UCroNr00O68n25IqSNapMK8w", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_prime_video_fr", label="Prime Video France",
              feed_url=_YT + "UC4YtyYW5RpGBgQqChMyjfIg", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_warner_fr", label="Warner Bros. France",
              feed_url=_YT + "UCZ0o1IeuSSceEixZbSATWtw", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_universal_fr", label="Universal Pictures France",
              feed_url=_YT + "UChbOoefuXz17nioY3sgvZxQ", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_sony_fr", label="Sony Pictures France",
              feed_url=_YT + "UC2I1RkNIDzTCAWB8M7BbAQw", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_disney_fr", label="Disney FR",
              feed_url=_YT + "UCakQLdwrxuo0KhJ49Sq2csA", theme="trailers", title_filter=TRAILER_FILTER),
    NewsSource(key="yt_gaumont", label="Gaumont",
              feed_url=_YT + "UCKnyzIyfL5Zj7cHhcC6bTyg", theme="trailers", title_filter=TRAILER_FILTER),
)

# Fils thematiques connus, dans l'ordre d'affichage du selecteur de l'onglet
# News -- une valeur inconnue dans la config retombe sur "gaming".
THEMES = ("gaming", "cinema", "trailers")
THEME_LABELS = {"gaming": "🎮 Gaming", "cinema": "🎬 Cinéma & séries",
                "trailers": "🎞️ Bandes-annonces"}

# Lecteurs connus (voir feed_fetcher.fetch_source) -- une valeur inconnue
# dans la config retombe sur "rss" plutot que d'ecarter la source.
KINDS = ("rss", "breakflip", "allocine_trailers")


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
        kind = str(entry.get("kind") or "rss").strip().lower()
        if kind not in KINDS:
            kind = "rss"
        theme = str(entry.get("theme") or "gaming").strip().lower()
        if theme not in THEMES:
            theme = "gaming"
        title_filter = str(entry.get("title_filter") or "").strip()
        featured_url = str(entry.get("featured_url") or "").strip()
        sources.append(NewsSource(key=key, label=label, feed_url=feed_url, kind=kind, theme=theme,
                                  title_filter=title_filter, featured_url=featured_url))

    return sources or list(DEFAULT_SOURCES)
