"""Les sous-titres disent le SCRIPT, aux instants de la VOIX.

DEFAUT REEL A L'ORIGINE DE CE FICHIER, signale en usage : un script francais
posé sur une video anglaise produisait des sous-titres EN ANGLAIS. Deux causes
distinctes, corrigees separement et couvertes ici :

1. la langue annoncee a Faster-Whisper etait celle de la video source, reprise
   du haut de la page. Forcer une langue que l'audio ne parle pas ne donne pas
   une transcription dans cette langue : Whisper TRADUIT ;
2. le texte des sous-titres etait celui que Whisper avait entendu. Or le texte
   est CONNU -- c'est le script. Whisper ne sert qu'au minutage.

Tests PURS : aucun modele, aucun encodage.
"""
from __future__ import annotations

import pytest

from core.models import Word
from voice_studio import align, video_service
from voice_studio.models import VideoSettings


def _heard(pairs) -> list[Word]:
    """(texte, debut, fin) -> mots horodates, comme Whisper les rend."""
    return [Word(text=t, start=s, end=e, probability=0.9) for t, s, e in pairs]


def _even(text: str, step: float = 0.4) -> list[Word]:
    return _heard([(t, i * step, i * step + step * 0.9)
                   for i, t in enumerate(text.split())])


class TestTexte:
    def test_la_sortie_est_exactement_le_script(self):
        script = "Le bois de construction commence avec un arbre."
        result = align.align_script(script, _even("Le bois de construction commence"))
        assert " ".join(w.text for w in result.words) == script

    def test_aucun_mot_n_est_ajoute_ni_supprime(self):
        script = "Un deux trois quatre cinq six"
        result = align.align_script(script, _even("un deux trois"))
        assert len(result.words) == 6

    def test_la_ponctuation_du_script_est_conservee(self):
        """Elle porte le rythme et l'intonation a la lecture, et elle fait
        partie de ce que l'utilisateur a ecrit."""
        script = "Vous pensez que c'est terminé ? Pas du tout !"
        result = align.align_script(script, _even("vous pensez que c'est termine"))
        rendu = " ".join(w.text for w in result.words)
        assert "?" in rendu and "!" in rendu
        assert rendu == script

    def test_le_texte_entendu_ne_remplace_jamais_le_script(self):
        """Le defaut signale : Whisper avait rendu une traduction anglaise."""
        script = "Ici, le processus commence avec un pin sylvestre."
        heard = _even("Here, the process begins with a Scots pine.")
        result = align.align_script(script, heard)
        rendu = " ".join(w.text for w in result.words)
        assert rendu == script
        assert "process begins" not in rendu

    def test_une_faute_de_reconnaissance_ne_passe_pas_dans_les_sous_titres(self):
        script = "Un pin sylvestre pousse ici."
        result = align.align_script(script, _even("Un pain sylvestre pousse ici."))
        assert "pin" in " ".join(w.text for w in result.words)
        assert "pain" not in " ".join(w.text for w in result.words)


class TestMinutages:
    def test_les_mots_reconnus_gardent_les_instants_de_la_voix(self):
        heard = _heard([("Le", 0.0, 0.30), ("bois", 0.35, 0.70),
                        ("brûle", 0.80, 1.40)])
        result = align.align_script("Le bois brûle", heard)
        assert result.words[0].start == pytest.approx(0.0)
        assert result.words[1].start == pytest.approx(0.35)
        assert result.words[2].end == pytest.approx(1.40)

    def test_un_mot_manquant_est_interpole_entre_ses_voisins_surs(self):
        heard = _heard([("le", 0.0, 0.2), ("arbre", 2.0, 2.4)])
        result = align.align_script("le grand vieux arbre", heard)
        milieu = result.words[1:3]
        assert all(0.2 <= w.start < w.end <= 2.0 for w in milieu)

    def test_l_interpolation_donne_plus_de_temps_aux_mots_longs(self):
        """Repartir a parts egales ferait defiler les sous-titres a
        contretemps : « a » et « extraordinairement » ne durent pas pareil."""
        heard = _heard([("debut", 0.0, 0.2), ("fin", 4.0, 4.2)])
        result = align.align_script("debut a extraordinairement fin", heard)
        court, long = result.words[1], result.words[2]
        assert (long.end - long.start) > (court.end - court.start)

    def test_les_instants_restent_croissants_et_sans_recouvrement(self):
        heard = _heard([("un", 0.0, 0.5), ("cinq", 0.6, 0.9)])
        result = align.align_script("un deux trois quatre cinq six sept", heard)
        for previous, following in zip(result.words, result.words[1:]):
            assert previous.end <= following.start
            assert following.end > following.start

    def test_aucun_bloc_de_duree_nulle(self):
        result = align.align_script("un deux trois", _heard([("un", 1.0, 1.0)]))
        assert all(w.end - w.start >= align.MIN_WORD_S for w in result.words)

    def test_le_script_qui_depasse_la_voix_est_prolonge_sans_empiler(self):
        heard = _heard([("un", 0.0, 0.3)])
        result = align.align_script("un deux trois quatre", heard)
        assert result.words[-1].start > result.words[0].end


class TestConfiance:
    def test_le_taux_d_appariement_est_rendu(self):
        result = align.align_script("un deux trois quatre", _even("un deux"))
        assert result.total == 4
        assert result.matched == 2
        assert result.ratio == pytest.approx(0.5)

    def test_une_transcription_dans_la_mauvaise_langue_est_signalee(self):
        result = align.align_script("Ici le processus commence",
                                    _even("Here the process begins"))
        assert not result.is_reliable, "l'utilisateur doit être averti"

    def test_une_transcription_fidele_est_fiable(self):
        result = align.align_script("Le bois de construction commence",
                                    _even("Le bois de construction commence"))
        assert result.is_reliable
        assert result.ratio == pytest.approx(1.0)

    def test_sans_rien_entendu_aucun_minutage_n_est_invente(self):
        result = align.align_script("Le bois brûle", [])
        assert result.words == []
        assert result.total == 3

    def test_un_script_vide_ne_produit_rien(self):
        result = align.align_script("", _even("un deux"))
        assert result.words == []
        assert result.total == 0


class TestLangueDeLaNarration:
    """La langue annoncee a Whisper vient de la VOIX, jamais de la video."""

    def _request(self, **overrides):
        params = dict(source_video="s.mp4", script="Bonjour", out_path="o.mp4",
                      settings=VideoSettings())
        params.update(overrides)
        return video_service.VideoRequest(**params)

    def test_une_voix_francaise_donne_le_francais(self):
        from voice_studio.tts import Voice

        request = self._request(voice=Voice(id="fr_FR-siwis", label="Siwis",
                                            language="fr-FR", engine="piper"))
        assert video_service.narration_language(request) == "fr"

    def test_une_voix_sans_langue_declaree_laisse_la_detection_faire(self):
        from voice_studio.tts import Voice

        request = self._request(voice=Voice(id="x", label="X", language=""))
        assert video_service.narration_language(request) is None

    def test_sans_voix_du_tout_rien_n_est_impose(self):
        assert video_service.narration_language(self._request()) is None

    def test_une_langue_explicite_reste_prioritaire(self):
        assert video_service.narration_language(self._request(language="es")) == "es"

    def test_la_langue_n_est_plus_reprise_du_haut_de_la_page(self):
        """C'est la cause du defaut : la page transmettait la langue de la
        video source, et le panneau la passait a Whisper."""
        import inspect

        from gui.voice_studio import video_panel

        signature = inspect.signature(video_panel.VideoPanel.set_transcription_options)
        assert "language" not in signature.parameters
