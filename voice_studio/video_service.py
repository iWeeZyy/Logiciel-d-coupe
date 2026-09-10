"""Creation d'une video narree : script -> voix -> sous-titres -> MP4.

Cette couche ne connait pas Qt (comme voice_studio/services.py) : l'interface
lui passe une demande et une fonction de compte rendu, elle appelle les modules
dans l'ordre. Rien de neuf n'est invente ici -- chaque etape est un composant
qui existait deja :

1. LA VOIX : voice_studio/tts.py, le moteur et le cache deja utilises par le
   bloc « Generer une voix ». Aucune deuxieme synthese.
2. LA DUREE REELLE de cette voix : mesuree sur le fichier produit
   (ffprobe), jamais estimee. L'estimation de narration.py sert a prevenir
   AVANT de generer ; a partir d'ici, on ne travaille plus que sur du mesure.
3. LES MINUTAGES : transcription/whisper_engine.py, lance SUR L'AUDIO
   REELLEMENT GENERE. C'est le coeur de la synchronisation : les sous-titres
   ne sont pas cales sur une duree calculee a partir du nombre de mots, mais
   sur les instants ou la voix prononce vraiment chaque mot. Une voix qui
   marque une pause, avale une liaison ou allonge un chiffre reste synchrone.
4. LES SOUS-TITRES : editing/captions.py + video/subtitle_renderer.py, les
   memes styles que les clips (config/subtitles.json).
5. LE RENDU : voice_studio/video_edit.py construit la commande, ffmpeg encode
   une seule fois.

CE QUI EST MIS EN CACHE, ET A QUEL GRAIN. Changer le style de sous-titres ne
doit pas relancer la synthese vocale : elle prend des dizaines de secondes,
elle n'a aucun rapport avec le style, et la refaire pour rien serait une
punition. Deux mecanismes s'en chargent -- la voix et la transcription deja
obtenues sont passees en entree quand elles existent (`narration_wav`,
`narration_transcript`), et si elles ne sont pas fournies, les caches disque de
tts.py et de transcription/cache.py les retrouvent quand meme.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.models import Transcript
from utils.errors import ClipFarmingError
from voice_studio import narration as narration_module
from voice_studio import store, transcription, tts, video_edit

logger = get_logger()

STEP_SCRIPT = "📝 Vérification du script"
STEP_VOICE = "🎤 Génération de la voix"
STEP_MEASURE = "⏱️ Mesure de la narration"
STEP_ALIGN = "🧠 Repérage des mots dans la voix générée"
STEP_SUBTITLES = "💬 Construction des sous-titres"
STEP_FRAMING = "🎯 Recherche du sujet dans l'image"
STEP_RENDER = "🎬 Rendu de la vidéo"
STEP_DONE = "✅ Terminé"

STEPS = [STEP_SCRIPT, STEP_VOICE, STEP_MEASURE, STEP_ALIGN, STEP_SUBTITLES,
         STEP_FRAMING, STEP_RENDER, STEP_DONE]

# Duree d'un apercu. Assez pour juger la voix, le cadrage et les sous-titres,
# assez court pour ne pas attendre un rendu complet a chaque essai.
PREVIEW_S = 15.0

# Plafond d'echantillons de detection de visage pour le suivi du sujet. Une
# video de dix minutes echantillonnee toutes les demi-secondes ferait 1200
# analyses d'image : le suivi couterait plus longtemps que le rendu.
FRAMING_SAMPLE_INTERVAL_S = 0.5
FRAMING_MAX_SAMPLES = 240


class VideoCreationError(ClipFarmingError):
    """Message deja ecrit pour un humain."""


@dataclass
class VideoRequest:
    """Ce que l'utilisateur a demande, tel qu'il l'a demande."""

    source_video: str
    script: str
    out_path: str
    settings: object                              # voice_studio.models.VideoSettings
    voice: object = None                          # tts.Voice ou None
    rate: float = 1.0
    volume: float = 1.0
    sentence_pause_s: float = 0.0
    whisper_model: str = "small"
    language: Optional[str] = None
    device: str = "auto"
    preview: bool = False
    # Voix et minutages deja obtenus : fournis par l'interface quand seul le
    # style, le cadrage ou le son a change (voir l'entete du module).
    narration_wav: str = ""
    narration_transcript: Optional[Transcript] = None


@dataclass
class VideoReport:
    """Ce qui s'est reellement passe."""

    output_path: str = ""
    narration_wav: str = ""
    narration_transcript: Optional[Transcript] = None
    narration_s: float = 0.0
    video_s: float = 0.0
    output_s: float = 0.0
    audio_mode: str = ""
    caption_count: int = 0
    ass_path: str = ""
    is_preview: bool = False
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.output_path) and Path(self.output_path).is_file()


class Reporter:
    """Compte rendu d'avancement, identique a celui de services.py."""

    def __init__(self, on_step: Optional[Callable[[int, str], None]] = None,
                 on_detail: Optional[Callable[[Optional[float], str], None]] = None):
        self._on_step = on_step
        self._on_detail = on_detail
        self.index = 0

    def step(self, label: str) -> None:
        self.index = STEPS.index(label) + 1 if label in STEPS else self.index + 1
        logger.info(f"[Voice Studio vidéo] {self.index}/{len(STEPS)} {label}")
        if self._on_step:
            self._on_step(self.index, label)

    def detail(self, fraction: Optional[float], label: str) -> None:
        if self._on_detail:
            self._on_detail(fraction, label)


def work_dir() -> Path:
    """Dossier de travail du rendu : narration, sous-titres, apercus.

    Durable et non temporaire, pour la meme raison que le cache : refaire un
    rendu avec un autre style ne doit pas re-synthetiser la voix.
    """
    path = store.store_dir() / "video_work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def narration_path_for(request: VideoRequest) -> Path:
    """Chemin STABLE du fichier de narration.

    Stable et non aleatoire : le cache de transcription est indexe sur le
    chemin, la taille et la date du fichier. Un nom different a chaque
    generation ferait retranscrire la meme voix a chaque fois.
    """
    key = tts.cache_key(request.script, request.voice, request.rate,
                        request.volume, request.sentence_pause_s)
    return work_dir() / f"narration_{key}.wav"


def measure_duration(path: str) -> float:
    """Duree reelle d'un media, 0.0 si elle ne peut pas etre mesuree."""
    from video.ffmpeg_utils import video_duration

    try:
        return float(video_duration(path))
    except Exception as error:                    # pragma: no cover - ffprobe absent
        logger.warning(f"Durée non mesurable pour {path} : {error}")
        return 0.0


def subtitle_style_params(style_key: str) -> tuple[str, dict]:
    """Style de sous-titres demande, ou celui par defaut de config/subtitles.json.

    Un style inconnu (config editee a la main, style supprime) retombe sur le
    style par defaut au lieu de faire echouer le rendu : le nom du style
    reellement utilise est renvoye, pour pouvoir le dire.
    """
    from core.config_loader import load_subtitles_config

    config = load_subtitles_config()
    styles = config.get("styles", {}) or {}
    if not styles:
        return "", {}
    default_key = config.get("default_style") or next(iter(styles))
    key = style_key if style_key in styles else default_key
    return key, styles.get(key, {}) or {}


def build_caption_groups(words, style: dict) -> list:
    """Blocs de sous-titres a partir des mots REELS de la voix generee.

    Meme decoupage et meme mise en evidence que les clips : editing/captions.py
    est appele avec les parametres du style, pas avec des valeurs recopiees.
    La mise en evidence se limite ici aux mots-cles et aux chiffres -- le
    signal sonore ("le mot est dit plus fort") n'a pas de sens sur une voix de
    synthese, qui parle a niveau constant.
    """
    from core.config_loader import load_keywords_config
    from editing.captions import build_captions, score_emphasis

    if not words:
        return []

    is_smart = style.get("mode") == "smart"
    keywords = load_keywords_config()
    scores = score_emphasis(
        words,
        keyword_terms=keywords.get("strong_keywords", []),
        weight_overrides=keywords.get("keyword_weight_overrides", {}),
    ) if is_smart else {}

    return build_captions(
        words,
        clip_start=0.0,
        max_words_per_group=style.get("words_per_group", 4),
        max_chars_per_group=style.get("max_chars_per_group", 0) if is_smart else 0,
        sentence_gap_s=0.6,
        break_on_sentence_end=is_smart,
        emphasis_scores=scores,
        min_display_s=style.get("min_display_ms", 250) / 1000.0,
        gap_s=style.get("gap_ms", 30) / 1000.0,
    )


# Toile sur laquelle les styles de config/subtitles.json sont calibres : le
# cadre 9:16 des clips. Tout autre format en derive par proportion.
STYLE_REFERENCE_HEIGHT = 1920


def scale_style(style: dict, out_w: int, out_h: int,
                floor_margin_v: int = 0) -> tuple[dict, int]:
    """Style adapte au cadre de sortie, et marge verticale a utiliser.

    Les styles sont ecrits pour un cadre 1080x1920. En 16:9, la meme taille de
    police occuperait presque le double de la hauteur de l'image : les valeurs
    exprimees en pixels (police, contour, ombre, marge) sont donc mises a
    l'echelle de la hauteur reelle, ce qui garde la MEME proportion a l'ecran.
    Rien n'est etire : la toile ASS vaut la taille de sortie, donc le texte
    garde ses proportions (voir `play_res` dans video/subtitle_renderer.py) --
    sans cela, une toile portrait posee sur une image paysage etire le texte
    horizontalement et place les marges n'importe ou.

    `floor_margin_v` est un plancher, en pixels de sortie : la bande basse
    occupee par le filigrane. Les sous-titres ne redescendent jamais dessous --
    c'est exactement le defaut constate au premier rendu 16:9, ou le texte
    passait derriere le logo.
    """
    factor = max(0.1, float(out_h) / float(STYLE_REFERENCE_HEIGHT))
    scaled = dict(style or {})
    for key, minimum in (("font_size", 12), ("outline_width", 0), ("shadow", 0),
                         ("margin_v", 0)):
        if key in scaled:
            try:
                scaled[key] = max(minimum, int(round(float(scaled[key]) * factor)))
            except (TypeError, ValueError):
                pass
    margin_v = max(int(scaled.get("margin_v", int(300 * factor))), int(floor_margin_v))
    scaled["margin_v"] = margin_v
    return scaled, margin_v


def _watermark_for(settings) -> object | None:
    """Filigrane a poser, ou None. Meme source que le rendu des clips."""
    if not getattr(settings, "watermark_enabled", False):
        return None
    try:
        from core.config_loader import _load_editing_config
        from video.watermark import from_config

        block = (_load_editing_config().get("watermark") or {})
        return from_config(block) if block.get("enabled", False) else None
    except Exception as error:                    # pragma: no cover - config abimee
        logger.warning(f"Filigrane ignoré ({error}).")
        return None


def _framing(request: VideoRequest, video_s: float, reporter: Reporter,
             cancel_token: Optional[CancelToken]) -> tuple[object, object, list]:
    """Cadrage 9:16 : rien, un centre de visage, ou une trajectoire de suivi.

    Reutilise la detection de visages du pipeline video (video/face_detector.py)
    et le meme constructeur de trajectoire (editing/framing.py). En 16:9 il n'y
    a rien a faire : l'image entiere est gardee.
    """
    settings = request.settings
    notes: list = []
    if getattr(settings, "aspect_ratio", "16:9") != "9:16":
        return None, None, notes
    if getattr(settings, "fit", video_edit.FIT_CROP) == video_edit.FIT_WHOLE:
        # Toute l'image est gardee : il n'y a aucun choix a faire sur ce qu'on
        # garde, donc rien a centrer ni a suivre.
        return None, None, notes
    if getattr(settings, "framing", video_edit.FRAMING_CENTER) != video_edit.FRAMING_SUBJECT:
        return None, None, notes

    reporter.step(STEP_FRAMING)
    reporter.detail(None, "détection des visages")
    try:
        from editing.framing import build_framing_plan
        from video.face_detector import crop_hint_from_track, detect_face_track

        samples = detect_face_track(
            request.source_video, 0.0, video_s or 0.0,
            sample_interval_s=FRAMING_SAMPLE_INTERVAL_S,
            max_samples=FRAMING_MAX_SAMPLES,
            max_faces=2,
        )
        if cancel_token is not None:
            cancel_token.check()
        hint = crop_hint_from_track(samples)
        plan = build_framing_plan(samples) if samples else None
        if hint is None and plan is None:
            notes.append("Aucun visage n'a été détecté : le recadrage 9:16 reste centré.")
        return hint, plan, notes
    except Exception as error:
        if type(error).__name__ == "CancelledError":
            raise
        # Le suivi est un confort : son echec ne doit pas empecher le rendu.
        logger.warning(f"Suivi du sujet indisponible ({error}).")
        notes.append("Le suivi du sujet n'a pas pu être calculé : le recadrage "
                     "9:16 reste centré.")
        return None, None, notes


def create_video(request: VideoRequest, reporter: Reporter | None = None,
                 cancel_token: Optional[CancelToken] = None) -> VideoReport:
    """Parcours complet. Leve VideoCreationError, TtsError ou CancelledError --
    toutes portent un message lisible, l'interface n'a rien a reformuler."""
    reporter = reporter or Reporter()
    report = VideoReport(is_preview=bool(request.preview))
    settings = request.settings

    def _check() -> None:
        if cancel_token is not None:
            cancel_token.check()

    reporter.step(STEP_SCRIPT)
    source = Path(request.source_video or "")
    if not source.is_file():
        raise VideoCreationError(
            "La vidéo source n'a pas été trouvée sur le disque :\n"
            f"{request.source_video}\n\n"
            "Elle a peut-être été déplacée ou supprimée depuis son téléchargement.")
    script = narration_module.clean_script(request.script)
    wants_narration = bool(script)
    if not wants_narration and settings.audio_mode != video_edit.AUDIO_KEEP:
        raise VideoCreationError(
            "Il n'y a pas de script à lire : colle ou importe un texte de "
            "narration, ou choisis de garder le son d'origine de la vidéo.")
    _check()

    report.video_s = measure_duration(str(source))

    # 1-2. La voix, puis sa duree reelle.
    narration_wav = ""
    if wants_narration:
        reporter.step(STEP_VOICE)
        target = (Path(request.narration_wav) if request.narration_wav
                  else narration_path_for(request))
        if request.narration_wav and target.is_file():
            reporter.detail(1.0, "voix déjà générée, réutilisée")
        else:
            reporter.detail(None, "synthèse en cours")
            tts.synthesize(script, str(target), voice=request.voice,
                           rate=request.rate, volume=request.volume,
                           sentence_pause_s=request.sentence_pause_s)
        narration_wav = str(target)
        report.narration_wav = narration_wav
        _check()

        reporter.step(STEP_MEASURE)
        report.narration_s = measure_duration(narration_wav)
        if report.narration_s <= 0:
            raise VideoCreationError(
                "La voix a été générée mais sa durée n'a pas pu être mesurée : "
                "ffprobe est introuvable ou le fichier produit est vide.")
        reporter.detail(1.0, f"narration : {report.narration_s:.1f} s")
        _check()

    # Le cadre et le filigrane sont resolus AVANT les sous-titres : la taille de
    # sortie decide de l'echelle du texte, et la bande occupee par le logo
    # decide du plus bas ou les sous-titres peuvent descendre.
    from video.cropper import target_size as cropper_target_size

    out_w, out_h = cropper_target_size(getattr(settings, "aspect_ratio", "16:9"))
    watermark = _watermark_for(settings)
    reserved_bottom = 0
    if watermark is not None:
        from video.watermark import reserved_bottom_px

        reserved_bottom = reserved_bottom_px(watermark, out_w, out_h=out_h)

    # 3. Les minutages, sur l'audio reellement produit.
    caption_groups: list = []
    narration_words: list = []
    ass_path = None
    style_key, style = subtitle_style_params(getattr(settings, "subtitle_style", ""))
    wants_subtitles = bool(getattr(settings, "subtitles_enabled", True)) and wants_narration

    if wants_subtitles:
        reporter.step(STEP_ALIGN)
        transcript = request.narration_transcript
        if transcript is None:
            transcript = transcription.transcribe_media(
                narration_wav, request.whisper_model, request.language, request.device,
                cancel_token=cancel_token, on_progress=reporter.detail,
            )
        else:
            reporter.detail(1.0, "minutages déjà connus, réutilisés")
        report.narration_transcript = transcript
        _check()

        reporter.step(STEP_SUBTITLES)
        narration_words = transcript.words() if transcript else []
        if not narration_words:
            report.notes.append(
                "Aucun mot n'a pu être repéré dans la voix générée : la vidéo "
                "est produite sans sous-titres.")
            wants_subtitles = False
        else:
            caption_groups = build_caption_groups(narration_words, style)
            report.caption_count = len(caption_groups)
            if not style:
                report.notes.append(
                    "Aucun style de sous-titres n'est disponible dans "
                    "config/subtitles.json : la vidéo est produite sans sous-titres.")
                wants_subtitles = False
        _check()

    if wants_subtitles and caption_groups:
        from video.subtitle_renderer import render_ass_file

        ass_path = str(work_dir() / "narration.ass")
        # `narration_words` ET `caption_groups` : les styles "smart" rendent les
        # blocs deja construits, les styles historiques (progressive, classic)
        # travaillent directement sur les mots. Les deux viennent de la meme
        # transcription de la voix generee, donc des memes instants reels.
        scaled_style, margin_v = scale_style(style, out_w, out_h, reserved_bottom)
        render_ass_file(narration_words, clip_start=0.0, style=scaled_style,
                        out_ass_path=ass_path, caption_groups=caption_groups,
                        margin_v=margin_v, play_res=(out_w, out_h))
        report.ass_path = ass_path

    # 4. Le cadre.
    face_hint, framing_plan, framing_notes = _framing(request, report.video_s,
                                                      reporter, cancel_token)
    report.notes.extend(framing_notes)
    _check()

    # 5. Le rendu.
    reporter.step(STEP_RENDER)
    from video.ffmpeg_utils import has_audio_stream, video_fps, video_resolution

    try:
        src_w, src_h = video_resolution(str(source))
    except Exception as error:                    # pragma: no cover - fichier illisible
        raise VideoCreationError(
            "Les dimensions de la vidéo source n'ont pas pu être lues : le "
            f"fichier est peut-être incomplet ou abîmé.\nDétail : {error}") from error

    has_audio = False
    try:
        has_audio = bool(has_audio_stream(str(source)))
    except Exception:                             # pragma: no cover - ffprobe absent
        has_audio = False

    audio_mode, audio_notes = video_edit.resolve_audio_mode(
        getattr(settings, "audio_mode", video_edit.AUDIO_REPLACE),
        has_original_audio=has_audio, has_narration=bool(narration_wav))
    report.audio_mode = audio_mode
    report.notes.extend(audio_notes)

    plan = video_edit.resolve_duration(
        report.video_s, report.narration_s,
        getattr(settings, "duration_policy", video_edit.DURATION_CUT))
    report.notes.extend(plan.notes)

    if request.preview:
        # L'apercu est LE MEME rendu, simplement plus court : ce qu'on voit est
        # ce qui sortira. Un apercu produit autrement pourrait ne pas
        # ressembler au fichier final, et ne servirait donc a rien.
        plan = video_edit.DurationPlan(
            output_s=min(plan.output_s, PREVIEW_S),
            freeze_s=min(plan.freeze_s, PREVIEW_S), notes=())

    args = video_edit.build_render_args(
        video_path=str(source),
        narration_path=narration_wav or None,
        ass_path=ass_path,
        out_path=request.out_path,
        src_w=src_w, src_h=src_h,
        target_size=(out_w, out_h),
        fill=getattr(settings, "fill", "flou"),
        audio_mode=audio_mode,
        duration_plan=plan,
        export_settings=_export_settings(),
        face_hint=face_hint,
        framing_plan=framing_plan,
        original_volume=getattr(settings, "original_volume",
                                video_edit.DEFAULT_ORIGINAL_VOLUME),
        narration_volume=getattr(settings, "narration_volume",
                                 video_edit.DEFAULT_NARRATION_VOLUME),
        watermark=watermark,
        fps=_safe_fps(str(source), video_fps),
        fit=getattr(settings, "fit", video_edit.FIT_CROP),
    )

    Path(request.out_path).parent.mkdir(parents=True, exist_ok=True)
    from video.ffmpeg_utils import run_ffmpeg

    run_ffmpeg(args, description="rendu de la vidéo narrée", cancel_token=cancel_token)

    report.output_path = request.out_path
    report.output_s = plan.output_s
    reporter.step(STEP_DONE)
    reporter.detail(1.0, "vidéo prête")
    return report


def _export_settings() -> dict:
    from core.config_loader import load_export_settings

    return load_export_settings()


def _safe_fps(path: str, reader) -> float:
    try:
        return float(reader(path, 25.0))
    except Exception:                             # pragma: no cover - ffprobe absent
        return 25.0
