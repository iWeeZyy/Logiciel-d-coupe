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
from analysis.hook_detector import generate_candidates, whole_video_candidate
from analysis.scoring import Scorer
from analysis.selector import select_clips
from content_factory.selection import DiverseRanker
from performance.profile import build_profile
from performance.store import PerformanceStore
from performance.tracker import record_production
from analysis.text_analyzer import TextAnalyzer
from core.cancellation import CancelToken
from core.config_loader import Settings
from core.logging_setup import StepProgress, get_logger
from core.models import Candidate, ClipResult, ProgressEvent, ScoredCandidate
from core.steps import (
    STEP_ANALYSIS,
    STEP_AUDIO,
    STEP_CLIP_ANALYSIS,
    STEP_CONTEXT,
    STEP_METADATA,
    STEP_RENDER,
    STEP_SELECTION,
    STEP_TRANSCRIPTION,
    build_step_labels,
)
from editing.captions import CaptionGroup, build_captions, choose_margin_v, score_emphasis
from editing.context import ContextResult, adjust_clip_bounds, resolve_overlaps
from editing.framing import FramingPlan, build_framing_plan
from editing.metadata import build_metadata
from editing.sentences import build_sentences, sentences_in_range
from editing.silence_cut import MontagePlan, build_montage_plan
from editing.speaker import detect_active_speaker
from editing.timeline import EditList
from editing.thumbnail import choose_text
from editing.zoom import ZoomTrack, build_zoom_track
from export.exporter import (
    clip_relative_path,
    clip_stem,
    ensure_output_dir,
    preflight_check,
    write_clip_metadata,
    write_results,
)
from export.subtitles_export import write_subtitles
from transcription import cache as transcript_cache
from transcription.whisper_engine import transcribe
from utils.errors import CancelledError, InputFileError
from video import ffmpeg_utils
from video.audio_extractor import extract_audio
from video.clip_builder import build_clip
from video.cropper import compute_crop_rect, face_center_in_output
from video.face_detector import crop_hint_from_track, detect_face_track
from video.thumbnailer import generate_thumbnails

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

    from video.watermark import from_config as watermark_from_config
    from video.watermark import reserved_bottom_px as watermark_reserved_bottom

    watermark = (watermark_from_config(settings.editing_module("watermark"))
                 if settings.editing_module_enabled("watermark") else None)
    # Le traitement du son fait partie du montage automatique : il est decrit
    # dans son bloc de configuration, et la case "Montage auto" doit donc le
    # couper aussi. Le lire directement, comme c'etait le cas, laissait le
    # volume normalise sur un clip que l'utilisateur avait demande intact --
    # exactement le defaut deja corrige sur la case des sous-titres.
    montage_audio_cfg = (settings.editing_module("montage").get("audio")
                         if settings.editing_module_enabled("montage") else None)
    reserved_bottom = watermark_reserved_bottom(
        watermark, settings.target_size()[0], out_h=settings.target_size()[1])
    # La source EST deja le clip (Radar) : il n'y a pas de passage a chercher
    # dedans, donc pas de recadrage temporel non plus. La detection du contexte
    # deplacerait des bornes choisies par la personne qui a decoupe le clip.
    whole_source = bool(getattr(settings, "whole_source", False))
    context_enabled = (settings.editing_module_enabled("context_detection")
                       and not whole_source)
    metadata_enabled = (settings.editing_module_enabled("metadata")
                        or settings.editing_module_enabled("thumbnails"))
    progress = StepProgress(
        build_step_labels(context_detection=context_enabled, metadata=metadata_enabled,
                          whole_source=whole_source),
        on_progress=on_progress,
    )
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

            # Le premier lancement telecharge le modele (484 Mo pour 'small',
            # ~3 Go pour 'large'). Sans ce compte rendu, l'interface reste figee
            # sur "Transcription" pendant tout ce temps et l'application parait
            # bloquee -- la fermer laisse alors un cache incomplet.
            def _on_download(done_mb: float, total_mb: float | None) -> None:
                if total_mb:
                    progress.report(
                        f"Téléchargement du modèle {settings.model} : "
                        f"{done_mb:.0f} / {total_mb:.0f} Mo",
                        fraction=min(done_mb / total_mb, 0.999),
                    )
                else:
                    progress.report(
                        f"Téléchargement du modèle {settings.model} : {done_mb:.0f} Mo"
                    )

            transcript = transcribe(
                wav_path, settings.model, settings.language, settings.device,
                cancel_token=cancel_token, on_segment_progress=_on_segment,
                on_download_progress=_on_download,
            )
            if not settings.no_cache:
                transcript_cache.save(str(input_path), settings.model, settings.language, transcript)

        # [3] Analyse des hooks
        if cancel_token:
            cancel_token.check()
        progress.step(STEP_CLIP_ANALYSIS if whole_source else STEP_ANALYSIS)
        audio_analyzer = AudioAnalyzer(
            wav_path,
            hop_length_ms=settings.audio_analysis.get("hop_length_ms", 20),
            compute_pitch=settings.audio_analysis.get("compute_pitch", True),
            silence_threshold_db=settings.scoring_params.get("silence_rms_threshold_db", -35),
            peak_prominence_db=settings.scoring_params.get("energy_peak_prominence_db", 6),
        )
        text_analyzer = TextAnalyzer(transcript, settings.keywords_config, settings.scoring_params)

        if whole_source:
            # Une seule fenetre : le clip entier. Rien n'est compare, donc rien
            # ne peut etre rogne.
            candidates = [whole_video_candidate(audio_analyzer, text_analyzer, video_duration)]
        else:
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
        # Phrases de toute la video, calculees une seule fois et partagees :
        # la qualite de contexte du Priority Score en a besoin des la selection,
        # le montage s'en sert ensuite pour proteger les pauses volontaires.
        # Elles servent dans les deux cas : le montage en a besoin meme quand il
        # n'y a rien a selectionner.
        sentence_index = build_sentences(
            transcript.words(),
            max_gap_s=settings.editing_module("context_detection").get("sentence_gap_s", 0.6),
            question_starters=settings.keywords_config.get("question_starters", []),
        )

        if whole_source:
            # Choisir le meilleur parmi un seul, c'est le prendre. Pas d'etape
            # affichee pour une decision qui n'en est pas une, et surtout pas de
            # --pre-roll/--post-roll : les bornes du clip sont deja les bonnes.
            ranker = None
            selected = scored
            progress.set_clips_found(len(selected))
        else:
            if cancel_token:
                cancel_token.check()
            progress.step(STEP_SELECTION)

            ranker = _build_ranker(settings, sentence_index, audio_analyzer, video_duration)
            selected = select_clips(
                scored,
                nb_clips=settings.nb_clips,
                min_gap=settings.min_gap,
                pre_roll=settings.pre_roll,
                post_roll=settings.post_roll,
                max_overshoot_ratio=settings.hook_detection.get("max_overshoot_ratio", 0.2),
                video_duration=video_duration,
                text_analyzer=text_analyzer,
                # Quand la detection du contexte est active, c'est elle qui fixe
                # les bornes : appliquer en plus --pre-roll/--post-roll ferait
                # deux extensions superposees.
                apply_context=not context_enabled,
                ranker=ranker,
            )
            progress.set_clips_found(len(selected))
            if ranker is not None:
                for line in ranker.funnel.lines():
                    logger.info(line)
                progress.report(" -> ".join(ranker.funnel.lines()))

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
        source_fps = ffmpeg_utils.video_fps(str(input_path))
        clip_results: list[ClipResult] = []
        # Temps de parole et priorite par clip : connus ici seulement, et
        # necessaires a la fiche technique enregistree en fin de production.
        spoken_seconds: dict[int, float] = {}
        clip_priorities: dict[int, float] = {}
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

            face_hint, framing_plan = _analyse_framing(
                settings, str(input_path), c, audio_analyzer, face_cfg
            )

            emphasis_scores = _emphasis_scores(c, settings, subtitle_style, audio_analyzer)
            montage_plan, zoom_track = _build_montage(
                c, settings, audio_analyzer, sentence_index, emphasis_scores
            )
            edit_list = montage_plan.edit_list

            # Le montage change la timeline : les mots (et donc les sous-titres)
            # sont recales dessus avant tout rendu. Cette conversion passe par
            # l'EditList et nulle part ailleurs.
            remapped = edit_list.remap_words_with_indices(c.words)
            render_words = [w for _, w in remapped]
            render_scores = {
                new_index: emphasis_scores[old_index]
                for new_index, (old_index, _) in enumerate(remapped)
                if old_index in emphasis_scores
            }

            caption_groups, caption_margin_v = _build_captions_for_clip(
                render_words, render_scores, settings, subtitle_style, face_hint, src_w, src_h,
                reserved_bottom=reserved_bottom,
            )

            out_mp4_path = str(Path(settings.output) / relative_path)
            # DEFAUT REEL CORRIGE : la case "Sous-titres" de l'accueil coupait
            # bien le module captions, mais le .ass etait construit et incruste
            # quoi qu'il arrive -- seul l'export du fichier .srt etait supprime.
            # Decocher la case ne changeait donc rien a l'image. Le meme test
            # decide desormais de l'incrustation ET de l'export.
            ass_path = (str(Path(tmp_dir) / f"{clip_stem(i)}.ass")
                        if settings.editing_module_enabled("captions") else None)

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
                    caption_groups=caption_groups,
                    subtitle_margin_v=caption_margin_v,
                    edit_list=edit_list,
                    framing_plan=framing_plan,
                    zoom_track=zoom_track,
                    audio_cfg=montage_audio_cfg,
                    fps=source_fps,
                    target_size=settings.target_size(),
                    watermark=watermark,
                    fill=settings.fill_mode,
                    fit=settings.fit_mode,
                )
            except CancelledError:
                # ffmpeg a ete tue en plein encodage -- le fichier de sortie est
                # incomplet/corrompu, on le supprime plutot que de laisser un
                # .mp4 casse dans le dossier de sortie.
                Path(out_mp4_path).unlink(missing_ok=True)
                raise

            captions_cfg = settings.editing_module("captions")
            subtitle_files = []
            # editing_module_enabled et non captions_cfg["enabled"] : c'est lui
            # qui applique l'interrupteur "sous-titres". Sans cela, un clip sans
            # sous-titres incrustes exportait quand meme un fichier .srt.
            if caption_groups and settings.editing_module_enabled("captions"):
                subtitle_files = [
                    str(Path(p).relative_to(Path(settings.output)).as_posix())
                    for p in write_subtitles(
                        settings.output, clip_stem(i), caption_groups,
                        srt=bool(captions_cfg.get("export_srt", False)),
                        vtt=bool(captions_cfg.get("export_vtt", False)),
                    )
                ]

            spoken_seconds[i] = sum(max(0.0, w.end - w.start) for w in c.words)
            if ranker is not None:
                breakdown = ranker.priority_of(sc)
                if breakdown is not None:
                    clip_priorities[i] = round(breakdown.total, 1)

            clip_result = ClipResult(
                index=i,
                file_name=relative_path,
                start=c.start,
                end=c.end,
                duration=edit_list.output_duration,
                score=score,
                scores=sc.scores.to_dict(),
                transcript=c.text,
                language=transcript.language,
                reasons=sc.reasons,
                context=ctx.to_dict() if ctx is not None else {},
                subtitles=subtitle_files,
                framing=framing_plan.to_dict() if framing_plan is not None else {},
                montage={**montage_plan.to_dict(), "zoom": zoom_track.to_dict()}
                if montage_plan.applied or zoom_track.events else {},
            )
            write_clip_metadata(settings.output, clip_result)
            clip_results.append(clip_result)

        if metadata_enabled and clip_results:
            if cancel_token:
                cancel_token.check()
            progress.step(STEP_METADATA)
            _build_metadata_and_thumbnails(
                clip_results, clips, settings, sentence_index, str(input_path), progress, cancel_token
            )

        results_path = write_results(settings.output, clip_results)
        logger.info(f"Termine. {len(clip_results)} clip(s) dans '{settings.output}/', details : {results_path}")

        # Fiche technique de chaque clip, pour pouvoir plus tard confronter ce
        # qui a ete estime a ce qui a ete constate (section 11). Rien n'est
        # mesure de plus ici : ce sont les valeurs deja calculees, photographiees
        # au moment de la production. Une erreur d'ecriture ne doit jamais faire
        # echouer une production reussie -- les clips existent, c'est eux le
        # resultat attendu.
        try:
            record_production(
                PerformanceStore(),
                clip_results,
                project_name=Path(settings.output).name,
                priorities=clip_priorities,
                spoken_seconds=spoken_seconds,
                subtitle_style=settings.subtitle_style,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Caracteristiques des clips non enregistrees : {error}")

        if settings.debug_scores:
            _print_debug_summary(clip_results)

        return clip_results

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_metadata_and_thumbnails(
    clip_results: list[ClipResult],
    clips,
    settings: Settings,
    sentence_index,
    video_path: str,
    progress: StepProgress,
    cancel_token: Optional[CancelToken],
) -> None:
    """Titres, description et miniatures, apres que les clips existent.

    Cette etape vient en dernier a dessein : elle ne peut pas faire echouer la
    generation des clips, et un titre ou une miniature manquants laissent un
    clip parfaitement utilisable."""
    metadata_cfg = settings.editing_module("metadata")
    thumbnails_cfg = settings.editing_module("thumbnails")

    for clip_result, (sc, _ctx) in zip(clip_results, clips):
        if cancel_token:
            cancel_token.check()
        progress.substep(
            f"clip {clip_result.index}/{len(clip_results)} -> titres et miniatures",
            fraction=(clip_result.index - 1) / max(1, len(clip_results)),
        )

        clip_sentences = sentences_in_range(sentence_index, sc.candidate.start, sc.candidate.end)

        if metadata_cfg.get("enabled", False) and clip_sentences:
            meta = build_metadata(
                clip_sentences,
                keyword_terms=settings.keywords_config.get("strong_keywords", []),
                max_title_words=int(metadata_cfg.get("max_title_words", 11)),
                punchy_max_words=int(metadata_cfg.get("punchy_max_words", 6)),
                min_title_words=int(metadata_cfg.get("min_title_words", 3)),
                max_description_sentences=int(metadata_cfg.get("max_description_sentences", 2)),
                max_hashtags=int(metadata_cfg.get("max_hashtags", 5)),
            )
            data = meta.to_dict()
            if not metadata_cfg.get("descriptions", True):
                # Titres sans description : deux interrupteurs distincts cote
                # interface, la description est donc retiree apres coup plutot
                # que via un second chemin de calcul.
                data["description"] = ""
                data["hashtags"] = []
            clip_result.metadata = data

        if thumbnails_cfg.get("enabled", False):
            text = choose_text(
                clip_result.metadata.get("titles", []),
                max_words=int(thumbnails_cfg.get("text_max_words", 7)),
            )
            clip_result.thumbnails = generate_thumbnails(
                video_path, sc.candidate.start, sc.candidate.end,
                settings.output, clip_stem(clip_result.index), text=text, cfg=thumbnails_cfg,
                target_size=settings.target_size(),
            )

        write_clip_metadata(settings.output, clip_result)


def _emphasis_scores(
    candidate: Candidate,
    settings: Settings,
    subtitle_style: dict,
    audio_analyzer: AudioAnalyzer,
) -> dict[int, float]:
    """Importance de chaque mot du clip (index sur candidate.words).

    Calculee une seule fois et partagee par les trois modules qui en ont
    besoin : mise en evidence des sous-titres, protection des pauses
    volontaires au montage, et choix des instants de zoom."""
    cfg = settings.editing_module("captions")
    emphasis_cfg = cfg.get("emphasis", {})
    if not cfg.get("enabled", False) or not emphasis_cfg.get("enabled", False) or not candidate.words:
        return {}

    loudness = [audio_analyzer.mean_db(w.start, w.end) for w in candidate.words]
    threshold = None
    if len(loudness) >= 2:
        mean = sum(loudness) / len(loudness)
        variance = sum((x - mean) ** 2 for x in loudness) / len(loudness)
        threshold = mean + float(emphasis_cfg.get("loudness_threshold_sigma", 0.75)) * (variance ** 0.5)

    return score_emphasis(
        candidate.words,
        keyword_terms=settings.keywords_config.get("strong_keywords", []),
        weight_overrides=settings.keywords_config.get("keyword_weight_overrides", {}),
        loudness_db=loudness,
        loud_threshold_db=threshold,
    )


def _build_ranker(settings, sentences, audio_analyzer, video_duration):
    """Classeur diversifie du Content Factory, ou None pour le comportement
    historique.

    Desactive aussi quand un seul clip est demande : la diversite d'un
    ensemble d'un element n'a pas de sens, et la penalite de redondance ne
    s'appliquerait a rien.
    """
    cfg = settings.editing_module("content_factory")
    if not cfg.get("enabled", False) or settings.nb_clips <= 1:
        return None

    priority_cfg = cfg.get("priority", {}) or {}
    diversity_cfg = cfg.get("diversity", {}) or {}
    # Profil personnel : lu une fois, et seulement s'il existe. Une erreur de
    # lecture ne doit pas empecher de produire -- on repart alors sur l'analyse
    # generale seule, qui est le comportement par defaut.
    try:
        profile = build_profile(PerformanceStore().records())
    except Exception as error:  # noqa: BLE001
        logger.warning(f"Profil de performance ignore ({error}) -- analyse generale seule.")
        profile = None

    return DiverseRanker(
        sentences=sentences,
        audio_stats={
            "mean_db": audio_analyzer.global_rms_db_mean,
            "p90_db": audio_analyzer.global_rms_db_p90,
        },
        video_duration=video_duration,
        nb_clips=settings.nb_clips,
        weights=priority_cfg.get("weights", {}) or {},
        params=priority_cfg.get("params", {}) or {},
        diversity_strength=float(diversity_cfg.get("strength", 0.55)),
        topic_weight=float(diversity_cfg.get("topic_weight", 0.5)),
        temporal_weight=float(diversity_cfg.get("temporal_weight", 0.5)),
        horizon_s=diversity_cfg.get("horizon_s"),
        funnel_thresholds=cfg.get("funnel", {}) or {},
        profile=profile,
    )


def _analyse_framing(
    settings: Settings,
    video_path: str,
    candidate: Candidate,
    audio_analyzer: AudioAnalyzer,
    face_cfg: dict,
) -> tuple[object, Optional[FramingPlan]]:
    """Une seule passe de detection de visages, deux usages : le cadrage fixe
    (repli historique) et la trajectoire de suivi.

    Rien n'est detecte quand l'image est gardee ENTIERE : il n'y a alors aucun
    choix a faire sur ce qu'on garde, donc rien a centrer ni a suivre. Chercher
    des visages malgre tout couterait une passe d'analyse video complete pour
    un resultat que le rendu ignorerait.
    """
    if getattr(settings, "fit_mode", "recadrer") == "entier":
        return None, None

    framing_cfg = settings.editing_module("framing")
    tracking = framing_cfg.get("enabled", False)

    if not face_cfg.get("enabled", True) and not tracking:
        return None, None

    samples = detect_face_track(
        video_path, candidate.start, candidate.end,
        sample_interval_s=float(framing_cfg.get("sample_interval_s", 0.5)) if tracking
        else float(face_cfg.get("sample_interval_s", 1.0)),
        max_samples=int(framing_cfg.get("max_samples_per_clip", 140)) if tracking
        else int(face_cfg.get("max_samples_per_clip", 20)),
        confidence_threshold=float(framing_cfg.get("confidence_threshold", face_cfg.get("confidence_threshold", 0.6))),
        max_faces=2 if tracking else 1,
    )
    face_hint = crop_hint_from_track(samples)

    if not tracking:
        return face_hint, None

    speaker_decisions = []
    speaker_cfg = framing_cfg.get("active_speaker", {})
    if speaker_cfg.get("enabled", False) and samples:
        energies = [audio_analyzer.mean_db(s.t, s.t + float(framing_cfg.get("sample_interval_s", 0.5)))
                    for s in samples]
        speaker_decisions = detect_active_speaker(
            samples, energies,
            window_s=float(speaker_cfg.get("window_s", 3.0)),
            min_correlation=float(speaker_cfg.get("min_correlation", 0.25)),
            margin=float(speaker_cfg.get("margin", 0.12)),
            min_hold_s=float(speaker_cfg.get("min_hold_s", 2.0)),
        )

    plan = build_framing_plan(
        samples,
        speaker_decisions=speaker_decisions,
        min_samples_ratio=float(framing_cfg.get("min_samples_ratio", 0.35)),
        two_faces_min_distance_frac=float(framing_cfg.get("two_faces_min_distance_frac", 0.18)),
        vertical_bias=float(framing_cfg.get("vertical_bias", 0.42)),
        smoothing_alpha=float(framing_cfg.get("smoothing_alpha", 0.25)),
        max_speed_frac_per_s=float(framing_cfg.get("max_speed_frac_per_s", 0.12)),
        deadzone_frac=float(framing_cfg.get("deadzone_frac", 0.02)),
        max_keyframes=int(framing_cfg.get("max_keyframes", 60)),
        static_movement_threshold=float(framing_cfg.get("static_movement_threshold", 0.03)),
    )
    return face_hint, plan


def _build_montage(
    candidate: Candidate,
    settings: Settings,
    audio_analyzer: AudioAnalyzer,
    sentences,
    emphasis_scores: dict[int, float],
) -> tuple[MontagePlan, ZoomTrack]:
    """Montage (silences, hesitations) et zooms dynamiques du clip."""
    cfg = settings.editing_module("montage")
    identity = MontagePlan(EditList.identity(candidate.start, candidate.end))
    if not cfg.get("enabled", False):
        return identity, ZoomTrack()

    emphasis_times = [candidate.words[i].start for i in emphasis_scores if i < len(candidate.words)]

    silences_cfg = cfg.get("remove_silences", {})
    fillers_cfg = cfg.get("remove_fillers", {})

    silence_threshold = (
        settings.scoring_params.get("silence_rms_threshold_db", -35)
        + float(silences_cfg.get("silence_threshold_offset_db", 6))
    )

    def _is_silent(start: float, end: float) -> bool:
        return audio_analyzer.mean_db(start, end) < silence_threshold

    plan = build_montage_plan(
        candidate.words, candidate.start, candidate.end,
        sentences=[s for s in sentences if s.start < candidate.end and s.end > candidate.start],
        emphasis_times=emphasis_times,
        remove_silences=bool(silences_cfg.get("enabled", False)),
        remove_fillers=bool(fillers_cfg.get("enabled", False)),
        min_silence_s=float(silences_cfg.get("min_silence_s", 0.55)),
        keep_padding_s=float(silences_cfg.get("keep_padding_s", 0.12)),
        protect_after_question_s=float(silences_cfg.get("protect_after_question_s", 1.2)),
        protect_before_emphasis_s=float(silences_cfg.get("protect_before_emphasis_s", 0.4)),
        filler_terms=fillers_cfg.get("words", []),
        remove_repetitions=bool(fillers_cfg.get("remove_repetitions", True)),
        max_removed_ratio=float(cfg.get("max_removed_ratio", 0.35)),
        is_silent=_is_silent,
    )

    zoom_cfg = cfg.get("dynamic_zoom", {})
    zoom_track = ZoomTrack()
    if zoom_cfg.get("enabled", False):
        zoom_track = build_zoom_track(
            emphasis_times, candidate.start, candidate.end,
            max_zoom=float(zoom_cfg.get("max_zoom", 1.08)),
            attack_s=float(zoom_cfg.get("attack_s", 0.25)),
            hold_s=float(zoom_cfg.get("hold_s", 0.5)),
            release_s=float(zoom_cfg.get("release_s", 0.4)),
            min_gap_s=float(zoom_cfg.get("min_gap_s", 4.0)),
            max_events=int(zoom_cfg.get("max_events", 4)),
        )

    return plan, zoom_track


def _build_captions_for_clip(
    words,
    emphasis_scores: dict[int, float],
    settings: Settings,
    subtitle_style: dict,
    face_hint,
    src_w: int,
    src_h: int,
    reserved_bottom: int = 0,
) -> tuple[Optional[list[CaptionGroup]], Optional[int]]:
    """Blocs de sous-titres du clip + marge verticale eventuellement corrigee.

    `words` est deja recale sur la timeline de sortie (montage applique), donc
    les blocs produits sont directement ceux du .ass incruste ET du .srt
    exporte.

    Les blocs sont construits des que le module est actif, meme pour un style
    historique : ils servent alors uniquement a l'export .srt/.vtt, le rendu
    incruste restant celui du style. La mise en evidence et le repositionnement,
    eux, ne s'appliquent qu'aux styles "smart".
    """
    cfg = settings.editing_module("captions")
    if not cfg.get("enabled", False) or not words:
        return None, None

    is_smart = subtitle_style.get("mode") == "smart"
    emphasis_cfg = cfg.get("emphasis", {})
    scores = emphasis_scores if is_smart else {}

    groups = build_captions(
        words,
        clip_start=0.0,
        max_words_per_group=subtitle_style.get("words_per_group", 4),
        max_chars_per_group=subtitle_style.get("max_chars_per_group", 0) if is_smart else 0,
        sentence_gap_s=0.6,
        break_on_sentence_end=is_smart,
        emphasis_scores=scores,
        max_emphasis_ratio=float(emphasis_cfg.get("max_ratio", 0.18)),
        min_emphasis_score=float(emphasis_cfg.get("min_score", 0.5)),
        min_display_s=subtitle_style.get("min_display_ms", 250) / 1000.0,
        gap_s=subtitle_style.get("gap_ms", 30) / 1000.0,
    )

    margin_v = None
    position_cfg = cfg.get("smart_position", {})
    if is_smart and position_cfg.get("enabled", False):
        rect = compute_crop_rect(src_w, src_h, face_hint)
        face_position = face_center_in_output(face_hint, src_w, src_h, rect)
        font_size = subtitle_style.get("font_size", 64)
        margin_v = choose_margin_v(
            face_position[1] if face_position else None,
            default_margin_v=subtitle_style.get("margin_v", 300),
            text_height_px=int(font_size * float(position_cfg.get("text_height_ratio", 2.2))),
            avoid_half_frac=float(position_cfg.get("avoid_half_frac", 0.16)),
            # Le placement intelligent rapproche les sous-titres du bas quand
            # un visage occupe le cadre. Il n'a aucune raison de savoir qu'un
            # logo est pose la : la bande reservee par le filigrane devient donc
            # son plancher, sinon le texte retomberait dessus.
            min_margin_v=max(int(position_cfg.get("min_margin_v", 140)), reserved_bottom),
        )

    return groups, margin_v


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
