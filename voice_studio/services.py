"""Enchainement complet : une URL en entree, un projet transcrit en sortie.

Cette couche ne connait pas Qt. L'interface lui passe une demande et une
fonction de compte rendu ; elle appelle les modules dans l'ordre et rend un
projet. C'est ce qui rend le parcours testable sans ouvrir une fenetre, et ce
qui garde le fil graphique libre : gui/voice_studio/workers.py se contente
d'appeler `run_analysis` dans un QThread.

Ordre des sources, du plus fiable au plus couteux :
1. les sous-titres publies par la chaine ;
2. les sous-titres generes automatiquement par YouTube ;
3. la transcription locale par Faster-Whisper.

L'etape 3 demande la piste audio, donc l'accord explicite de l'utilisateur.
Sans cet accord, les etapes 1 et 2 restent possibles : elles ne telechargent
que du texte deja publie avec la video.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from utils.errors import CancelledError, RightsNotConfirmedError
from voice_studio import store, transcription
from voice_studio.models import (
    SOURCE_AUTO_CAPTIONS,
    SOURCE_SUBTITLES,
    SOURCE_WHISPER,
    STATUS_DONE,
    VoiceStudioProject,
)
from voice_studio.subtitle_source import SubtitlesUnavailable, fetch_transcript
from voice_studio.transcript import coverage
from voice_studio.youtube_source import fetch_info, parse_video_id, watch_url

logger = get_logger()

STEP_URL = "🔗 Vérification de l'adresse"
STEP_VIDEO = "🎬 Identification de la vidéo"
STEP_SUBTITLES = "📝 Recherche d'une transcription accessible"
STEP_AUDIO = "🎙️ Préparation de l'audio"
STEP_TRANSCRIBE = "🧠 Transcription"
STEP_FINALISE = "✅ Finalisation"

STEPS = [STEP_URL, STEP_VIDEO, STEP_SUBTITLES, STEP_AUDIO, STEP_TRANSCRIBE, STEP_FINALISE]


@dataclass
class AnalysisRequest:
    """Ce que l'utilisateur a demande, tel qu'il l'a demande."""

    url: str
    model: str = "small"
    language: Optional[str] = None          # None = detection automatique
    device: str = "auto"
    allow_audio_download: bool = False      # case cochee dans l'interface
    prefer_subtitles: bool = True
    force_whisper: bool = False             # "Refaire avec Faster-Whisper"


@dataclass
class AnalysisReport:
    """Ce qui s'est reellement passe. Sert a l'interface pour dire la verite a
    l'utilisateur : d'ou vient le texte, et s'il couvre toute la video."""

    project: Optional[VoiceStudioProject] = None
    used_source: str = ""
    from_cache: bool = False
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.project and self.project.has_transcript)


class Reporter:
    """Compte rendu d'avancement : etape courante, detail, fraction.

    Volontairement minuscule : l'interface a besoin d'une etape et d'un
    pourcentage, pas d'un systeme d'evenements.
    """

    def __init__(self, on_step: Optional[Callable[[int, str], None]] = None,
                 on_detail: Optional[Callable[[Optional[float], str], None]] = None):
        self._on_step = on_step
        self._on_detail = on_detail
        self.index = 0

    def step(self, label: str) -> None:
        self.index = STEPS.index(label) + 1 if label in STEPS else self.index + 1
        logger.info(f"[Voice Studio] {self.index}/{len(STEPS)} {label}")
        if self._on_step:
            self._on_step(self.index, label)

    def detail(self, fraction: Optional[float], label: str) -> None:
        if self._on_detail:
            self._on_detail(fraction, label)


def existing_project(url_or_id: str) -> VoiceStudioProject | None:
    """Projet deja enregistre pour cette video, s'il y en a un.

    Appele AVANT de lancer quoi que ce soit : l'interface propose alors
    d'ouvrir la transcription existante plutot que de la refaire.
    """
    try:
        video_id = parse_video_id(url_or_id)
    except ValueError:
        return None
    project = store.load(video_id)
    return project if project and project.has_transcript else None


def run_analysis(request: AnalysisRequest, reporter: Reporter | None = None,
                 cancel_token: Optional[CancelToken] = None,
                 ydl_factory=None) -> AnalysisReport:
    """Parcours complet. Leve InvalidYouTubeUrl, YouTubeDownloadError,
    RightsNotConfirmedError ou CancelledError -- toutes portent deja un message
    lisible, l'interface n'a rien a reformuler."""
    reporter = reporter or Reporter()
    report = AnalysisReport()

    def _check() -> None:
        if cancel_token is not None:
            cancel_token.check()

    reporter.step(STEP_URL)
    video_id = parse_video_id(request.url)
    _check()

    reporter.step(STEP_VIDEO)
    info = fetch_info(video_id, ydl_factory=ydl_factory)
    project = store.load(video_id) or VoiceStudioProject(youtube_video_id=video_id)
    project.youtube_url = watch_url(video_id)
    project.title = info.title or project.title
    project.channel = info.channel or project.channel
    project.duration_s = info.duration_s if info.duration_s else project.duration_s
    project.thumbnail_url = info.thumbnail_url or project.thumbnail_url
    report.project = project
    _check()

    transcript = None
    source = ""

    with tempfile.TemporaryDirectory(prefix="voice_studio_src_") as workdir:
        try:
            reporter.step(STEP_SUBTITLES)
            if request.prefer_subtitles and not request.force_whisper:
                if info.has_any_captions:
                    reporter.detail(None, "lecture des sous-titres publiés")
                    try:
                        transcript = fetch_transcript(video_id, workdir, request.language,
                                                      ydl_factory=ydl_factory)
                    except SubtitlesUnavailable as error:
                        report.notes.append(str(error))
                        transcript = None
                    if transcript is not None:
                        source = getattr(transcript, "voice_studio_source", SOURCE_SUBTITLES)
                else:
                    report.notes.append(
                        "Aucun sous-titre n'est publié avec cette vidéo."
                    )
            _check()

            if transcript is None:
                if not request.allow_audio_download:
                    raise RightsNotConfirmedError(
                        "Aucune transcription n'est publiée avec cette vidéo. Pour la "
                        "transcrire ici, Voice Studio doit récupérer sa piste audio : "
                        "coche la case correspondante si tu as le droit de traiter ce "
                        "contenu.\n\n" + transcription.RIGHTS_NOTICE
                    )
                reporter.step(STEP_AUDIO)
                media_path = transcription.download_audio(
                    video_id, workdir, consent_confirmed=True,
                    on_progress=reporter.detail, cancel_token=cancel_token,
                    ydl_factory=ydl_factory,
                )
                _check()

                reporter.step(STEP_TRANSCRIBE)
                transcript = transcription.transcribe_media(
                    media_path, request.model, request.language, request.device,
                    cancel_token=cancel_token, on_progress=reporter.detail,
                )
                source = SOURCE_WHISPER
        except CancelledError:
            # Rien n'est enregistre : une transcription interrompue est
            # incomplete, et une transcription incomplete presentee comme
            # complete serait pire que pas de transcription du tout.
            raise

    reporter.step(STEP_FINALISE)
    project.transcript = transcript
    project.transcription_status = STATUS_DONE
    project.transcription_source = source
    project.language = getattr(transcript, "language", "") or project.language
    project.whisper_model = request.model if source == SOURCE_WHISPER else ""
    store.save(project)

    report.used_source = source
    covered = coverage(transcript, project.duration_s or 0.0)
    if not covered.reached_end:
        report.notes.append(
            f"La transcription s'arrête à {covered.last_end_s / 60:.1f} min alors que la "
            f"vidéo dure {(project.duration_s or 0) / 60:.1f} min. "
            "Il manque peut-être la fin : relance avec Faster-Whisper si le texte "
            "vient des sous-titres."
        )
    if source == SOURCE_AUTO_CAPTIONS:
        report.notes.append(
            "Texte issu des sous-titres générés automatiquement par YouTube : "
            "leur ponctuation et certains mots peuvent être approximatifs."
        )
    return report
