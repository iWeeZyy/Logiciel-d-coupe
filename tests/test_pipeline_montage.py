"""`_build_montage` doit TOUJOURS renvoyer un triplet.

BUG REPRODUIT : « Erreur inattendue : not enough values to unpack (expected
3, got 2) » a l'etape « Generation des clips », signale en usage reel avec le
module montage desactive.

CAUSE : `_build_montage` promet un triplet
(MontagePlan, ZoomTrack, DelirePlan) -- c'est sa signature, et c'est ce que
`run_pipeline` deballe (`montage_plan, zoom_track, delire_plan = ...`). Le
chemin de sortie anticipee (module montage desactive) ne renvoyait qu'un
couple (MontagePlan, ZoomTrack), oubli du troisieme element quand le module
delire a ete ajoute apres coup. Le module delire est INDEPENDANT du module
montage (chacun sa propre case dans editing.json / l'accueil) : desactiver
« Montage auto » seul suffit a declencher ce chemin, quel que soit l'etat du
module delire.

Ce test appelle `_build_montage` directement plutot que `run_pipeline` en
entier : la fonction est pure une fois l'AudioAnalyzer fourni, et le chemin en
cause ne le consulte meme pas (retour avant toute lecture audio) -- un stub
suffit, sans fichier .wav de test.
"""
from __future__ import annotations

from core.config_loader import Settings
from core.models import Candidate, Word
from editing.delire import Plan as DelirePlan
from editing.silence_cut import MontagePlan
from editing.zoom import ZoomTrack
from pipeline import _build_montage


class AudioAnalyzerJamaisAppele:
    """N'importe quel appel signale que le chemin « module desactive » ne
    retourne plus avant toute lecture audio -- ce serait un changement de
    comportement, pas seulement un defaut de test."""

    def mean_db(self, start, end):
        raise AssertionError(
            "le chemin 'montage desactive' ne doit consulter aucun signal audio")


def _mot(text, start, end):
    return Word(text=text, start=start, end=end, probability=0.99)


def _candidat():
    mots = [_mot("Bonjour", 0.0, 0.4), _mot("a", 0.4, 0.5), _mot("tous", 0.5, 0.9)]
    return Candidate(start=0.0, end=1.0, text="Bonjour a tous", words=mots,
                     audio={}, text_features={})


def _settings(editing: dict) -> Settings:
    return Settings(editing=editing)


class TestLeTripletEstToujoursRendu:
    def test_module_montage_desactive_seul(self):
        """LE SCENARIO EXACT DU BUG : montage coupe, rien dit du module delire
        (absent de la configuration -- comme un editing.json qui ne l'a pas
        encore, ou un editing_overrides partiel)."""
        settings = _settings({"montage": {"enabled": False}})
        resultat = _build_montage(
            _candidat(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert len(resultat) == 3
        montage_plan, zoom_track, delire_plan = resultat  # ne doit pas lever
        assert isinstance(montage_plan, MontagePlan)
        assert isinstance(zoom_track, ZoomTrack)
        assert isinstance(delire_plan, DelirePlan)

    def test_module_montage_desactive_delire_actif(self):
        """Le module delire est INDEPENDANT : meme actif dans la
        configuration, il n'est jamais consulte quand le montage est coupe --
        c'est le montage seul qui commande ce chemin de sortie."""
        settings = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": True, "level": "chaos"},
        })
        montage_plan, zoom_track, delire_plan = _build_montage(
            _candidat(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        # Plan par defaut (vide), pas celui que le module delire aurait
        # construit -- puisqu'il n'a pas ete consulte.
        assert delire_plan == DelirePlan()

    def test_module_montage_desactive_l_edit_list_est_identite(self):
        """Le clip garde sa forme d'origine (aucun silence/hesitation retire)
        -- c'est le sens de « module desactive »."""
        candidat = _candidat()
        settings = _settings({"montage": {"enabled": False}})
        montage_plan, _, _ = _build_montage(
            candidat, settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert montage_plan.edit_list.is_identity
        assert montage_plan.applied is False

    def test_module_montage_absent_de_la_configuration(self):
        """Un bloc absent est traite comme desactive (editing_module() renvoie
        {} et cfg.get("enabled", False) vaut False) -- meme chemin, meme bug
        potentiel s'il devait reapparaitre."""
        montage_plan, zoom_track, delire_plan = _build_montage(
            _candidat(), _settings({}), AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert isinstance(delire_plan, DelirePlan)

    def test_module_montage_actif_reste_un_triplet(self):
        """REGRESSION A EVITER DANS L'AUTRE SENS : le chemin normal (module
        actif, aucun sous-module) doit continuer a renvoyer trois elements --
        ce test aurait deja echoue avant le bug si le chemin normal avait
        jamais ete casse a son tour."""
        settings = _settings({"montage": {"enabled": True}})
        resultat = _build_montage(
            _candidat(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert len(resultat) == 3
