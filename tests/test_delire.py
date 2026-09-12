"""Montage delire : ou les effets tombent, combien, et pas plus.

Le planificateur (editing/delire.py) et le traducteur
(video/delire_filters.py) sont PURS : aucun encodage n'est necessaire pour les
verifier, donc ces tests tournent en quelques millisecondes.

CE QU'ILS DEFENDENT, par ordre d'importance :
  * LA DUREE NE CHANGE JAMAIS. C'est la promesse qui tient tout le reste :
    sous-titres et audio sont cales en temps absolu, un seul effet qui
    allongerait la video les desynchroniserait tous.
  * LES BORNES. Un effet toutes les deux secondes donne une video que personne
    ne regarde : nombre par minute, ecart minimal, part maximale du clip.
  * LE SILENCE PAR DEFAUT. Pas de moment marquant, pas d'effet. Et le module
    desactive doit produire EXACTEMENT ce qui sortait avant son existence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from editing import delire
from video import delire_filters

REPO = Path(__file__).resolve().parents[1]


def _config() -> dict:
    data = json.loads((REPO / "config" / "editing.json").read_text(encoding="utf-8"))
    return data["delire"]


class TestDesactiveParDefaut:
    """Un parti pris esthetique ne s'impose pas a quelqu'un qui ne l'a pas
    demande."""

    def test_le_module_est_coupe_dans_la_configuration(self):
        assert _config()["enabled"] is False

    def test_un_plan_vide_ne_produit_aucun_filtre(self):
        assert delire_filters.build_filters(delire.Plan()) == []
        assert delire_filters.build_chain(delire.Plan()) == ""

    def test_aucun_plan_du_tout_ne_produit_aucun_filtre(self):
        assert delire_filters.build_filters(None) == []

    def test_la_chaine_video_est_identique_sans_plan(self):
        """LA REGRESSION A EVITER : un clip sans delire doit sortir exactement
        comme avant que ce module existe."""
        from video.filter_graph import build_video_chain

        params = dict(edit_list=None, framing_plan=None, zoom_track=None,
                      src_w=1920, src_h=1080, fps=30.0, ass_path="/tmp/x.ass",
                      target_size=(1080, 1920), fit="entier")
        assert build_video_chain(**params) == \
            build_video_chain(**params, delire_plan=delire.Plan())


class TestOuTombentLesEffets:
    def test_aucun_moment_marquant_aucun_effet(self):
        """Decorer au hasard serait pire que ne rien faire."""
        plan = delire.build_plan([], 0.0, 30.0)
        assert plan.is_empty
        assert "aucun moment" in " ".join(plan.reasons)

    def test_les_effets_suivent_les_moments_marquants(self):
        plan = delire.build_plan([5.0, 15.0, 25.0], 0.0, 30.0, seed=1)
        for event, instant in zip(plan.events, (5.0, 15.0, 25.0)):
            assert event.start <= instant <= event.end + 0.01

    def test_l_effet_commence_AVANT_le_mot(self):
        """L'oeil doit etre deja sur l'image quand le mot tombe ; un effet qui
        demarre apres arrive trop tard."""
        plan = delire.build_plan([10.0], 0.0, 30.0, level="doux", seed=1)
        assert plan.events[0].start < 10.0

    def test_les_temps_sont_relatifs_au_debut_du_clip(self):
        """`enable=between(t,...)` de ffmpeg compte depuis le debut de la
        SORTIE, pas depuis le debut de la video source."""
        plan = delire.build_plan([105.0], 100.0, 130.0, seed=1)
        assert 0.0 <= plan.events[0].start <= 6.0

    def test_rien_au_tout_debut_ni_a_la_toute_fin(self):
        """Un effet sur la premiere image passe pour un defaut d'encodage."""
        plan = delire.build_plan([0.05, 29.98], 0.0, 30.0, seed=1)
        assert plan.is_empty

    def test_un_clip_vide_ne_plante_pas(self):
        assert delire.build_plan([1.0], 5.0, 5.0).is_empty


class TestLesBornes:
    LONG = [float(t) for t in range(1, 59)]      # un moment par seconde

    def test_le_nombre_par_minute_est_respecte(self):
        for level in delire.LEVELS:
            plan = delire.build_plan(self.LONG, 0.0, 60.0, level=level, seed=2)
            budget = delire.rules_for(level)["events_per_minute"]
            assert len(plan.events) <= budget + 1, level

    def test_l_ecart_minimal_est_respecte(self):
        for level in delire.LEVELS:
            plan = delire.build_plan(self.LONG, 0.0, 60.0, level=level, seed=2)
            ecart = delire.rules_for(level)["min_gap_s"]
            for avant, apres in zip(plan.events, plan.events[1:]):
                assert apres.start - avant.end >= ecart - 0.01, level

    def test_la_part_du_clip_sous_effet_est_plafonnee(self):
        for level in delire.LEVELS:
            plan = delire.build_plan(self.LONG, 0.0, 60.0, level=level, seed=2)
            plafond = delire.rules_for(level)["max_ratio"] * 60.0
            assert plan.covered_s <= plafond + 0.01, level

    def test_aucun_chevauchement(self):
        """Deux effets superposes s'additionnent, et le resultat n'est plus
        previsible."""
        plan = delire.build_plan(self.LONG, 0.0, 60.0, level="maximum", seed=4)
        for avant, apres in zip(plan.events, plan.events[1:]):
            assert avant.end <= apres.start

    def test_tout_reste_dans_les_bornes_du_clip(self):
        plan = delire.build_plan(self.LONG, 0.0, 60.0, level="maximum", seed=5)
        for event in plan.events:
            assert 0.0 <= event.start < event.end <= 60.0

    def test_un_clip_court_recoit_moins_d_effets_qu_un_long(self):
        court = delire.build_plan([2.0, 4.0, 6.0], 0.0, 8.0, level="moyen", seed=6)
        long = delire.build_plan(self.LONG, 0.0, 60.0, level="moyen", seed=6)
        assert len(court.events) < len(long.events)


class TestLesCrans:
    def test_les_trois_crans_existent(self):
        assert delire.LEVELS == ("doux", "moyen", "maximum")

    def test_chaque_cran_ouvre_de_nouveaux_effets(self):
        """Ce ne sont pas trois forces du meme effet."""
        precedent = ()
        for level in delire.LEVELS:
            kinds = delire.rules_for(level)["kinds"]
            assert set(precedent).issubset(set(kinds)), level
            precedent = kinds
        assert set(delire.rules_for("maximum")["kinds"]) == set(delire.EFFECTS)

    def test_doux_est_plus_sage_que_maximum(self):
        doux = delire.rules_for("doux")
        fort = delire.rules_for("maximum")
        assert doux["events_per_minute"] < fort["events_per_minute"]
        assert doux["min_gap_s"] > fort["min_gap_s"]
        assert doux["max_ratio"] < fort["max_ratio"]

    def test_un_cran_inconnu_retombe_sur_le_defaut(self):
        assert delire.rules_for("survolté") == delire.rules_for(delire.DEFAULT_LEVEL)

    def test_le_cran_est_rappele_dans_le_plan(self):
        plan = delire.build_plan([5.0], 0.0, 30.0, level="doux", seed=1)
        assert plan.level == "doux"


class TestReproductibilite:
    def test_la_meme_graine_donne_le_meme_plan(self):
        """Sans cela, comparer deux reglages serait impossible."""
        premier = delire.build_plan([5.0, 12.0, 20.0], 0.0, 30.0, seed=42)
        second = delire.build_plan([5.0, 12.0, 20.0], 0.0, 30.0, seed=42)
        assert premier.to_dict() == second.to_dict()

    def test_deux_graines_donnent_des_effets_differents(self):
        instants = [float(t) for t in range(2, 58, 3)]
        a = delire.build_plan(instants, 0.0, 60.0, level="maximum", seed=1)
        b = delire.build_plan(instants, 0.0, 60.0, level="maximum", seed=2)
        assert [e.kind for e in a.events] != [e.kind for e in b.events]

    def test_sans_graine_le_debut_du_clip_en_tient_lieu(self):
        """Deux clips differents de la meme video n'ont pas les memes effets,
        mais rejouer le meme clip redonne le meme resultat."""
        premier = delire.build_plan([12.0], 10.0, 40.0)
        second = delire.build_plan([12.0], 10.0, 40.0)
        assert premier.to_dict() == second.to_dict()


class TestTraductionEnFiltres:
    def _un(self, kind: str, strength: float = 1.0):
        return delire.Event(kind=kind, start=1.0, end=1.5, strength=strength)

    def _plan(self, *events):
        return delire.Plan(events=tuple(events))

    @pytest.mark.parametrize("kind", delire.EFFECTS)
    def test_chaque_effet_produit_au_moins_un_filtre(self, kind):
        assert delire_filters.build_filters(self._plan(self._un(kind)))

    @pytest.mark.parametrize("kind", delire.EFFECTS)
    def test_chaque_filtre_est_borne_dans_le_temps(self, kind):
        """Un filtre sans `enable` s'appliquerait a TOUT le clip."""
        for filtre in delire_filters.build_filters(self._plan(self._un(kind))):
            assert "enable='between(t," in filtre, filtre

    @pytest.mark.parametrize("kind", delire.EFFECTS)
    def test_aucun_filtre_ne_touche_a_la_duree(self, kind):
        """LA PROMESSE CENTRALE. Ces filtres deplaceraient tout ce qui suit."""
        interdits = ("setpts", "atempo", "trim", "reverse", "tpad", "fps=",
                     "framerate", "loop")
        for filtre in delire_filters.build_filters(self._plan(self._un(kind))):
            for interdit in interdits:
                assert interdit not in filtre, (kind, filtre, interdit)

    def test_un_effet_inconnu_est_ignore_et_non_remplace(self):
        """Un plan venu d'une version plus recente ne doit pas produire un
        effet que personne n'a demande."""
        assert delire_filters.build_filters(self._plan(self._un("teleportation"))) == []

    def test_un_effet_de_duree_nulle_est_ignore(self):
        vide = delire.Event(kind=delire.GLITCH, start=2.0, end=2.0)
        assert delire_filters.build_filters(self._plan(vide)) == []

    def test_la_force_change_l_amplitude(self):
        faible = delire_filters.build_filters(self._plan(self._un(delire.PIXEL, 0.0)))
        forte = delire_filters.build_filters(self._plan(self._un(delire.PIXEL, 1.0)))
        assert faible != forte

    def test_le_glitch_est_decoupe_en_tranches(self):
        """Un decalage constant ressemble a une image mal imprimee ; c'est le
        tremblement qui evoque un signal qui lache. Et rgbashift n'accepte
        qu'un ENTIER, pas une expression -- verifie dans ffmpeg."""
        filtres = delire_filters.build_filters(self._plan(self._un(delire.GLITCH)))
        assert len(filtres) >= 2
        for filtre in filtres:
            assert "sin(" not in filtre, "rgbashift n'evalue pas les expressions"

    def test_les_tranches_du_glitch_couvrent_l_intervalle_sans_trou(self):
        event = self._un(delire.GLITCH)
        bornes = []
        for filtre in delire_filters.build_filters(self._plan(event)):
            texte = filtre.split("between(t,")[1].split(")")[0]
            debut, fin = (float(v) for v in texte.split(","))
            bornes.append((debut, fin))
        assert bornes[0][0] == pytest.approx(event.start, abs=0.01)
        assert bornes[-1][1] == pytest.approx(event.end, abs=0.01)
        for avant, apres in zip(bornes, bornes[1:]):
            assert avant[1] == pytest.approx(apres[0], abs=0.01)

    def test_les_effets_tres_courts_le_restent(self):
        """Un eclair d'une demi-seconde n'est plus une ponctuation, c'est une
        panne."""
        instants = [float(t) for t in range(2, 58, 2)]
        plan = delire.build_plan(instants, 0.0, 60.0, level="maximum", seed=9)
        for event in plan.events:
            if event.kind in (delire.FLASH, delire.BLIP):
                assert event.duration <= 0.15, event


class TestPlaceDansLaChaine:
    def test_les_effets_passent_avant_les_sous_titres(self):
        """Un texte qui glitche n'est plus lisible, et l'interet d'un
        sous-titre est qu'on le lise."""
        from video.filter_graph import build_video_chain

        plan = delire.build_plan([5.0], 0.0, 20.0, level="maximum", seed=1)
        chain = build_video_chain(
            edit_list=None, framing_plan=None, zoom_track=None,
            src_w=1920, src_h=1080, fps=30.0, ass_path="/tmp/x.ass",
            target_size=(1080, 1920), fit="entier", delire_plan=plan)
        assert "subtitles=" in chain
        premier_effet = min(chain.index(f.split("=")[0])
                            for f in delire_filters.build_filters(plan))
        assert premier_effet < chain.index("subtitles=")

    def test_le_format_paysage_recoit_aussi_les_effets(self):
        from video.filter_graph import build_video_chain

        plan = delire.build_plan([5.0], 0.0, 20.0, level="maximum", seed=1)
        chain = build_video_chain(
            edit_list=None, framing_plan=None, zoom_track=None,
            src_w=1920, src_h=1080, fps=30.0, ass_path=None,
            target_size=(1920, 1080), delire_plan=plan)
        assert any(f.split("=")[0] in chain
                   for f in delire_filters.build_filters(plan))


class TestInterfaceDeLAccueil:
    @pytest.fixture
    def box(self):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.widgets.production_options import ProductionOptionsBox

        QApplication.instance() or QApplication([])
        return ProductionOptionsBox(columns=3)

    def test_la_case_est_decochee_au_demarrage(self, box):
        assert not box.boxes["delire"].isChecked()

    def test_le_cran_est_grise_tant_que_la_case_est_decochee(self, box):
        assert not box.delire_combo.isEnabled()

    def test_il_s_active_avec_la_case(self, box):
        box.set_module_enabled("delire", True)
        assert box.delire_combo.isEnabled()

    def test_les_trois_crans_sont_proposes(self, box):
        cles = [box.delire_combo.itemData(i)
                for i in range(box.delire_combo.count())]
        assert tuple(cles) == delire.LEVELS

    def test_le_cran_voyage_avec_l_etat_de_la_case(self, box):
        box.set_module_enabled("delire", True)
        box.set_delire_level("maximum")
        assert box.editing_overrides()["delire"] == {"enabled": True,
                                                     "level": "maximum"}

    def test_les_autres_modules_restent_actifs_par_defaut(self, box):
        """Ajouter un module decoche ne devait pas decocher les autres."""
        overrides = box.editing_overrides()
        for cle in ("captions", "framing", "montage", "metadata"):
            valeur = overrides[cle]
            assert (valeur["enabled"] if isinstance(valeur, dict) else valeur)


class TestLEffetSuitLeSens:
    """« Que ce ne soit pas trop aléatoire. »

    Le choix de l'effet ne doit pas etre un tirage : un mot de colere, un rire
    et un chiffre ne meritent pas le meme traitement. Le SENS choisit la
    famille d'effets, et il la choisit toujours de la meme facon.
    """

    def _moment(self, texte: str, **signaux):
        return delire.Moment(t=5.0, text=texte, **signaux)

    def test_la_colere_est_reconnue(self):
        assert delire.classify(self._moment("Putain")) == "colere"

    def test_le_rire_est_reconnu(self):
        for mot in ("mdr", "MDR", "ptdr", "haha"):
            assert delire.classify(self._moment(mot)) == "rire", mot

    def test_la_surprise_est_reconnue(self):
        assert delire.classify(self._moment("Sérieux ?!")) == "surprise"

    def test_les_accents_et_la_ponctuation_ne_genent_pas(self):
        """« Sérieux ?! » et « serieux » doivent tomber sur la meme entree."""
        assert delire.classify(self._moment("sérieux")) == \
            delire.classify(self._moment("SERIEUX..."))

    def test_un_chiffre_est_reconnu(self):
        assert delire.classify(self._moment("42", digit=True)) == "chiffre"

    def test_un_cri_est_reconnu(self):
        assert delire.classify(self._moment("maintenant", very_loud=True)) == "crie"

    def test_une_question_est_reconnue(self):
        assert delire.classify(self._moment("alors", question=True)) == "question"

    def test_un_mot_cle_configure_est_reconnu(self):
        assert delire.classify(self._moment("clutch", keyword=True)) == "motcle"

    def test_un_mot_ordinaire_retombe_sur_le_defaut(self):
        """Plutot que d'etre ignore : c'est un moment marquant, il a droit a un
        effet, simplement pas a un effet particulier."""
        assert delire.classify(self._moment("table")) == "defaut"

    def test_ce_qui_est_DIT_passe_avant_ce_qui_est_ENTENDU(self):
        """LE TEST QUI COMPTE. Un juron hurle est d'abord un juron. Dans
        l'autre ordre, le lexique ne servirait jamais : une montee de volume
        accompagne presque toujours un mot fort."""
        crie = self._moment("putain", very_loud=True, loud=True, keyword=True)
        assert delire.classify(crie) == "colere"

    def test_un_rire_crie_reste_un_rire(self):
        assert delire.classify(self._moment("mdr", very_loud=True)) == "rire"

    def test_l_ordre_de_priorite_est_declare_explicitement(self):
        assert delire.CUE_ORDER[:3] == ("colere", "rire", "surprise")
        assert delire.CUE_ORDER[-1] == "defaut"

    def test_le_lexique_est_surchargeable_par_signal(self):
        """Redefinir un signal ne doit pas effacer les autres."""
        cues = {nom: dict(e) for nom, e in delire.DEFAULT_CUES.items()}
        cues["colere"] = {"words": ["flûte"], "effects": [delire.PIXEL]}
        assert delire.classify(self._moment("flûte"), cues) == "colere"
        assert delire.classify(self._moment("putain"), cues) == "defaut"
        assert delire.classify(self._moment("mdr"), cues) == "rire"

    def test_chaque_signal_du_catalogue_a_une_famille_d_effets(self):
        for nom in delire.CUE_ORDER:
            effets = delire.DEFAULT_CUES[nom]["effects"]
            assert effets, nom
            for effet in effets:
                assert effet in delire.EFFECTS, (nom, effet)

    def test_la_configuration_livree_reprend_le_catalogue(self):
        livree = _config()["cues"]
        assert set(livree) == set(delire.DEFAULT_CUES)
        for nom, entree in delire.DEFAULT_CUES.items():
            assert livree[nom]["effects"] == list(entree["effects"]), nom


class TestMemeSensMemeFamille:
    def _plan(self, mots, **kw):
        moments = [delire.Moment(t=2.0 + 5.0 * i, text=mot)
                   for i, mot in enumerate(mots)]
        return delire.build_plan(moments, 0.0, 60.0, level="maximum", **kw)

    def test_le_signal_est_conserve_dans_le_plan(self):
        """Un montage qu'on ne sait pas expliquer ne se corrige pas."""
        plan = self._plan(["putain", "mdr", "quoi"], seed=1)
        assert [e.cue for e in plan.events] == ["colere", "rire", "surprise"]
        assert [e.word for e in plan.events] == ["putain", "mdr", "quoi"]

    def test_chaque_effet_appartient_a_la_famille_de_son_signal(self):
        plan = self._plan(["putain", "mdr", "quoi", "table"], seed=3)
        for event in plan.events:
            famille = delire.DEFAULT_CUES[event.cue]["effects"]
            assert event.kind in famille, (event.cue, event.kind)

    def test_le_meme_mot_donne_toujours_le_meme_signal(self):
        premier = self._plan(["putain", "table", "putain"], seed=5)
        signaux = [e.cue for e in premier.events]
        assert signaux[0] == signaux[2] == "colere"

    def test_le_cran_a_le_dernier_mot_sur_la_famille(self):
        """« Doux » n'autorise que glitch et VHS. Une surprise appelle un
        eclair, mais le cran doit gagner -- sinon il ne voudrait plus rien
        dire."""
        moments = [delire.Moment(t=5.0, text="quoi")]
        plan = delire.build_plan(moments, 0.0, 30.0, level="doux", seed=1)
        assert plan.events[0].cue == "surprise"
        assert plan.events[0].kind in delire.rules_for("doux")["kinds"]

    def test_jamais_deux_fois_le_meme_effet_de_suite(self):
        """Un clip entier de rires ne doit pas etre une seule texture."""
        plan = self._plan(["mdr"] * 8, seed=7)
        assert len(plan.events) >= 3
        for avant, apres in zip(plan.events, plan.events[1:]):
            assert avant.kind != apres.kind

    def test_un_signal_a_famille_unique_peut_se_repeter(self):
        """La regle precedente ne doit pas bloquer un plan quand il n'y a qu'un
        seul effet possible."""
        cues = {nom: dict(e) for nom, e in delire.DEFAULT_CUES.items()}
        cues["rire"] = {"words": ["mdr"], "effects": [delire.PIXEL]}
        moments = [delire.Moment(t=2.0 + 5.0 * i, text="mdr") for i in range(4)]
        plan = delire.build_plan(moments, 0.0, 60.0, level="maximum",
                                 seed=1, cues=cues)
        assert len(plan.events) >= 3
        assert {e.kind for e in plan.events} == {delire.PIXEL}

    def test_le_compte_par_signal_est_rapporte(self):
        plan = self._plan(["mdr", "mdr", "putain"], seed=2)
        assert "rire x2" in " ".join(plan.reasons)

    def test_des_instants_nus_restent_acceptes(self):
        """Mode degrade : sans signal, tout vient de la famille « defaut ».
        Le montage reste borne, il ne suit simplement plus ce qui est dit."""
        plan = delire.build_plan([5.0, 15.0], 0.0, 30.0, level="maximum", seed=1)
        assert not plan.is_empty
        assert {e.cue for e in plan.events} == {"defaut"}

    def test_deux_clips_differents_ne_se_ressemblent_pas(self):
        """« Que les clips ne se ressemblent pas. » Le contenu differe, donc
        les signaux different, donc les effets different."""
        colere = self._plan(["putain", "merde", "bordel"], seed=1)
        rires = self._plan(["mdr", "ptdr", "haha"], seed=1)
        assert [e.kind for e in colere.events] != [e.kind for e in rires.events]

    def test_deux_graines_varient_le_membre_pas_la_famille(self):
        """Le sens reste, l'habillage bouge."""
        a = self._plan(["mdr", "mdr", "mdr"], seed=11)
        b = self._plan(["mdr", "mdr", "mdr"], seed=12)
        assert [e.cue for e in a.events] == [e.cue for e in b.events]
        for event in list(a.events) + list(b.events):
            assert event.kind in delire.DEFAULT_CUES["rire"]["effects"]
