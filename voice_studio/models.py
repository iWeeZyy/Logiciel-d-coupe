"""Donnees de Voice Studio.

Un seul modele de transcription dans tout le projet : `core.models.Transcript`
(segments et mots horodates) est reutilise tel quel. `VoiceStudioProject`
n'ajoute que ce que Whisper ne sait pas -- d'ou vient la video, comment la
transcription a ete obtenue, et ce qu'on en a fait.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

from core.models import Transcript

# D'ou vient le texte. La distinction est affichee a l'utilisateur : des
# sous-titres publies par la chaine et une transcription automatique n'ont pas
# la meme fiabilite, et il a le droit de le savoir.
SOURCE_SUBTITLES = "subtitles"          # sous-titres publies
SOURCE_AUTO_CAPTIONS = "auto_captions"  # sous-titres generes par YouTube
SOURCE_WHISPER = "whisper"              # transcription locale

SOURCE_LABELS = {
    SOURCE_SUBTITLES: "Sous-titres publiés par la chaîne",
    SOURCE_AUTO_CAPTIONS: "Sous-titres générés automatiquement par YouTube",
    SOURCE_WHISPER: "Transcription locale (Faster-Whisper)",
}

STATUS_EMPTY = "empty"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# Version de la chaine de transcription. Enregistree avec chaque projet : si la
# chaine change, on saura que les anciennes transcriptions n'ont pas ete
# produites de la meme facon.
TRANSCRIPTION_PIPELINE_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TtsSettings:
    """Reglages de la synthese vocale. Aucun n'est devine : ce sont ceux que
    l'utilisateur a choisis dans l'interface."""

    voice_id: str = ""
    voice_label: str = ""
    rate: float = 1.0            # multiplicateur de vitesse
    volume: float = 1.0          # 0.0 a 1.0
    sentence_pause_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> "TtsSettings":
        data = data or {}
        known = {f.name for f in fields(TtsSettings)}
        return TtsSettings(**{k: v for k, v in data.items() if k in known})


@dataclass
class VideoSettings:
    """Reglages de la creation video. Comme TtsSettings : rien n'est devine,
    ce sont les choix faits dans l'interface, enregistres pour que rouvrir la
    video les retrouve."""

    aspect_ratio: str = "16:9"          # "16:9" ou "9:16"
    fill: str = "flou"                  # remplissage des bords en 16:9
    framing: str = "centre"             # "centre" ou "sujet" (9:16 seulement)
    # "recadrer" (garder une fenetre) ou "entier" (toute l'image, bords
    # remplis). Sans effet en 16:9, ou l'image est deja gardee entiere.
    fit: str = "recadrer"
    audio_mode: str = "remplacer"       # voir voice_studio/video_edit.py
    original_volume: float = 0.20
    narration_volume: float = 1.00
    duration_policy: str = "couper"
    subtitles_enabled: bool = True
    subtitle_style: str = ""            # "" = style par defaut de config/subtitles.json
    watermark_enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> "VideoSettings":
        data = data or {}
        known = {f.name for f in fields(VideoSettings)}
        return VideoSettings(**{k: v for k, v in data.items() if k in known})


@dataclass
class VoiceStudioProject:
    """Une video analysee et ce qui en a ete tire.

    `transcript` porte les segments ET les mots horodates : il n'y a pas de
    champ separe pour les seconds, ils sont dans les segments, la ou Whisper
    les a produits.
    """

    youtube_video_id: str
    youtube_url: str = ""
    title: str = ""
    channel: str = ""
    duration_s: Optional[float] = None
    thumbnail_url: str = ""
    language: str = ""
    transcription_status: str = STATUS_EMPTY
    transcription_source: str = ""
    whisper_model: str = ""
    pipeline_version: int = TRANSCRIPTION_PIPELINE_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    transcript: Optional[Transcript] = None
    tts_settings: TtsSettings = field(default_factory=TtsSettings)
    generated_audio: list = field(default_factory=list)
    exports: list = field(default_factory=list)

    # --- Creation video ---------------------------------------------------
    # Une video source posee sur le projet (telechargee depuis Recherche), le
    # script de narration, la voix produite et sa transcription reelle. La
    # transcription de la narration est gardee A PART de `transcript` : la
    # premiere est le texte de la video d'origine, la seconde est le minutage
    # mot a mot de la voix generee. Les confondre ferait afficher les
    # sous-titres de l'une sur l'audio de l'autre.
    source_video_path: str = ""
    source_video_title: str = ""
    narration_script: str = ""
    narration_audio_path: str = ""
    narration_transcript: Optional[Transcript] = None
    video_settings: VideoSettings = field(default_factory=VideoSettings)
    video_exports: list = field(default_factory=list)

    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript and self.transcript.segments)

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.transcription_source, "")

    @property
    def has_source_video(self) -> bool:
        return bool(self.source_video_path)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("transcript", None)
        data["transcript"] = self.transcript.to_dict() if self.transcript else None
        data.pop("narration_transcript", None)
        data["narration_transcript"] = (self.narration_transcript.to_dict()
                                        if self.narration_transcript else None)
        return data

    @staticmethod
    def from_dict(data: dict) -> "VoiceStudioProject":
        data = dict(data or {})
        raw_transcript = data.pop("transcript", None)
        raw_narration = data.pop("narration_transcript", None)
        raw_tts = data.pop("tts_settings", None)
        raw_video = data.pop("video_settings", None)
        known = {f.name for f in fields(VoiceStudioProject)}
        project = VoiceStudioProject(**{k: v for k, v in data.items()
                                        if k in known and k != "youtube_video_id"},
                                     youtube_video_id=data.get("youtube_video_id", ""))
        project.transcript = Transcript.from_dict(raw_transcript) if raw_transcript else None
        project.narration_transcript = (Transcript.from_dict(raw_narration)
                                        if raw_narration else None)
        project.tts_settings = TtsSettings.from_dict(raw_tts)
        project.video_settings = VideoSettings.from_dict(raw_video)
        return project
