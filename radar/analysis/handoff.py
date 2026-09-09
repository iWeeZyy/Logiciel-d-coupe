"""Ce qu'une analyse peut transmettre, et a qui (sections 14 et 15).

Deux fonctions de traduction, rien de plus. Elles n'ecrivent nulle part,
n'appellent ni le Content Factory ni l'apprentissage, et ne declenchent aucun
traitement : la demande est explicite, PREPARER l'integration sans la construire
maintenant. Une integration ecrite avant d'etre demandee serait une integration
ecrite contre des besoins supposes.

Le point commun des deux : une valeur inconnue est ABSENTE du dictionnaire, elle
n'est pas mise a zero. C'est la difference entre "ce clip n'a pas de Hook Score"
et "ce clip a un Hook Score de 0", et cette difference compte enormement pour un
systeme d'apprentissage -- des zeros inventes tireraient toutes les correlations
vers le bas sans que personne comprenne pourquoi.
"""
from __future__ import annotations

from radar.analysis.models import ClipAnalysis


def to_content_factory(analysis: ClipAnalysis) -> dict:
    """Charge utile pour le Content Factory (section 14).

    Contient ce que la section 14 enumere : transcription, moment cle, resume,
    description, titres, scores et horodatages. Le Content Factory n'est pas
    modifie ; c'est a lui, le jour ou il consommera ces donnees, de choisir ce
    qu'il en fait.
    """
    payload: dict = {
        "content_id": analysis.content_id,
        "platform": analysis.platform,
        "creator": analysis.creator_label,
        "title": analysis.clip_title,
        "url": analysis.clip_url,
        "transcript": analysis.transcript_text,
        "segments": list(analysis.segments),
        "language": analysis.language,
        "summary": analysis.summary,
        "description": analysis.description,
        "short_description": analysis.short_description,
        "social_description": analysis.social_description,
        "hashtags": list(analysis.hashtags),
        "titles": {
            "direct": analysis.title_direct,
            "curiosite": analysis.title_curiosity,
            "punchy": analysis.title_punchy,
        },
        "topics": list(analysis.detected_topics),
        "confidence": analysis.confidence,
        "analysis_level": analysis.analysis_level,
        "analyzed_at": analysis.analyzed_at,
    }

    if analysis.duration_s is not None:
        payload["duration_s"] = analysis.duration_s
    if analysis.radar_score is not None:
        payload["radar_score"] = analysis.radar_score
    if analysis.words:
        payload["words"] = list(analysis.words)

    moment = analysis.key_moment or {}
    if moment:
        payload["key_moment"] = {
            "start": moment.get("start"),
            "end": moment.get("end"),
            "text": moment.get("text", ""),
            "reaction": moment.get("reaction_text", ""),
            "label": moment.get("label", ""),
        }

    return payload


def to_performance_features(analysis: ClipAnalysis) -> dict:
    """Ce qu'une analyse apporte a l'apprentissage existant (section 15).

    Aucun nouveau systeme d'apprentissage n'est cree : performance/ existe deja
    et ce dictionnaire parle SON vocabulaire (voir performance/models.py). Ce qui
    manque ici -- hook_score, rewatch_score, viral_potential_score -- n'est pas
    oublie : ces scores sont produits par le pipeline video sur un clip decoupe
    par le logiciel, et un clip Twitch recupere tel quel n'en a aucun. Les
    ecrire a zero les ferait passer pour mesures.
    """
    features: dict = {
        "clip_id": analysis.content_id,
        "created_at": analysis.analyzed_at,
        "category": analysis.platform,
        "source": "radar",
        "analysis_level": analysis.analysis_level,
        "confidence": analysis.confidence,
        "creator": analysis.creator_label,
    }

    if analysis.duration_s is not None:
        features["duration"] = analysis.duration_s
    if analysis.radar_score is not None:
        features["radar_score"] = analysis.radar_score
    if analysis.speech_density is not None:
        features["word_density"] = analysis.speech_density
    if analysis.silence_ratio is not None:
        features["silence_ratio"] = analysis.silence_ratio

    signals = " ".join(analysis.detected_signals)
    features["question_detected"] = "question" in signals

    if analysis.detected_emotions:
        # Seule l'emotion la mieux etayee est transmise, et sa confiance avec
        # elle : une emotion supposee sur un marqueur isole ne doit pas peser
        # autant qu'une emotion appuyee par plusieurs.
        best = analysis.detected_emotions[0]
        features["emotion"] = best.get("name", "")
        features["emotion_confidence"] = best.get("confidence", "")

    if analysis.detected_topics:
        features["topics"] = list(analysis.detected_topics)

    return features
