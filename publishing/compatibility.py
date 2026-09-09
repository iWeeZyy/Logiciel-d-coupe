"""Controle du fichier avant publication (sections 18 et 19).

Ce module REGARDE le fichier et dit ce qu'il voit. Il ne garantit rien : seule
la plateforme decide d'accepter une video. Les limites ci-dessous sont celles
publiees par Instagram et TikTok au moment de l'ecriture, et elles changent ;
elles servent a prevenir avant un envoi qui echouerait, pas a promettre qu'il
reussira.

Consequence assumee : un avertissement n'empeche jamais de publier. Une regle
devenue obsolete bloquerait alors un envoi parfaitement valide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from publishing.models import PLATFORM_INSTAGRAM, PLATFORM_TIKTOK

# Limites par plateforme. Volontairement prudentes et nommees, pour qu'une mise
# a jour se fasse ici et nulle part ailleurs.
LIMITS = {
    PLATFORM_INSTAGRAM: {
        "min_duration_s": 3.0,
        "max_duration_s": 15 * 60.0,
        "max_bytes": 1024 * 1024 * 1024,
        "video_codecs": ("h264", "hevc"),
        "audio_codecs": ("aac",),
        "containers": (".mp4", ".mov"),
        "min_width": 540,
    },
    PLATFORM_TIKTOK: {
        "min_duration_s": 3.0,
        "max_duration_s": 10 * 60.0,
        "max_bytes": 4 * 1024 * 1024 * 1024,
        "video_codecs": ("h264", "hevc"),
        "audio_codecs": ("aac",),
        "containers": (".mp4", ".mov", ".webm"),
        "min_width": 360,
    },
}

# Format attendu par les deux : vertical. Un clip horizontal part quand meme,
# la plateforme ajoutera des bandes -- c'est un avertissement, pas un refus.
VERTICAL_RATIO_MAX = 1.0


@dataclass(frozen=True)
class MediaInfo:
    exists: bool = False
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    size_bytes: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    has_audio: bool = False
    suffix: str = ""

    @property
    def is_vertical(self) -> bool:
        return bool(self.height) and self.width <= self.height


@dataclass(frozen=True)
class CheckResult:
    """Le bilan lu par l'interface : une ligne par controle."""

    platform: str
    info: MediaInfo
    passed: tuple = field(default_factory=tuple)     # libelles verts
    warnings: tuple = field(default_factory=tuple)   # libelles orange

    @property
    def ready(self) -> bool:
        """Pret veut dire "rien d'anormal detecte", pas "sera accepte"."""
        return not self.warnings


def probe(path: str | Path) -> MediaInfo:
    """Lit le fichier avec ffprobe. Ne leve jamais.

    Un fichier illisible rend un MediaInfo vide plutot qu'une exception : cette
    fonction sert a AFFICHER un bilan, et une fenetre qui plante en preparant
    une publication est pire qu'un bilan incomplet.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return MediaInfo(exists=False, suffix=file_path.suffix.lower())

    try:
        from video.ffmpeg_utils import probe as ffprobe

        data = ffprobe(str(file_path))
    except Exception:
        return MediaInfo(exists=True, size_bytes=file_path.stat().st_size,
                         suffix=file_path.suffix.lower())

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}

    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    return MediaInfo(
        exists=True,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_s=duration,
        size_bytes=file_path.stat().st_size,
        video_codec=str(video.get("codec_name") or "").lower(),
        audio_codec=str((audio or {}).get("codec_name") or "").lower(),
        has_audio=audio is not None,
        suffix=file_path.suffix.lower(),
    )


def check(path: str | Path, platform: str, info: MediaInfo | None = None) -> CheckResult:
    """Bilan de compatibilite d'un fichier pour une plateforme."""
    info = info if info is not None else probe(path)
    limits = LIMITS.get(platform, LIMITS[PLATFORM_TIKTOK])
    passed: list[str] = []
    warnings: list[str] = []

    if not info.exists:
        return CheckResult(platform=platform, info=info,
                           warnings=("Le fichier vidéo est introuvable.",))

    if info.suffix in limits["containers"]:
        passed.append(f"Format de fichier {info.suffix} accepté")
    else:
        warnings.append(f"Format de fichier {info.suffix or 'inconnu'} : "
                        + " ou ".join(limits["containers"]) + " est attendu.")

    if info.width and info.height:
        if info.is_vertical:
            passed.append(f"Vidéo verticale {info.width} × {info.height}")
        else:
            warnings.append(f"Vidéo horizontale {info.width} × {info.height} : "
                            "la plateforme ajoutera des bandes.")
        if info.width < limits["min_width"]:
            warnings.append(f"Largeur de {info.width} px, {limits['min_width']} px minimum.")
    else:
        warnings.append("Définition illisible.")

    if info.duration_s:
        if limits["min_duration_s"] <= info.duration_s <= limits["max_duration_s"]:
            passed.append(f"Durée de {info.duration_s:.0f} s")
        elif info.duration_s < limits["min_duration_s"]:
            warnings.append(f"Durée de {info.duration_s:.1f} s, "
                            f"{limits['min_duration_s']:.0f} s minimum.")
        else:
            warnings.append(f"Durée de {info.duration_s / 60:.0f} min, "
                            f"{limits['max_duration_s'] / 60:.0f} min maximum.")

    if info.video_codec:
        if info.video_codec in limits["video_codecs"]:
            passed.append(f"Codec vidéo {info.video_codec}")
        else:
            warnings.append(f"Codec vidéo {info.video_codec} : "
                            + " ou ".join(limits["video_codecs"]) + " est attendu.")

    if info.has_audio:
        if not info.audio_codec or info.audio_codec in limits["audio_codecs"]:
            passed.append("Piste audio présente")
        else:
            warnings.append(f"Codec audio {info.audio_codec} : "
                            + " ou ".join(limits["audio_codecs"]) + " est attendu.")
    else:
        # Une video muette part quand meme -- certains montages n'ont pas de son
        # -- mais sur ces deux plateformes c'est presque toujours une erreur.
        warnings.append("Aucune piste audio dans ce fichier.")

    if info.size_bytes:
        if info.size_bytes <= limits["max_bytes"]:
            passed.append(f"Taille de {info.size_bytes / (1024 * 1024):.0f} Mo")
        else:
            warnings.append(f"Fichier de {info.size_bytes / (1024 * 1024):.0f} Mo, "
                            f"{limits['max_bytes'] / (1024 * 1024):.0f} Mo maximum.")

    return CheckResult(platform=platform, info=info,
                       passed=tuple(passed), warnings=tuple(warnings))


def needs_reencoding(result: CheckResult) -> bool:
    """Faut-il proposer de reencoder ?

    Seulement pour ce qu'un reencodage repare vraiment : conteneur, codecs,
    orientation. Une video trop longue ou trop lourde ne se corrige pas en
    changeant de codec, et proposer un bouton qui ne resout rien fait perdre du
    temps (section 18 : ne pas reencoder si le fichier est deja compatible).
    """
    reparables = ("Format de fichier", "Codec vidéo", "Codec audio", "Vidéo horizontale")
    return any(w.startswith(reparables) for w in result.warnings)
