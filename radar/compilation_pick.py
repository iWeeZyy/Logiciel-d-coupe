"""Choix des clips d'une compilation : les plus vus, sans doublon.

Demande de l'utilisateur : « les clips les plus vus, mais que 2 mêmes clips
ne se retrouvent pas dans la vidéo ». Sur Twitch, un meme moment est souvent
clippe par plusieurs spectateurs : des clips differents (autres identifiants,
autres titres) qui montrent la meme scene. Deux clips sont tenus pour le
MEME MOMENT quand :
1. ils ont le meme identifiant (le meme clip vu deux fois) ;
2. ils viennent de la meme VOD et leurs passages s'y chevauchent (position
   `vod_offset` + duree, donnees par Twitch) ;
3. sans VOD (supprimee, ou jamais gardee par le streamer), meme streamer et
   crees a moins de 2 minutes d'intervalle -- c'est le cas typique de deux
   spectateurs qui clippent la meme action ; ou meme titre normalise.
Le plus vu des deux est garde, l'autre ecarte avec la raison. Module pur.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_CLOSE_CREATION_S = 120
_OVERLAP_MARGIN_S = 2.0


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _when(value: str):
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def same_moment(a, b) -> str:
    """Raison pour laquelle `a` et `b` montrent le meme moment, ou ""."""
    if a.content_id == b.content_id:
        return "même clip"
    if a.creator_key != b.creator_key:
        return ""
    extra_a, extra_b = a.extra or {}, b.extra or {}
    vod_a, vod_b = extra_a.get("source_video_id"), extra_b.get("source_video_id")
    off_a, off_b = extra_a.get("vod_offset"), extra_b.get("vod_offset")
    if vod_a and vod_a == vod_b and off_a is not None and off_b is not None:
        end_a = off_a + (a.duration_s or 30)
        end_b = off_b + (b.duration_s or 30)
        overlap = min(end_a, end_b) - max(off_a, off_b)
        return "même passage du live" if overlap > -_OVERLAP_MARGIN_S else ""
    title_a, title_b = _fold(a.title), _fold(b.title)
    if title_a and title_a == title_b:
        return "même titre"
    if not (vod_a and vod_a == vod_b):
        when_a, when_b = _when(a.published_at), _when(b.published_at)
        if when_a and when_b and abs((when_a - when_b).total_seconds()) <= _CLOSE_CREATION_S:
            return "clippé au même moment"
    return ""


def pick_top(opportunities, count: int):
    """(clips retenus, [(clip ecarte, clip garde, raison)]) : les `count`
    plus vus, un seul clip par moment. Les directs et ce qui n'est pas un
    clip sont ignores."""
    candidates = [o for o in opportunities
                  if getattr(o, "kind", "") == "clip" and not getattr(o, "is_live", False)]
    candidates.sort(key=lambda o: o.view_count if o.view_count is not None else -1, reverse=True)
    kept, skipped = [], []
    for candidate in candidates:
        if len(kept) >= count:
            break
        duplicate = next(((k, r) for k in kept if (r := same_moment(candidate, k))), None)
        if duplicate:
            skipped.append((candidate, duplicate[0], duplicate[1]))
        else:
            kept.append(candidate)
    return kept, skipped
