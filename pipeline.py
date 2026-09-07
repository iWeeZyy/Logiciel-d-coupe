"""Orchestrateur : enchaine transcription -> analyse -> scoring -> selection ->
generation des clips, avec l'affichage de progression "[i/5] Etape..." demande
par le cahier des charges (section 11)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from analysis.audio_analyzer import AudioAnalyzer
from analysis.hook_detector import generate_candidates
from analysis.scoring import Scorer
from analysis.selector import select_clips
from analysis.text_analyzer import TextAnalyzer
from core.config_loader import Settings
from core.logging_setup import StepProgress, get_logger
from core.models import ClipResult
from export.exporter import clip_filename, ensure_output_dir, preflight_check, write_results
from transcription import cache as transcript_cache
from transcription.whisper_engine import transcribe
from utils.errors import InputFileError
from video import ffmpeg_utils
from video.audio_extractor import extract_audio
from video.clip_builder import build_clip
from video.face_detector import detect_crop_hint

logger = get_logger()


def run(settings: Settings) -> list[ClipResult]:
    input_path = Path(settings.input)
    if not input_path.exists():
        raise InputFileError(f"Fichier introuvable : {settings.input}")

    ffmpeg_utils.ensure_ffmpeg_available()
    preflight_check(settings.output, settings.overwrite)

    video_duration = ffmpeg_utils.video_duration(str(input_path))
    src_w, src_h = ffmpeg_utils.video_resolution(str(input_path))
    logger.info(f"Video : {video_duration:.1f}s, {src_w}x{src_h}.")

    progress = StepProgress(5)
    tmp_dir = tempfile.mkdtemp(prefix="clip_farming_")

    try:
        # [1/5] Extraction audio
        progress.step("Extraction audio")
        wav_path = str(Path(tmp_dir) / "audio_16k_mono.wav")
        extract_audio(str(input_path), wav_path, sample_rate=settings.audio_analysis.get("sample_rate", 16000))

        # [2/5] Transcription
        progress.step("Transcription")
        transcript = None
        if not settings.no_cache:
            transcript = transcript_cache.load(str(input_path), settings.model, settings.language)
            if transcript is not None:
                logger.info("Transcription recuperee depuis le cache (.cache/) -- Whisper non relance.")
        if transcript is None:
            transcript = transcribe(wav_path, settings.model, settings.language, settings.device)
            if not settings.no_cache:
                transcript_cache.save(str(input_path), settings.model, settings.language, transcript)

        # [3/5] Analyse des hooks
        progress.step("Analyse des hooks")
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
        )
        scored = [scorer.score(c) for c in candidates]

        # [4/5] Selection des meilleurs passages
        progress.step("Selection des meilleurs passages")
        selected = select_clips(
            scored,
            nb_clips=settings.nb_clips,
            min_gap=settings.min_gap,
            pre_roll=settings.pre_roll,
            post_roll=settings.post_roll,
            max_overshoot_ratio=settings.hook_detection.get("max_overshoot_ratio", 0.2),
            video_duration=video_duration,
            text_analyzer=text_analyzer,
        )

        # [5/5] Generation des clips
        progress.step("Generation des clips")
        ensure_output_dir(settings.output)
        subtitle_style = settings.subtitle_style_params()
        face_cfg = settings.face_detection

        clip_results: list[ClipResult] = []
        for i, sc in enumerate(selected, start=1):
            c = sc.candidate
            score = sc.scores.total
            filename = clip_filename(i, score)
            progress.substep(f"clip {i}/{len(selected)} -> {filename} (score {score:.0f}/100)")

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

            out_mp4_path = str(Path(settings.output) / filename)
            ass_path = str(Path(tmp_dir) / f"clip_{i:02d}.ass")

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
                clip_label=filename,
            )

            clip_results.append(
                ClipResult(
                    index=i,
                    file_name=filename,
                    start=c.start,
                    end=c.end,
                    duration=c.duration,
                    score=score,
                    scores=sc.scores.to_dict(),
                    transcript=c.text,
                    language=transcript.language,
                    reasons=sc.reasons,
                )
            )

        results_path = write_results(settings.output, clip_results)
        logger.info(f"Termine. {len(clip_results)} clip(s) dans '{settings.output}/', details : {results_path}")

        if settings.debug_scores:
            _print_debug_summary(clip_results)

        return clip_results

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
