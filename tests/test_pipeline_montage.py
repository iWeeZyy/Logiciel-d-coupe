"""`_build_montage` : le triplet qu'elle promet, et l'independance du delire.

DEUX BUGS DANS LA MEME FONCTION, SIGNALES EN USAGE REEL COUP SUR COUP.

BUG 1 (corrige dans le build precedent) : « Erreur inattendue : not enough
values to unpack (expected 3, got 2) » a l'etape « Generation des clips »,
avec le module Montage auto desactive. `_build_montage` promet un triplet
(MontagePlan, ZoomTrack, DelirePlan) -- c'est sa signature, et c'est ce que
`run_pipeline` deballe. Le chemin de sortie anticipee (module montage
desactive) ne renvoyait qu'un COUPLE, oubli du troisieme element quand le
module delire a ete ajoute apres coup.

BUG 2 (corrige ici) : « Le mode Delire n'a jamais fonctionne », signale
ensuite. Consequence directe du correctif du bug 1 pris au pied de la lettre :
le patch minimal renvoyait bien un DelirePlan() vide sur ce chemin -- mais
VIDE, jamais celui que le module delire aurait construit. Le calcul du delire
vivait plus bas dans la fonction, a l'interieur du bloc qui ne s'execute que
si `cfg.get("enabled")` (le bloc MONTAGE) est vrai. Decocher seulement
« Montage auto » (en gardant « Delire » coche) desactivait donc le delire
SANS LE DIRE : aucune erreur, juste aucun effet sur le clip produit -- ce qui
explique parfaitement « jamais fonctionne » si l'utilisateur, pour eviter le
crash du bug 1 ou simplement parce qu'il ne voulait que les effets delire
sans le decoupage de silences, avait pris l'habitude de decocher « Montage
auto ». Le delire est pourtant un module A PART, pas un sous-reglage du
montage : sa propre case, cote a cote de « Montage auto » a l'accueil
(gui/widgets/production_options.py, QUICK_MODULES), son propre bloc de
premier niveau dans config/editing.json (a la difference du zoom dynamique et
de la suppression des silences/hesitations, QUI SONT documentes comme des
sous-reglages de "montage" dans settings_page.py).

Ces tests appellent `_build_montage` directement plutot que `run_pipeline` en
entier : la fonction est pure une fois l'AudioAnalyzer fourni, et aucun des
deux chemins couverts ici ne le consulte (le calcul du delire lit
`word_loudness`/`loud_threshold` deja mesures, jamais l'audio directement) --
un stub qui leve sur tout appel suffit donc a le prouver, sans fichier .wav.
"""
from __future__ import annotations

from core.config_loader import Settings
from core.models import Candidate, Word
from editing.delire import Plan as DelirePlan
from editing.silence_cut import MontagePlan
from editing.zoom import ZoomTrack
from pipeline import _build_montage


class AudioAnalyzerJamaisAppele:
    """N'importe quel appel signale un changement de comportement, pas
    seulement un defaut de test : ni le chemin « montage desactive » ni le
    calcul du delire ne doivent consulter l'audio directement."""

    def mean_db(self, start, end):
        raise AssertionError(
            "ce chemin ne doit consulter aucun signal audio directement")


def _mot(text, start, end):
    return Word(text=text, start=start, end=end, probability=0.99)


def _candidat_court():
    """Un clip d'une seconde : suffit a verifier qu'aucune erreur n'est
    levee et que le triplet a la bonne forme."""
    mots = [_mot("Bonjour", 0.0, 0.4), _mot("a", 0.4, 0.5), _mot("tous", 0.5, 0.9)]
    return Candidate(start=0.0, end=1.0, text="Bonjour a tous", words=mots,
                     audio={}, text_features={})


def _candidat_realiste():
    """Un clip de 20 s avec un mot marquant loin des deux bords : la duree
    minimale pour que build_plan() puisse reellement poser un effet (voir
    editing/delire.py -- une marge de 0,25 s est retiree de chaque bord, et le
    budget d'un clip d'une seconde ne laisse pas la place a un seul effet)."""
    mots = [_mot("Bonjour", 0.0, 0.4), _mot("attends", 9.8, 10.2), _mot("quoi", 19.5, 19.9)]
    return Candidate(start=0.0, end=20.0, text="Bonjour attends quoi", words=mots,
                     audio={}, text_features={})


def _settings(editing: dict) -> Settings:
    return Settings(editing=editing)


class TestLeTripletEstToujoursRendu:
    """Bug 1 : le crash. Corrige, garde comme regression."""

    def test_module_montage_desactive_seul(self):
        settings = _settings({"montage": {"enabled": False}})
        resultat = _build_montage(
            _candidat_court(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert len(resultat) == 3
        montage_plan, zoom_track, delire_plan = resultat  # ne doit pas lever
        assert isinstance(montage_plan, MontagePlan)
        assert isinstance(zoom_track, ZoomTrack)
        assert isinstance(delire_plan, DelirePlan)

    def test_module_montage_desactive_l_edit_list_est_identite(self):
        """Le clip garde sa forme d'origine (aucun silence/hesitation retire)
        -- c'est le sens de « module desactive »."""
        candidat = _candidat_court()
        settings = _settings({"montage": {"enabled": False}})
        montage_plan, _, _ = _build_montage(
            candidat, settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert montage_plan.edit_list.is_identity
        assert montage_plan.applied is False

    def test_module_montage_desactive_le_zoom_reste_vide(self):
        """Le zoom dynamique, LUI, EST un sous-reglage du montage (chemin
        ("montage", "dynamic_zoom", "enabled") dans settings_page.py) : le
        couper avec « Montage auto » reste correct, a la difference du
        delire."""
        montage_plan, zoom_track, _ = _build_montage(
            _candidat_court(), _settings({"montage": {"enabled": False}}),
            AudioAnalyzerJamaisAppele(), sentences=[], emphasis_scores={},
        )
        assert zoom_track == ZoomTrack()

    def test_module_montage_absent_de_la_configuration(self):
        """Un bloc absent est traite comme desactive (editing_module() renvoie
        {} et cfg.get("enabled", False) vaut False) -- meme chemin."""
        montage_plan, zoom_track, delire_plan = _build_montage(
            _candidat_court(), _settings({}), AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert isinstance(delire_plan, DelirePlan)

    def test_module_montage_actif_reste_un_triplet(self):
        """REGRESSION A EVITER DANS L'AUTRE SENS : le chemin normal (module
        actif, aucun sous-module) doit continuer a renvoyer trois elements."""
        settings = _settings({"montage": {"enabled": True}})
        resultat = _build_montage(
            _candidat_court(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert len(resultat) == 3


class TestLeDelireEstIndependantDuMontage:
    """Bug 2 : « Le mode Delire n'a jamais fonctionne ». Le delire doit
    s'appliquer quel que soit l'etat du module montage -- c'est ce que sa
    propre case, separee de « Montage auto », promet a l'utilisateur."""

    def test_delire_actif_montage_desactive_produit_un_effet(self):
        """LE SCENARIO EXACT DU BUG : « Montage auto » decoche, « Delire »
        coche. Avant le correctif, delire_plan etait un DelirePlan() vide
        (aucune exception -- c'est pour ca que le bug 1 masquait celui-ci) ;
        desormais il contient l'effet que le module aurait du poser."""
        settings = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": True, "level": "moyen", "seed": 0},
        })
        candidat = _candidat_realiste()
        # index 1 = "attends", a 9.8 s : loin des deux bords d'un clip de 20 s.
        montage_plan, zoom_track, delire_plan = _build_montage(
            candidat, settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={1: 0.9}, word_loudness=[-20.0, -20.0, -20.0],
            loud_threshold=-15.0,
        )
        assert not delire_plan.is_empty, delire_plan.reasons
        assert len(delire_plan.events) >= 1
        # Le montage, lui, est bien reste desactive -- les deux etats
        # coexistent, ils ne se remplacent pas l'un l'autre.
        assert montage_plan.edit_list.is_identity

    def test_delire_actif_montage_actif_produit_le_meme_effet(self):
        """Meme resultat cote delire que montage soit actif ou non -- ce n'est
        pas « le montage active le delire », c'est « le delire ne depend pas
        du montage », dans les deux sens."""
        settings_communs = {"delire": {"enabled": True, "level": "moyen", "seed": 0}}
        candidat = _candidat_realiste()
        kwargs = dict(sentences=[], emphasis_scores={1: 0.9},
                     word_loudness=[-20.0, -20.0, -20.0], loud_threshold=-15.0)

        _, _, delire_desactive = _build_montage(
            candidat, _settings({**settings_communs, "montage": {"enabled": False}}),
            AudioAnalyzerJamaisAppele(), **kwargs)
        _, _, delire_actif = _build_montage(
            candidat, _settings({**settings_communs, "montage": {"enabled": True}}),
            AudioAnalyzerJamaisAppele(), **kwargs)

        assert delire_desactive.to_dict() == delire_actif.to_dict()

    def test_delire_desactive_reste_vide_montage_desactive_aussi(self):
        """Ni l'un ni l'autre coches : aucun effet, comme avant -- le
        correctif n'active pas le delire par defaut."""
        settings = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": False},
        })
        _, _, delire_plan = _build_montage(
            _candidat_realiste(), settings, AudioAnalyzerJamaisAppele(),
            sentences=[], emphasis_scores={1: 0.9},
            word_loudness=[-20.0, -20.0, -20.0], loud_threshold=-15.0,
        )
        assert delire_plan.is_empty

    def test_delire_desactive_absent_du_bloc_montage(self):
        """Le bloc "delire" n'est jamais lu a l'interieur du bloc "montage" --
        editing_module("delire") est appele SANS PASSER par `cfg` (le bloc
        montage). Une configuration ou "montage" ne contient meme pas de cle
        "delire" imbriquee doit donner le meme resultat qu'avec."""
        plat = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": True, "level": "moyen", "seed": 0},
        })
        imbrique_par_erreur = _settings({
            "montage": {"enabled": False, "delire": {"enabled": True, "level": "moyen", "seed": 0}},
        })
        candidat = _candidat_realiste()
        kwargs = dict(sentences=[], emphasis_scores={1: 0.9},
                     word_loudness=[-20.0, -20.0, -20.0], loud_threshold=-15.0)

        _, _, delire_plat = _build_montage(candidat, plat, AudioAnalyzerJamaisAppele(), **kwargs)
        _, _, delire_imbrique = _build_montage(
            candidat, imbrique_par_erreur, AudioAnalyzerJamaisAppele(), **kwargs)

        assert not delire_plat.is_empty
        assert delire_imbrique.is_empty, (
            "le bloc delire ne doit etre lu qu'au premier niveau, jamais sous montage")


class TestChoixDuThemeDansLePipeline:
    """`_build_montage` doit choisir entre rafales et theme selon
    `delire.theme` dans la configuration -- c'est le seul point de decision,
    voir editing/delire.py pour pourquoi les deux sont exclusifs."""

    def test_un_theme_renseigne_produit_un_plan_de_theme(self):
        from editing.delire import THEME_PLUIE

        settings = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": True, "theme": THEME_PLUIE},
        })
        _, _, delire_plan = _build_montage(
            _candidat_court(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert delire_plan.theme == THEME_PLUIE
        assert delire_plan.events == ()

    def test_theme_vide_garde_le_comportement_des_rafales(self):
        """"" (ou absent) : comportement historique, inchange par cette
        fonctionnalite."""
        settings = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": True, "theme": "", "level": "moyen"},
        })
        candidat = _candidat_realiste()
        _, _, delire_plan = _build_montage(
            candidat, settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={1: 0.9}, word_loudness=[-20.0, -20.0, -20.0],
            loud_threshold=-15.0,
        )
        assert delire_plan.theme == ""

    def test_un_theme_inconnu_retombe_sur_un_plan_vide(self):
        """Une valeur de configuration corrompue ne doit pas planter : elle
        est traitee comme "aucun theme", et comme aucun mot n'est marquant
        ici, aucune rafale ne se substitue non plus -- mieux vaut un clip
        sobre qu'un theme invente."""
        settings = _settings({
            "montage": {"enabled": False},
            "delire": {"enabled": True, "theme": "hiver"},
        })
        _, _, delire_plan = _build_montage(
            _candidat_court(), settings, AudioAnalyzerJamaisAppele(), sentences=[],
            emphasis_scores={},
        )
        assert delire_plan.theme == ""
        assert delire_plan.is_empty
