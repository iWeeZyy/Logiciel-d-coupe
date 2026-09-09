"""Enchainement complet d'une analyse de clip (sections 4, 16, 17, 18, 20).

Ce fichier ne CALCULE rien lui-meme : il appelle, dans l'ordre, des briques qui
existaient deja (extraction audio, cache de transcription, Faster-Whisper,
decoupage en phrases) puis les modules purs de ce paquet. C'est volontaire --
toute la logique testable est ailleurs, et ce qui reste ici est de la plomberie
qu'on peut lire d'un seul coup d'oeil.

Trois garanties tenues ici et pas ailleurs :

- ANNULABLE a tout instant : le meme CancelToken que le pipeline video, verifie
  entre chaque etape et transmis a la transcription, qui sait s'interrompre
  entre deux segments.
- RIEN N'EST LAISSE DERRIERE : le wav temporaire, et le cas echeant la video
  telechargee, sont supprimes meme en cas d'erreur ou d'annulation.
- AUCUN RETRAITEMENT INUTILE : une analyse deja faite pour le meme media, le
  meme modele et le meme niveau est rendue telle quelle.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from core.cancellation import CancelToken
from core.logging_setup import StepProgress, get_logger
from radar.analysis import confidence, describer, key_moment, lexicon, media
from radar.analysis import transcript_analysis as ta
from radar.analysis.models import (
    LEVEL_DEEP,
    LEVEL_FAST,
    LEVEL_STANDARD,
    ClipAnalysis,
    utc_now_iso,
)
from radar.models import KIND_CLIP
from utils.errors import ClipFarmingError, MediaNotAvailableError

logger = get_logger()

STEP_MEDIA = "🔊 Préparation de l'audio"
STEP_TRANSCRIPTION = "📝 Transcription"
STEP_ANALYSIS = "🧠 Analyse"
STEP_DESCRIPTION = "✍️ Génération du descriptif"
STEPS = [STEP_MEDIA, STEP_TRANSCRIPTION, STEP_ANALYSIS, STEP_DESCRIPTION]

# Garde-fou de duree (section 16, "timeout raisonnable"). La fonctionnalite vise
# des clips : quelques dizaines de secondes. Transcrire une rediffusion de
# quatre heures prendrait des heures sans que personne l'ait demande. La limite
# refuse AVANT de commencer, avec un message clair, plutot que d'interrompre au
# milieu -- une analyse a moitie faite ne sert a rien.
MAX_MEDIA_DURATION_S = 20 * 60

# Modeles preferes en mode rapide, du plus leger au plus lourd. Ils ne sont
# retenus que s'ils sont DEJA telecharges : proposer un mode "rapide" qui
# commence par telecharger 75 Mo serait une promesse inversee.
FAST_MODEL_PREFERENCES = ("tiny", "base")


@dataclass(frozen=True)
class AnalysisRequest:
    """Ce qu'il faut pour lancer une analyse."""

    opportunity: object
    level: str = LEVEL_STANDARD
    local_path: str | None = None
    language: str | None = None
    model: str = "small"
    device: str = "auto"
    rights_confirmed: bool = False
    force: bool = False              # relancer meme si une analyse existe
    creator_label: str = ""
    keyword_terms: tuple = ()


def choose_model(level: str, configured: str, is_available=None) -> str:
    """Modele Whisper reellement utilise pour ce niveau.

    Le niveau ne change PAS la qualite de la transcription en standard et en
    approfondie : c'est le modele choisi dans les parametres qui decide. En
    rapide, un modele plus leger est prefere s'il est deja present sur la
    machine ; sinon on garde celui qui est configure, car telecharger pour aller
    plus vite serait absurde.
    """
    if level != LEVEL_FAST:
        return configured
    if is_available is None:
        from transcription.whisper_engine import _cached_snapshot_path, snapshot_is_complete

        def is_available(name: str) -> bool:
            path = _cached_snapshot_path(name)
            return bool(path) and snapshot_is_complete(path)

    for candidate in FAST_MODEL_PREFERENCES:
        try:
            if is_available(candidate):
                return candidate
        except Exception:      # pragma: no cover - defaut de cache illisible
            continue
    return configured


def reusable(existing, *, level: str, model: str, fingerprint: str) -> bool:
    """Une analyse deja en base peut-elle etre reaffichee telle quelle ?

    Les trois criteres de la section 17 : meme media, meme modele, meme niveau.
    Un seul qui change et le resultat serait different, donc l'analyse est
    refaite.
    """
    if existing is None:
        return False
    if existing.analysis_level != level or existing.model_used != model:
        return False
    if fingerprint and existing.media_fingerprint and existing.media_fingerprint != fingerprint:
        return False
    return True


def _cleanup(paths) -> None:
    for path in paths:
        try:
            target = Path(path)
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        except OSError as error:      # pragma: no cover - disque verrouille
            logger.warning(f"Fichier temporaire non supprimé ({path}) : {error}")


def _transcribe(wav_path: str, request: AnalysisRequest, model: str,
                progress: StepProgress, cancel_token: CancelToken | None):
    """Transcription, en passant par le cache deja utilise par le pipeline."""
    from transcription import cache as transcript_cache
    from transcription.whisper_engine import transcribe

    source = request.local_path or wav_path
    cached = transcript_cache.load(source, model, request.language)
    if cached is not None:
        progress.report("transcription réutilisée depuis le cache", 1.0)
        return cached, True

    def on_segment(done: float, total: float) -> None:
        progress.report(f"{done:.0f} s / {total:.0f} s transcrites",
                        min(1.0, done / total) if total else None)

    def on_download(fraction: float, total_mb):
        label = "téléchargement du modèle Whisper"
        if total_mb:
            label += f" ({fraction * 100:.0f} % de {total_mb:.0f} Mo)"
        progress.report(label, fraction)

    transcript = transcribe(
        wav_path, model, request.language, request.device,
        cancel_token=cancel_token,
        on_segment_progress=on_segment,
        on_download_progress=on_download,
    )
    transcript_cache.save(source, model, request.language, transcript)
    return transcript, False


def run(request: AnalysisRequest, *, store=None, cancel_token: CancelToken | None = None,
        on_progress=None) -> ClipAnalysis:
    """Analyse un contenu et renvoie le resultat, enregistre si un store est fourni."""
    started = time.monotonic()
    opportunity = request.opportunity
    level = request.level if request.level in (LEVEL_FAST, LEVEL_STANDARD, LEVEL_DEEP) else LEVEL_STANDARD
    progress = StepProgress(STEPS, on_progress=on_progress)
    temporaries: list[str] = []

    try:
        # ---------------------------------------------------- 1. le media
        progress.step(STEP_MEDIA)
        source = media.resolve(opportunity, local_path=request.local_path,
                               rights_confirmed=request.rights_confirmed)
        if source.temporary:
            temporaries.append(source.path)

        model = choose_model(level, request.model)

        if store is not None and not request.force:
            existing = store.get_analysis(opportunity.key)
            if reusable(existing, level=level, model=model, fingerprint=source.fingerprint):
                progress.report("analyse déjà disponible pour ce clip", 1.0)
                logger.info(f"Analyse réutilisée pour {opportunity.key}")
                return existing

        from video.ffmpeg_utils import video_duration
        duration = video_duration(source.path)
        if duration > MAX_MEDIA_DURATION_S:
            raise MediaNotAvailableError(
                f"Ce média dure {duration / 60:.0f} minutes. L'analyse de contenu est "
                f"prévue pour des clips (jusqu'à {MAX_MEDIA_DURATION_S // 60} minutes) : "
                "au-delà, elle prendrait beaucoup plus de temps qu'elle n'en ferait gagner.")

        if cancel_token is not None:
            cancel_token.check()

        work_dir = tempfile.mkdtemp(prefix="clipfarming-analyse-")
        temporaries.append(work_dir)
        wav_path = str(Path(work_dir) / "audio.wav")
        progress.substep("extraction de la piste audio", 0.5)
        from video.audio_extractor import extract_audio
        extract_audio(source.path, wav_path)

        # ------------------------------------------------ 2. transcription
        progress.step(STEP_TRANSCRIPTION)
        transcript, from_cache = _transcribe(wav_path, request, model, progress, cancel_token)
        if cancel_token is not None:
            cancel_token.check()

        # ---------------------------------------------------- 3. analyse
        progress.step(STEP_ANALYSIS)
        lx = lexicon.load()
        words = [w for segment in transcript.segments for w in segment.words]
        from core.config_loader import load_keywords_config
        keywords = load_keywords_config()
        from editing.sentences import build_sentences
        sentences = build_sentences(
            words, question_starters=tuple(keywords.get("question_starters", ())))

        profile = ta.analyze(sentences, lx, duration_s=transcript.duration or duration)
        emotions = ta.detect_emotions(sentences, lx, profile) if level != LEVEL_FAST else []
        moment = key_moment.find(
            sentences, lx, duration_s=transcript.duration or duration,
            silences=profile.silences,
            max_alternatives=3 if level == LEVEL_DEEP else 0,
        )
        if cancel_token is not None:
            cancel_token.check()

        # ------------------------------------------------ 4. descriptif
        progress.step(STEP_DESCRIPTION)
        is_clip = getattr(opportunity, "kind", "") == KIND_CLIP
        description = describer.describe(
            sentences, profile=profile, key_moment=moment, emotions=emotions, lexicon=lx,
            creator_label=request.creator_label, category=getattr(opportunity, "category", ""),
            is_clip=is_clip,
            keyword_terms=request.keyword_terms or tuple(keywords.get("strong_keywords", ())),
        )

        report = confidence.evaluate(
            profile=profile, key_moment=moment,
            word_probability=confidence.mean_word_probability(transcript),
            language_probability=getattr(transcript, "language_probability", None),
            speech_absence=confidence.no_speech_ratio(transcript),
            warnings=description.warnings,
        )

        analysis = ClipAnalysis(
            content_id=opportunity.key,
            platform=getattr(opportunity, "platform", ""),
            creator_label=request.creator_label,
            clip_title=getattr(opportunity, "title", ""),
            clip_url=getattr(opportunity, "url", ""),
            duration_s=transcript.duration or duration,
            published_at=getattr(opportunity, "published_at", ""),
            view_count=getattr(opportunity, "view_count", None),
            radar_score=getattr(opportunity, "radar_score", None),
            language=getattr(transcript, "language", ""),
            language_probability=getattr(transcript, "language_probability", None),
            transcript_text=getattr(transcript, "full_text", ""),
            segments=[{"start": s.start, "end": s.end, "text": s.text} for s in transcript.segments],
            words=([{"text": w.text, "start": w.start, "end": w.end,
                     "probability": w.probability} for w in words]
                   if level != LEVEL_FAST else []),
            key_moment=moment.to_dict() if moment is not None else None,
            summary=description.summary,
            description=description.editorial,
            short_description=description.short,
            social_description=description.social,
            hashtags=list(description.hashtags),
            title_direct=description.title_direct,
            title_curiosity=description.title_curiosity,
            title_punchy=description.title_punchy,
            detected_topics=list(description.topics),
            detected_signals=list(profile.signals),
            detected_emotions=[e.to_dict() for e in emotions],
            speech_density=round(profile.speech_density, 3),
            silence_ratio=round(profile.silence_ratio, 3),
            confidence=report.level,
            confidence_reasons=list(report.reasons),
            warnings=list(description.warnings),
            analysis_level=level,
            analyzed_at=utc_now_iso(),
            processing_time_s=round(time.monotonic() - started, 2),
            model_used=model,
            media_fingerprint=source.fingerprint,
        )

        if from_cache:
            analysis.warnings.append("Transcription réutilisée depuis le cache local.")

        if store is not None:
            store.save_analysis(analysis)

        progress.report("analyse terminée", 1.0)
        return analysis

    except ClipFarmingError:
        raise
    except MemoryError as error:      # pragma: no cover - depend de la machine
        raise ClipFarmingError(
            "Mémoire insuffisante pour analyser ce clip. Choisissez un modèle Whisper "
            "plus léger dans les paramètres, ou fermez d'autres applications."
        ) from error
    except Exception as error:
        logger.exception("Échec inattendu de l'analyse de clip")
        raise ClipFarmingError(
            f"L'analyse de ce clip a échoué. Détail technique consigné dans les logs : {error}"
        ) from error
    finally:
        _cleanup(temporaries)
