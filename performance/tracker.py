"""Enregistrement des caracteristiques d'un clip a sa production (section 11).

Ce module ne MESURE rien. Tout ce qu'il ecrit a deja ete calcule par le
pipeline : les quatre scores, la duree, le contexte, le cadrage retenu, le
montage applique, le style de sous-titres, la variante de miniature. Il en
prend une photo au moment de la production.

Pourquoi une photo plutot qu'un renvoi vers results.json : les reglages, les
seuils et le code evolueront. Une comparaison faite dans six mois doit porter
sur ce qui a REELLEMENT ete produit ce jour-la, pas sur ce que le moteur
d'aujourd'hui recalculerait.

L'identifiant d'un clip est "<projet>/<fichier>" : deux projets peuvent contenir
un clip_01.mp4, et l'historique doit les distinguer sans ambiguite.
"""
from __future__ import annotations

from datetime import datetime, timezone

from performance.models import ClipFeatures


def clip_identifier(project_name: str, file_name: str) -> str:
    return f"{project_name}/{file_name}" if project_name else file_name


def silence_ratio(duration: float, spoken_s: float | None) -> float | None:
    """Part du clip sans parole, a partir du temps de parole deja connu.

    `spoken_s` vient des mots horodates que le pipeline a sous la main ; il
    n'est PAS present dans ClipResult, d'ou son passage explicite. Inconnu ->
    None, jamais 0.0 : "aucun silence" et "non mesure" ne sont pas la meme
    chose, et les confondre fausserait toute correlation ulterieure.
    """
    if spoken_s is None or duration <= 0:
        return None
    return round(max(0.0, min(1.0, 1.0 - spoken_s / duration)), 3)


def features_from_clip(clip_result, project_name: str, priority: float | None = None,
                       word_count: int | None = None, spoken_s: float | None = None,
                       subtitle_style: str = "") -> ClipFeatures:
    """Fiche technique d'un clip produit, a partir de son seul ClipResult."""
    scores = clip_result.scores or {}
    context = clip_result.context or {}
    framing = clip_result.framing or {}
    metadata = clip_result.metadata or {}
    titles = metadata.get("titles") or []

    duration = clip_result.duration or 0.0
    words = word_count if word_count is not None else len((clip_result.transcript or "").split())

    return ClipFeatures(
        clip_id=clip_identifier(project_name, clip_result.file_name),
        project=project_name,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        hook_score=float(scores.get("total", 0.0)),
        rewatch_score=float(scores.get("rewatch", 0.0)),
        content_score=float(scores.get("content", 0.0)),
        viral_potential_score=float(scores.get("viral", 0.0)),
        priority_score=priority,
        duration=duration,
        word_density=round(words / duration, 2) if duration > 0 else 0.0,
        audio_intensity=float(scores.get("audio", 0.0)),
        silence_ratio=silence_ratio(duration, spoken_s),
        # Le detecteur de hooks note deja les questions : on relit sa raison
        # plutot que de re-analyser le texte, ce qui donnerait deux definitions
        # concurrentes de "une question a ete detectee".
        question_detected=any("question" in r.lower() for r in (clip_result.reasons or [])),
        loop_potential=float(scores.get("rewatch", 0.0)) or None,
        context_quality=float(context["confidence"]) * 100.0 if "confidence" in context else None,
        category=context.get("category", ""),
        # Le style de sous-titres est un reglage du run, pas une propriete du
        # clip : il vient de l'appelant, seul a le connaitre.
        subtitle_style=subtitle_style,
        title_style=(titles[0].get("kind", "") if titles else ""),
        # Variante de miniature reellement retenue : la premiere generee est
        # celle affichee par defaut. Vide s'il n'y en a aucune.
        thumbnail_variant=(clip_result.thumbnails[0].rsplit("_", 1)[-1].split(".")[0]
                           if clip_result.thumbnails else ""),
        framing_mode=framing.get("mode", ""),
        montage_applied=bool(clip_result.montage),
    )


def record_production(store, clip_results, project_name: str,
                      priorities: dict | None = None,
                      spoken_seconds: dict | None = None,
                      subtitle_style: str = "") -> list[ClipFeatures]:
    """Enregistre la fiche de chaque clip produit. Renvoie ce qui a ete ecrit.

    Une erreur d'ecriture ne doit jamais faire echouer une production qui a
    reussi : les clips existent, ce sont eux le resultat attendu.
    """
    priorities = priorities or {}
    spoken_seconds = spoken_seconds or {}
    features = [
        features_from_clip(
            clip, project_name,
            priority=priorities.get(clip.index),
            spoken_s=spoken_seconds.get(clip.index),
            subtitle_style=subtitle_style,
        )
        for clip in clip_results
    ]
    store.save_features(features)
    return features
