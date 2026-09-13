"""Fenetres glissantes sur la timeline -> liste de Candidate (audio+texte).

45s -> analyse -> score du passage -> passage suivant -> ... (schema du cahier
des charges, section "detection automatique des hooks").

POURQUOI LA GRILLE SEULE NE SUFFIT PAS. Le pas vaut clip_duration x
stride_ratio, soit 14,85 s pour un clip de 45 s : une fenetre ne peut donc
commencer qu'a 0, 14,85, 29,7... quelle que soit la structure de la parole. Or
DEUX composantes du score dependent directement de l'endroit ou la fenetre
commence et finit :

  - `_silence_build_up_score` lit `audio.silence_before_s`. Une fenetre qui
    demarre en pleine phrase n'a aucun silence devant elle, donc la composante
    tombe a son plancher (10 sur 100) ; une fenetre qui demarre apres une pause
    obtient 100. Quatre-vingt-dix points d'ecart decides par la grille.
  - `_ending_completeness_score` vaut 100 si le dernier mot termine une phrase,
    40 sinon.

Mesure sur une transcription de 759 s (1702 mots, 220 phrases, clip de 45 s) :
sur 50 fenetres de grille, 6 seulement commencent a moins de 0,25 s d'un debut
de phrase, 45 n'ont aucun silence devant elles et 17 finissent sur une phrase
terminee. Le classement etait donc domine par la chance de la grille sur ces
deux composantes, pas par le contenu.

D'ou les fenetres ANCREES : elles commencent au debut d'une phrase et
finissent a la fin de la derniere phrase qui tient dans la duree demandee. La
grille est conservee a cote -- un monologue sans ponctuation ni pause ne
produit presque aucune phrase, et il faut bien le couvrir.
"""
from __future__ import annotations

import numpy as np

from analysis.audio_analyzer import AudioAnalyzer
from analysis.text_analyzer import TextAnalyzer
from core.logging_setup import get_logger
from core.models import Candidate

logger = get_logger()


def grid_windows(video_duration: float, clip_duration: float,
                 stride_ratio: float) -> list[tuple[float, float]]:
    """Fenetres posees sur une grille reguliere -- le comportement historique.

    Conservees meme quand l'ancrage sur les phrases est actif : un monologue
    sans ponctuation ni pause ne produit presque aucune phrase, et il faut bien
    le couvrir.
    """
    stride = max(1.0, clip_duration * stride_ratio)
    if video_duration <= clip_duration:
        starts = [0.0]
    else:
        starts = list(np.arange(0.0, video_duration - clip_duration + stride, stride))

    windows = []
    for start in starts:
        start = float(max(0.0, start))
        end = float(min(start + clip_duration, video_duration))
        if end <= start or end - start < clip_duration * 0.5:
            # Fenetre de fin trop courte pour etre un clip exploitable.
            continue
        windows.append((start, end))
    return windows


def anchored_windows(sentences, video_duration: float, clip_duration: float,
                     min_duration_ratio: float = 0.6) -> list[tuple[float, float]]:
    """Fenetres calees sur la structure de la parole.

    Chaque phrase ouvre une fenetre ; celle-ci se referme sur la FIN de la
    derniere phrase qui tient entierement dans la duree demandee. Les deux
    bornes tombent donc sur de vraies frontieres de parole, ce qui rend
    `silence_before_s` et la completude de fin significatifs au lieu d'etre
    tires au sort par la grille.

    Une phrase plus longue a elle seule que la duree demandee ne peut pas etre
    refermee proprement : la fenetre est alors coupee a la duree demandee,
    comme la grille l'aurait fait. Et une fenetre trop courte -- une phrase
    isolee suivie d'un long silence -- est ecartee plutot que de produire un
    clip de quelques secondes la ou on en demandait quarante-cinq.

    Fonction PURE : elle ne lit ni audio ni texte, seulement des bornes. C'est
    ce qui permet de la tester sans transcrire quoi que ce soit.
    """
    spans = [s for s in sentences if s.end > s.start]
    if not spans:
        return []

    plancher = clip_duration * max(0.0, min(1.0, min_duration_ratio))
    windows: list[tuple[float, float]] = []
    for index, span in enumerate(spans):
        start = float(max(0.0, span.start))
        limite = start + clip_duration
        end = 0.0
        for suivant in spans[index:]:
            if suivant.end <= limite:
                end = float(suivant.end)
            else:
                break
        if end <= start:
            # Meme la premiere phrase depasse la duree demandee : on coupe,
            # exactement comme la grille.
            end = float(min(limite, video_duration))
        end = float(min(end, video_duration))
        if end - start < plancher:
            continue
        windows.append((start, end))
    return windows


def _candidate_for(audio_analyzer: AudioAnalyzer, text_analyzer: TextAnalyzer,
                   start: float, end: float, min_words_in_window: int):
    """Un Candidate pour cette fenetre, ou None si elle ne porte pas de parole.

    Partage par les deux facons de poser une fenetre : la mesure d'un passage
    ne doit pas dependre de la maniere dont ses bornes ont ete choisies.
    """
    text, text_features = text_analyzer.analyze_window(start, end)
    if text_features.word_count < min_words_in_window:
        # Passage quasi silencieux ou sans parole exploitable : ecarte plutot
        # que de lui attribuer un score artificiellement bas mais non nul.
        return None
    return Candidate(
        start=start,
        end=end,
        text=text,
        words=text_analyzer.words_in_window(start, end),
        audio=audio_analyzer.analyze_window(start, end),
        text_features=text_features,
    )


def generate_candidates(
    audio_analyzer: AudioAnalyzer,
    text_analyzer: TextAnalyzer,
    video_duration: float,
    clip_duration: float,
    stride_ratio: float,
    min_words_in_window: int,
    sentences=None,
    anchored: dict | None = None,
) -> list[Candidate]:
    """`sentences` et `anchored` sont optionnels : sans eux, la liste produite
    est exactement celle de la grille, comme avant l'ajout de l'ancrage."""
    cfg = anchored or {}
    windows = grid_windows(video_duration, clip_duration, stride_ratio) \
        if cfg.get("keep_grid", True) else []
    n_grille = len(windows)

    n_ancrees = 0
    if cfg.get("enabled", False) and sentences:
        ancrees = anchored_windows(sentences, video_duration, clip_duration,
                                   cfg.get("min_duration_ratio", 0.6))
        # La grille passe en premier : son ordre historique est preserve, et
        # une fenetre ancree identique a une fenetre de grille n'est pas
        # comptee deux fois.
        connues = {(round(a, 2), round(b, 2)) for a, b in windows}
        for borne in ancrees:
            cle = (round(borne[0], 2), round(borne[1], 2))
            if cle in connues:
                continue
            connues.add(cle)
            windows.append(borne)
            n_ancrees += 1

    # L'analyseur audio decide de sa strategie de hauteur en fonction du
    # nombre de fenetres : precalculer la piste entiere pour une seule fenetre
    # coutrait plus cher que de la calculer seule.
    planifier = getattr(audio_analyzer, "plan_pitch", None)
    if callable(planifier) and windows:
        moyenne = sum(b - a for a, b in windows) / len(windows)
        planifier(len(windows), moyenne)

    candidates = []
    for start, end in windows:
        candidate = _candidate_for(audio_analyzer, text_analyzer, start, end,
                                   min_words_in_window)
        if candidate is not None:
            candidates.append(candidate)

    stride = max(1.0, clip_duration * stride_ratio)
    detail = f"pas={stride:.1f}s"
    if n_ancrees:
        detail += f", {n_grille} de grille + {n_ancrees} calees sur les phrases"
    logger.info(f"{len(candidates)} fenetres candidates generees ({detail}).")
    return candidates


def whole_video_candidate(
    audio_analyzer: AudioAnalyzer,
    text_analyzer: TextAnalyzer,
    video_duration: float,
) -> Candidate:
    """La source entiere, en UNE fenetre, sans condition.

    Utilise quand la source EST deja un clip (un clip Twitch recupere par le
    Radar). Deux differences volontaires avec generate_candidates :

    - une seule fenetre, donc aucune comparaison : le clip garde ses bornes
      d'origine, celles choisies par la personne qui l'a decoupe ;
    - AUCUN filtre sur le nombre de mots. Le filtre a un sens quand il s'agit
      de choisir un passage parmi d'autres -- il ecarte un blanc. Ici il ferait
      echouer la production d'un clip de reaction, ou personne ne parle
      vraiment, alors qu'il n'y a rien a choisir.

    Le passage est quand meme score : les notes affichees sur la fiche du clip
    en viennent, et le montage se sert des memes mesures.
    """
    end = max(0.05, float(video_duration))
    text, text_features = text_analyzer.analyze_window(0.0, end)
    return Candidate(
        start=0.0,
        end=end,
        text=text,
        words=text_analyzer.words_in_window(0.0, end),
        audio=audio_analyzer.analyze_window(0.0, end),
        text_features=text_features,
    )
