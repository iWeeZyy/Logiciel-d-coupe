"""Orchestrateur : enchaine transcription -> analyse -> scoring -> selection ->
detection du contexte -> generation des clips, avec l'affichage de progression
"[i/n] Etape..." demande par le cahier des charges (section 11).

C'est ici, et nulle part ailleurs, qu'on decide d'appliquer ou non la
proposition d'un module d'edition automatique : chaque module renvoie une
confiance, l'orchestrateur tranche (section 13).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from analysis.audio_analyzer import AudioAnalyzer
from analysis.hook_detector import generate_candidates
from analysis.scoring import Scorer
from analysis.selector import select_clips
from analysis.text_analyzer import TextAnalyzer
from core.cancellation import CancelToken
from core.config_loader import Settings
from core.logging_setup import StepProgress, get_logger
from core.models import Candidate, ClipResult, ProgressEvent, ScoredCandidate
from core.steps import (
    STEP_ANALYSIS,
    STEP_AUDIO,
    STEP_CONTEXT,
    STEP_RENDER,
    STEP_SELECTION,
    STEP_TRANSCRIPTION,
    build_step_labels,
)
from editing.context import ContextResult, adjust_clip_bounds, resolve_overlaps
from editing.sentences import build_sentences
from export.exporter import (
    clip_relative_path,
    clip_stem,
    ensure_output_dir,
    preflight_check,
    write_clip_metadata,
    write_results,
)
from transcription import cache as transcript_cache
from transcription.whisper_engine import transcribe
from utils.errors import CancelledError, InputFileError
from video import ffmpeg_utils
from video.audio_extractor import extract_audio
from video.clip_builder import build_clip
from video.face_detector import detect_crop_hint

logger = get_logger()


def run(
    settings: Settings,
    on_progress: Optional[Callable[[ProgressEvent], None]] = None,
    cancel_token: Optional[CancelToken] = None,
) -> list[ClipResult]:
    input_path = Path(settings.input)
    if not input_path.exists():
        raise InputFileError(f"Fichier introuvable : {settings.input}")

    ffmpeg_utils.ensure_ffmpeg_available()
    preflight_check(settings.output, settings.overwrite)

    video_duration = ffmpeg_utils.video_duration(str(input_path))
    src_w, src_h = ffmpeg_utils.video_resolution(str(input_path))
    logger.info(f"Video : {video_duration:.1f}s, {src_w}x{src_h}.")

    context_enabled = settings.editing_module_enabled("context_detection")
    progress = StepProgress(build_step_labels(context_detection=context_enabled), on_progress=on_progress)
    tmp_dir = tempfile.mkdtemp(prefix="clip_farming_")

    try:
        # [1] Extraction audio
        if cancel_token:
            cancel_token.check()
        progress.step(STEP_AUDIO)
        wav_path = str(Path(tmp_dir) / "audio_16k_mono.wav")
        extract_audio(str(input_path), wav_path, sample_rate=settings.audio_analysis.get("sample_rate", 16000))

        # [2] Transcription
        if cancel_token:
            cancel_token.check()
        progress.step(STEP_TRANSCRIPTION)
        transcript = None
        if not settings.no_cache:
            transcript = transcript_cache.load(str(input_path), settings.model, settings.language)
            if transcript is not None:
                logger.info("Transcription recuperee depuis le cache (.cache/) -- Whisper non relance.")
        if transcript is None:
            def _on_segment(elapsed: float, total: float) -> None:
                progress.report(f"{elapsed:.0f}s / {total:.0f}s transcrites", fraction=elapsed / total)

            transcript = transcribe(
                wav_path, settings.model, settings.language, settings.device,
                cancel_token=cancel_token, on_segment_progress=_on_segment,
            )
            if not settings.no_cache:
                transcript_cache.save(str(input_path), settings.model, settings.language, transcript)

        # [3] Analyse des hooks
        if cancel_token:
            cancel_token.check()
        progress.step(STEP_ANALYSIS)
        audio_analyzer = AudioAnalyzer(
            wav_path,
            hop_length_ms=settings.audio_analysis.get("hop_length_ms", 20),
            compute_pitch=settings.audio_analysis.get("compute_pitch", True),
            silence_threshold_db=settings.scoring_params.get("silence_rms_threshold_db", -35),
            peak_prominence_db=settings.scoring_params.get("energy_peak_prominence_db", 6),
        )
        text_analyzer = TextAnalyzer(transcript, settings.keywords_config, settings.scoring_params)

        candidates = generate_candidates(
            audio_analyzer,
            text_analyzer,
            video_duration=video_duration,
            clip_duration=settings.clip_duration,
            stride_ratio=settings.hook_detection.get("stride_ratio", 0.33),
            min_words_in_window=settings.scoring_params.get("min_words_in_window", 8),
        )

        scorer = Scorer(
            weights=settings.weights,
            scoring_params=settings.scoring_params,
            audio_stats={
                "mean_db": audio_analyzer.global_rms_db_mean,
                "p90_db": audio_analyzer.global_rms_db_p90,
            },
            clip_scores=settings.editing.get("clip_scores"),
        )
        scored = [scorer.score(c) for c in candidates]

        # [4] Selection des meilleurs passages
        if cancel_token:
            cancel_token.check()
        progress.step(STEP_SELECTION)
        selected = select_clips(
            scored,
            nb_clips=settings.nb_clips,
            min_gap=settings.min_gap,
            pre_roll=settings.pre_roll,
            post_roll=settings.post_roll,
            max_overshoot_ratio=settings.hook_detection.get("max_overshoot_ratio", 0.2),
            video_duration=video_duration,
            text_analyzer=text_analyzer,
            # Quand la detection du contexte est active, c'est elle qui fixe les
            # bornes : appliquer en plus --pre-roll/--post-roll ferait deux
            # extensions superposees.
            apply_context=not context_enabled,
        )
        progress.set_clips_found(len(selected))

        # [5] Detection du contexte (optionnelle)
        clips: list[tuple[ScoredCandidate, Optional[ContextResult]]]
        if context_enabled:
            if cancel_token:
                cancel_token.check()
            progress.step(STEP_CONTEXT)
            clips = _detect_context(
                selected, settings, transcript, text_analyzer, audio_analyzer, scorer, video_duration, progress
            )
            progress.set_clips_found(len(clips))
        else:
            clips = [(sc, None) for sc in selected]

        # [6] Generation des clips
        if cancel_token:
            cancel_token.check()
        progress.step(STEP_RENDER)
        ensure_output_dir(settings.output)
        subtitle_style = settings.subtitle_style_params()
        face_cfg = settings.face_detection

        clip_results: list[ClipResult] = []
        for i, (sc, ctx) in enumerate(clips, start=1):
            if cancel_token:
                cancel_token.check()
            c = sc.candidate
            score = sc.scores.viral
            relative_path = clip_relative_path(i)
            progress.substep(
                f"clip {i}/{len(clips)} -> {clip_stem(i)} (score {score:.0f}/100)",
                fraction=(i - 1) / len(clips),
            )

            face_hint = None
            if face_cfg.get("enabled", True):
                face_hint = detect_crop_hint(
                    str(input_path),
                    c.start,
                    c.end,
                    sample_interval_s=face_cfg.get("sample_interval_s", 1.0),
                    max_samples=face_cfg.get("max_samples_per_clip", 20),
                    confidence_threshold=face_cfg.get("confidence_threshold", 0.6),
                )

            out_mp4_path = str(Path(settings.output) / relative_path)
            ass_path = str(Path(tmp_dir) / f"{clip_stem(i)}.ass")

            try:
                build_clip(
                    video_path=str(input_path),
                    start=c.start,
                    end=c.end,
                    src_w=src_w,
                    src_h=src_h,
                    face_hint=face_hint,
                    words=c.words,
                    subtitle_style=subtitle_style,
                    out_mp4_path=out_mp4_path,
                    ass_path=ass_path,
                    export_settings=settings.export,
                    clip_label=clip_stem(i),
                    cancel_token=cancel_token,
                )
            except CancelledError:
                # ffmpeg a ete tue en plein encodage -- le fichier de sortie est
                # incomplet/corrompu, on le supprime plutot que de laisser un
                # .mp4 casse dans le dossier de sortie.
                Path(out_mp4_path).unlink(missing_ok=True)
                raise

            clip_result = ClipResult(
                index=i,
                file_name=relative_path,
                start=c.start,
                end=c.end,
                duration=c.duration,
                score=score,
                scores=sc.scores.to_dict(),
                transcript=c.text,
                language=transcript.language,
                reasons=sc.reasons,
                context=ctx.to_dict() if ctx is not None else {},
            )
            write_clip_metadata(settings.output, clip_result)
            clip_results.append(clip_result)

        results_path = write_results(settings.output, clip_results)
        logger.info(f"Termine. {len(clip_results)} clip(s) dans '{settings.output}/', details : {results_path}")

        if settings.debug_scores:
            _print_debug_summary(clip_results)

        return clip_results

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _detect_context(
    selected: list[ScoredCandidate],
    settings: Settings,
    transcript,
    text_analyzer: TextAnalyzer,
    audio_analyzer: AudioAnalyzer,
    scorer: Scorer,
    video_duration: float,
    progress: StepProgress,
) -> list[tuple[ScoredCandidate, Optional[ContextResult]]]:
    """Recale les bornes de chaque clip sur la structure du discours, resout les
    chevauchements que cette extension peut creer, puis RE-NOTE les passages sur
    leurs bornes definitives.

    La re-notation compte : sans elle, les scores affiches seraient ceux de la
    fenetre d'analyse, pas ceux du clip reellement exporte -- et le Rewatch
    Score, qui regarde justement si le clip se termine sur une phrase finie,
    serait systematiquement faux."""
    cfg = settings.editing_module("context_detection")
    min_duration = float(cfg.get("min_clip_duration_s", 8.0))
    max_duration = settings.max_clip_duration()

    sentences = build_sentences(
        transcript.words(),
        max_gap_s=cfg.get("sentence_gap_s", 0.6),
        question_starters=settings.keywords_config.get("question_starters", []),
    )
    if not sentences:
        logger.info("Aucune phrase horodatee exploitable -- bornes d'origine conservees.")
        return [(sc, None) for sc in selected]

    proposals: list[ContextResult] = []
    for sc in selected:
        proposals.append(
            adjust_clip_bounds(
                start=sc.candidate.start,
                end=sc.candidate.end,
                sentences=sentences,
                video_duration=video_duration,
                max_duration=max_duration,
                min_duration=min_duration,
                padding_before_s=float(cfg.get("padding_before_s", 0.0)),
                padding_after_s=float(cfg.get("padding_after_s", 0.0)),
                max_lookback_s=float(cfg.get("max_lookback_s", 12.0)),
                max_lookahead_s=float(cfg.get("max_lookahead_s", 12.0)),
                payoff_max_gap_s=float(cfg.get("payoff_max_gap_s", 1.5)),
                min_confidence=float(cfg.get("min_confidence", 0.0)),
                narrative_markers=settings.editing.get("narrative_markers", {}),
            )
        )

    # L'extension peut faire se chevaucher deux clips que la selection avait
    # choisis disjoints. min_gap n'est volontairement pas re-applique ici : la
    # regle a ce stade est "aucun clip ne doit montrer le meme passage qu'un
    # autre", pas "les clips doivent rester eloignes de --min-gap".
    resolved = resolve_overlaps(
        [(p.start, p.end, sc.scores.viral) for sc, p in zip(selected, proposals)],
        min_gap=0.0,
        min_duration=min_duration,
    )

    out: list[tuple[ScoredCandidate, Optional[ContextResult]]] = []
    for sc, proposal, bounds in zip(selected, proposals, resolved):
        if bounds is None:
            logger.info(
                f"Clip {sc.candidate.start:.1f}s-{sc.candidate.end:.1f}s abandonne : "
                "entierement recouvert par un clip mieux note apres extension du contexte."
            )
            continue

        start, end = bounds
        trimmed = (abs(start - proposal.start) > 1e-3) or (abs(end - proposal.end) > 1e-3)
        reasons = list(proposal.reasons)
        if trimmed:
            reasons.append("bornes rognees pour ne pas chevaucher un autre clip")

        text, text_features = text_analyzer.analyze_window(start, end)
        audio_features = audio_analyzer.analyze_window(start, end)
        words = text_analyzer.words_in_window(start, end)
        rescored = scorer.score(
            Candidate(
                start=start, end=end, text=text or sc.candidate.text,
                words=words or sc.candidate.words,
                audio=audio_features, text_features=text_features,
            )
        )

        final_context = ContextResult(
            start=start, end=end,
            applied=proposal.applied,
            confidence=proposal.confidence,
            category=proposal.category,
            reasons=reasons,
            truncated_by_max_duration=proposal.truncated_by_max_duration,
        )
        out.append((rescored, final_context))

    if not out:
        # Ne devrait pas arriver (le mieux note est toujours conserve), mais on
        # ne renvoie jamais une liste vide sans repli.
        logger.warning("Detection du contexte : aucun clip retenu, retour aux bornes d'origine.")
        return [(sc, None) for sc in selected]

    out.sort(key=lambda pair: pair[0].scores.viral, reverse=True)
    progress.substep(f"{len(out)} clip(s) recadres sur la structure du discours")
    return out


def _print_debug_summary(clip_results: list[ClipResult]) -> None:
    for c in clip_results:
        print(f"\n{c.file_name}")
        print(f"Score total : {c.score:.0f}/100")
        for k, v in c.scores.items():
            if k == "total":
                continue
            print(f"  {k} : {v:.0f}")
        for r in c.reasons:
            print(f"  + {r}")
        if c.context:
            print(f"  contexte : {c.context.get('category') or 'non categorise'} "
                  f"(confiance {c.context.get('confidence', 0):.2f})")
