"""Legendes et hashtags, tires de l'analyse deja faite (sections 4, 5, 6).

RIEN N'EST RE-ANALYSE. Le clip a deja ete ecoute, resume et decrit par
radar/analysis/ ; ce module ne fait que choisir, parmi ce qui existe, ce qui va
dans une legende Instagram et ce qui va dans une legende TikTok. Relancer une
analyse pour publier ferait payer deux fois le meme travail.

Les deux legendes different par construction. Un Reel supporte un texte de
quelques lignes, TikTok recompense une accroche courte lue en une seconde. Les
forcer a etre identiques reviendrait a choisir le plus mauvais compromis.
"""
from __future__ import annotations

import re

from radar.analysis.models import ClipAnalysis

# Hashtags de PROVENANCE, ajoutes a ceux tires du contenu. Ils decrivent d'ou
# vient le clip, ce qui est verifiable, et non ce qu'il raconte, qui lui n'est
# jamais invente.
TWITCH_HASHTAGS = ("#Twitch", "#TwitchClips", "#Clips")

MAX_HASHTAGS = 8
# Limites publiques des deux plateformes au moment de l'ecriture. Elles servent
# a AVERTIR, pas a garantir une acceptation : seule la plateforme decide.
INSTAGRAM_CAPTION_MAX = 2200
TIKTOK_CAPTION_MAX = 2200

_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def instagram_caption(analysis: ClipAnalysis) -> str:
    """Legende de Reel : l'accroche, puis ce qui la remet dans son contexte."""
    lines = []
    hook = _clean(analysis.short_description)
    if hook:
        lines.append(hook)
    body = _clean(analysis.description) or _clean(analysis.summary)
    if body and body != hook:
        lines.append(body)
    return "\n\n".join(lines)[:INSTAGRAM_CAPTION_MAX]


def tiktok_caption(analysis: ClipAnalysis) -> str:
    """Legende TikTok : une seule phrase, celle qui donne envie de regarder.

    On prend la description courte, et le titre punchy si elle manque : c'est
    la phrase la plus forte reellement prononcee dans le clip.
    """
    caption = _clean(analysis.short_description)
    if not caption:
        caption = _clean(analysis.title_punchy) or _clean(analysis.title_direct)
    return caption[:TIKTOK_CAPTION_MAX]


def hashtags_for(analysis: ClipAnalysis, is_twitch: bool = True) -> list:
    """Hashtags du contenu, completes par ceux de la provenance.

    Ceux du contenu viennent de l'analyse (mots reellement prononces, nom du
    createur, jeu). On n'en fabrique pas d'autres : une longue liste de
    hashtags generiques n'apporte rien et dilue les vrais.
    """
    out: list[str] = []
    for tag in analysis.hashtags or []:
        tag = tag if tag.startswith("#") else f"#{tag}"
        if tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    if is_twitch:
        for tag in TWITCH_HASHTAGS:
            if tag.lower() not in {t.lower() for t in out}:
                out.append(tag)
    return out[:MAX_HASHTAGS]


def normalize_mention(value: str) -> str:
    """Mention utilisable, ou chaine vide.

    NE JAMAIS INVENTER UN COMPTE (section 6). Rien, dans le Radar, ne dit quel
    est le compte Instagram ou TikTok d'un streamer : son pseudo Twitch n'est
    pas une adresse sur un autre reseau, et publier "@pseudo" au hasard
    mentionne quelqu'un qui n'a rien demande, parfois une personne sans aucun
    rapport. Cette fonction ne fait donc que VALIDER ce que l'utilisateur a
    saisi lui-meme.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if not _HANDLE_RE.match(value):
        return ""
    return value if value.startswith("@") else f"@{value}"


def build_draft(analysis: ClipAnalysis, clip_path: str, *, cover_path: str = "",
                width=None, height=None, duration_s=None, mention: str = ""):
    """Brouillon complet a partir d'une analyse deja faite."""
    from publishing.models import PublicationDraft

    is_twitch = (analysis.platform or "").lower() == "twitch"
    return PublicationDraft(
        clip_path=clip_path,
        cover_path=cover_path,
        content_id=analysis.content_id,
        clip_title=analysis.clip_title,
        creator_label=analysis.creator_label,
        duration_s=duration_s if duration_s is not None else analysis.duration_s,
        width=width,
        height=height,
        instagram_caption=instagram_caption(analysis),
        tiktok_caption=tiktok_caption(analysis),
        hashtags=hashtags_for(analysis, is_twitch=is_twitch),
        mention=normalize_mention(mention),
    )
