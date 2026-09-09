"""Analyse de contenu d'un clip : signaux, moment cle, descriptif, confiance.

Tests purs -- aucun serveur, aucun modele Whisper, aucun fichier media. Ils
portent sur ce que le module GARANTIT : ne rien inventer, ne jamais couper une
phrase, et rester silencieux plutot que plausible quand le clip ne dit rien.
"""
from __future__ import annotations

import pytest

from core.models import Word
from editing.sentences import build_sentences
from radar.analysis import confidence, describer, key_moment, lexicon
from radar.analysis import transcript_analysis as ta
from radar.analysis.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    ClipAnalysis,
    format_timestamp,
)
from core.text_utils import normalize

LX = lexicon.load()


def words_from(text: str, start: float = 0.0, word_duration: float = 0.25,
               gap: float = 0.05, gaps: dict | None = None) -> list[Word]:
    """Mots horodates a partir d'une phrase ecrite.

    `gaps` permet d'inserer un silence AVANT le n-ieme mot, pour tester ce que
    le module fait d'un blanc -- un silence ne se simule pas autrement, les
    bornes de phrase etant identiques avec ou sans blanc.
    """
    out: list[Word] = []
    t = start
    for index, token in enumerate(text.split()):
        t += (gaps or {}).get(index, 0.0)
        out.append(Word(text=token, start=t, end=t + word_duration, probability=0.9))
        t += word_duration + gap
    return out


def analyse(text: str, *, duration: float | None = None, gaps: dict | None = None,
            deep: bool = False):
    words = words_from(text, gaps=gaps)
    sentences = build_sentences(words)
    total = duration if duration is not None else (words[-1].end if words else 0.0)
    profile = ta.analyze(sentences, LX, duration_s=total)
    emotions = ta.detect_emotions(sentences, LX, profile)
    moment = key_moment.find(sentences, LX, duration_s=total, silences=profile.silences,
                             max_alternatives=2 if deep else 0)
    return sentences, profile, emotions, moment


BAVARD = ("je prepare tranquillement ma prochaine manche sur la carte du desert. "
          "il me reste une seule chance de revenir au score. "
          "attends quoi ? "
          "c'est pas possible ! "
          "ahah je rigole, je n'ai jamais vu ca de ma vie. "
          "bon, du coup on repart pour une partie.")

SILENCIEUX = "bon. voila. c'est fait."


class TestSignauxLinguistiques:
    def test_compte_ce_qui_est_reellement_dans_le_texte(self):
        _s, profile, _e, _m = analyse(BAVARD)
        assert profile.question_count >= 1
        assert profile.exclamation_count >= 1
        assert profile.word_count > 20
        assert profile.speech_density > 0

    def test_un_silence_est_mesure_et_pas_devine(self):
        # 2,5 s de blanc avant le 6e mot.
        _s, profile, _e, _m = analyse("un deux trois quatre cinq six sept huit.",
                                      gaps={5: 2.5})
        assert profile.silences, "le blanc de 2,5 s doit etre releve"
        assert profile.longest_silence_s == pytest.approx(2.5, abs=0.05)

    def test_aucune_parole_ne_produit_aucun_signal_invente(self):
        profile = ta.analyze([], LX, duration_s=12.0)
        assert profile.word_count == 0
        assert profile.signals == ("aucune parole détectée",)
        assert profile.silence_ratio == 1.0

    def test_superlatifs_et_negations_viennent_du_lexique(self):
        _s, profile, _e, _m = analyse("je n'ai jamais vu un truc pareil, c'est le pire moment.")
        assert profile.superlative_count >= 1
        assert profile.negation_count >= 1


class TestEmotions:
    def test_marqueurs_convergents_donnent_une_confiance_haute(self):
        _s, _p, emotions, _m = analyse("ahah je rigole, mdr, trop drole cette situation.")
        rire = [e for e in emotions if e.name == "moment drôle"]
        assert rire and rire[0].confidence == CONFIDENCE_HIGH

    def test_un_marqueur_isole_reste_une_hypothese(self):
        # "attention" seul, sans ponctuation forte : mot parfaitement ordinaire.
        _s, _p, emotions, _m = analyse("attention a la marche en sortant du studio.")
        tendus = [e for e in emotions if e.name == "moment tendu"]
        assert tendus, "le marqueur doit etre releve"
        assert tendus[0].confidence == CONFIDENCE_LOW

    def test_chaque_emotion_cite_la_phrase_qui_la_declenche(self):
        sentences, _p, emotions, _m = analyse(BAVARD)
        source = normalize(" ".join(s.text for s in sentences))
        for emotion in emotions:
            assert emotion.evidence, f"{emotion.name} sans preuve citable"
            assert normalize(emotion.evidence) in source


class TestMomentCle:
    def test_ce_n_est_pas_le_debut_du_clip(self):
        sentences, _p, _e, moment = analyse(BAVARD)
        assert moment is not None
        assert moment.start > sentences[0].start, (
            "le moment cle ne doit pas etre systematiquement les premieres secondes")

    def test_les_bornes_sont_celles_de_phrases_entieres(self):
        sentences, _p, _e, moment = analyse(BAVARD)
        starts = {round(s.start, 3) for s in sentences}
        ends = {round(s.end, 3) for s in sentences}
        assert round(moment.start, 3) in starts
        assert round(moment.end, 3) in ends

    def test_le_contexte_autour_de_la_reaction_est_conserve(self):
        _s, _p, _e, moment = analyse(BAVARD)
        assert moment.reaction_text
        assert moment.setup_text or moment.payoff_text, (
            "une reaction sans rien autour serait incomprehensible")

    def test_le_libelle_decrit_les_signaux_et_ne_raconte_pas_une_scene(self):
        _s, _p, _e, moment = analyse(BAVARD)
        assert "réaction marquée par" in moment.label

    def test_candidats_supplementaires_seulement_en_approfondie(self):
        _s, _p, _e, standard = analyse(BAVARD)
        _s2, _p2, _e2, deep = analyse(BAVARD, deep=True)
        assert standard.alternatives == ()
        assert deep.alternatives, "l'analyse approfondie propose plusieurs candidats"

    def test_pas_de_moment_sans_parole(self):
        assert key_moment.find([], LX) is None


class TestDescriptif:
    def _describe(self, text, **kwargs):
        sentences, profile, emotions, moment = analyse(text)
        return sentences, describer.describe(
            sentences, profile=profile, key_moment=moment, emotions=emotions,
            lexicon=LX, keyword_terms=("carte", "score"), **kwargs)

    def test_tout_le_texte_produit_vient_du_clip(self):
        """Le test central : aucune phrase citee ne doit etre absente du clip."""
        sentences, description = self._describe(BAVARD)
        source = normalize(" ".join(s.text for s in sentences))
        quoted = [part for part in description.summary.split("« ")[1:]]
        assert quoted, "le resume doit citer le clip"
        for chunk in quoted:
            citation = chunk.split(" »")[0]
            assert normalize(citation) in source, f"citation absente du clip : {citation}"

    def test_les_trois_titres_sont_proposes(self):
        _s, description = self._describe(BAVARD)
        assert description.title_direct
        assert description.title_curiosity
        assert description.title_punchy
        assert description.title_punchy == description.title_punchy.upper()

    def test_hashtags_bornes_et_contextuels_pour_un_clip(self):
        _s, description = self._describe(BAVARD, is_clip=True, creator_label="ZeratoR",
                                         category="Just Chatting")
        assert len(description.hashtags) <= LX.max_hashtags
        assert "#Twitch" in description.hashtags
        assert "#ZeratoR" in description.hashtags
        assert all(tag.startswith("#") for tag in description.hashtags)

    def test_pas_de_hashtag_twitch_sur_un_contenu_qui_n_en_est_pas_un(self):
        _s, description = self._describe(BAVARD, is_clip=False)
        assert "#Twitch" not in description.hashtags

    def test_clip_sans_parole_ne_produit_aucun_descriptif(self):
        description = describer.describe([], profile=None, key_moment=None,
                                         emotions=[], lexicon=LX)
        assert description.summary == ""
        assert description.editorial == ""
        assert describer.NO_SPEECH_WARNING in description.warnings

    def test_clip_presque_muet_reste_minimal_et_le_dit(self):
        _s, description = self._describe(SILENCIEUX)
        assert describer.LOW_SPEECH_WARNING in description.warnings
        assert description.editorial == "", "rien ne doit etre invente pour remplir"

    def test_contexte_manquant_signale_et_non_comble(self):
        _s, description = self._describe(
            "et donc du coup il repart avec la voiture rouge sans rien dire a personne.")
        assert describer.CONTEXT_WARNING in description.warnings

    def test_la_description_courte_garde_la_question(self):
        _s, description = self._describe(BAVARD)
        assert description.short.endswith("?") or description.short.endswith(".")

    def test_le_moment_cle_est_date_dans_le_resume(self):
        _s, description = self._describe(BAVARD)
        assert "00:" in description.summary


class TestConfiance:
    def test_bonne_transcription_et_moment_identifie(self):
        _s, profile, _e, moment = analyse(BAVARD)
        report = confidence.evaluate(profile=profile, key_moment=moment,
                                     word_probability=0.9, language_probability=0.99)
        assert report.level == CONFIDENCE_HIGH

    def test_audio_douteux_et_clip_muet(self):
        _s, profile, _e, moment = analyse(SILENCIEUX)
        report = confidence.evaluate(profile=profile, key_moment=moment,
                                     word_probability=0.35, language_probability=0.45)
        assert report.level == CONFIDENCE_LOW
        assert report.reasons

    def test_aucun_pourcentage_n_est_affiche(self):
        _s, profile, _e, moment = analyse(BAVARD)
        report = confidence.evaluate(profile=profile, key_moment=moment)
        assert "%" not in " ".join(report.reasons)
        assert report.level in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

    def test_qualite_non_mesuree_est_annoncee(self):
        _s, profile, _e, moment = analyse(BAVARD)
        report = confidence.evaluate(profile=profile, key_moment=moment)
        assert any("non mesurée" in reason for reason in report.reasons)


class TestLexique:
    def test_config_absente_ne_desactive_pas_l_analyse(self):
        fallback = lexicon.load({})
        assert fallback.emotions
        assert fallback.max_hashtags > 0

    def test_config_partielle_complete_les_defauts(self):
        custom = lexicon.load({"thresholds": {"silence_gap_s": 2.0}})
        assert custom.threshold("silence_gap_s") == 2.0
        assert custom.threshold("short_sentence_words") == 5


class TestModeles:
    def test_une_analyse_survit_a_un_aller_retour_dictionnaire(self):
        analysis = ClipAnalysis(content_id="twitch:abc", summary="ok",
                                hashtags=["#Twitch"], detected_topics=["carte"])
        restored = ClipAnalysis.from_dict(analysis.to_dict())
        assert restored.content_id == "twitch:abc"
        assert restored.hashtags == ["#Twitch"]

    def test_un_champ_inconnu_ne_casse_pas_la_relecture(self):
        restored = ClipAnalysis.from_dict({"content_id": "twitch:x", "champ_du_futur": 1})
        assert restored.content_id == "twitch:x"

    def test_horodatage_lisible(self):
        assert format_timestamp(77.6) == "01:17"
        assert format_timestamp(None) == "--:--"


class TestTransmission:
    """Sections 14 et 15 : ce qu'une analyse peut transmettre, sans rien ecrire."""

    def _analysis(self, **kwargs):
        base = dict(
            content_id="twitch:c1", platform="twitch", creator_label="Streamer B",
            clip_title="Le moment", clip_url="https://clips.twitch.tv/c1",
            duration_s=28.0, radar_score=87.0, language="fr",
            transcript_text="Attends quoi ? C'est pas possible !",
            segments=[{"start": 0.0, "end": 3.0, "text": "Attends quoi ?"}],
            summary="Résumé", description="Description", short_description="Courte",
            social_description="Sociale", hashtags=["#Twitch"],
            title_direct="Direct", title_curiosity="Curiosité", title_punchy="PUNCHY",
            detected_topics=["carte"], detected_signals=["1 question(s)"],
            detected_emotions=[{"name": "surprise", "confidence": "elevee",
                                "evidence": "Attends quoi ?"}],
            speech_density=2.1, silence_ratio=0.12, confidence=CONFIDENCE_HIGH,
            key_moment={"start": 6.0, "end": 15.0, "text": "bloc",
                        "reaction_text": "Attends quoi ?", "label": "réaction"},
        )
        base.update(kwargs)
        return ClipAnalysis(**base)

    def test_le_content_factory_recoit_tout_ce_que_la_section_14_demande(self):
        from radar.analysis.handoff import to_content_factory

        payload = to_content_factory(self._analysis())
        for key in ("transcript", "key_moment", "summary", "description", "titles",
                    "radar_score", "segments"):
            assert key in payload, f"{key} manquant"
        assert payload["key_moment"]["start"] == 6.0
        assert payload["titles"]["punchy"] == "PUNCHY"

    def test_une_valeur_inconnue_est_absente_et_non_mise_a_zero(self):
        from radar.analysis.handoff import to_content_factory, to_performance_features

        vide = ClipAnalysis(content_id="twitch:x")
        payload = to_content_factory(vide)
        assert "radar_score" not in payload
        assert "duration_s" not in payload
        features = to_performance_features(vide)
        assert "duration" not in features
        assert "radar_score" not in features

    def test_l_apprentissage_ne_recoit_aucun_score_du_pipeline_video(self):
        """Un clip Twitch recupere tel quel n'a ni Hook ni Rewatch ni Viral Potential :
        ces scores viennent du decoupage fait par le logiciel. Les mettre a zero
        les ferait passer pour des mesures."""
        from radar.analysis.handoff import to_performance_features

        features = to_performance_features(self._analysis())
        for absent in ("hook_score", "rewatch_score", "viral_potential_score"):
            assert absent not in features

    def test_l_emotion_transmise_porte_sa_confiance(self):
        from radar.analysis.handoff import to_performance_features

        features = to_performance_features(self._analysis())
        assert features["emotion"] == "surprise"
        assert features["emotion_confidence"] == CONFIDENCE_HIGH

    def test_la_transmission_n_ecrit_nulle_part(self, tmp_path):
        """Section 14 : preparer l'integration, ne pas la construire."""
        from radar.analysis import handoff
        from radar.store import RadarStore

        store = RadarStore(path=tmp_path / "radar.sqlite3")
        handoff.to_content_factory(self._analysis())
        handoff.to_performance_features(self._analysis())
        assert store.list_analyses() == []
