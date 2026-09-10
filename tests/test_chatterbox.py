"""Chatterbox : le troisieme moteur de synthese.

NOTE SUR LES TESTS D'INTERFACE : ils lisent `isHidden()` et non `isVisible()`.
Qt considere invisible tout widget dont un ancetre n'est pas affiche ; sans
ouvrir de fenetre, `isVisible()` serait faux partout et ne dirait rien de ce
que le code a demande.

CE QUE CES TESTS COUVRENT : le catalogue, le decoupage des longs textes, les
bornes des reglages, la cle du cache, l'etat du modele et de l'environnement,
le contrat entre l'application et le processus de generation -- eprouve avec un
faux modele (tests/helpers/fake_chatterbox) qui rend un signal previsible.

CE QU'ILS NE COUVRENT PAS, ET QUI NE PEUT PAS L'ETRE ICI : la qualite de la
voix, la vitesse de generation, et le comportement reel du modele. Cela demande
le vrai modele et le vrai PyTorch, que cet environnement ne peut ni telecharger
ni executer. Voir le rapport de la fonctionnalite : TESTE / NON TESTE.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voice_studio import chatterbox_catalogue as catalogue
from voice_studio import chatterbox_models as models
from voice_studio import chatterbox_runtime as runtime
from voice_studio.chatterbox_engine import ChatterboxEngine

FAKE_PACKAGE = Path(__file__).parent / "helpers" / "fake_chatterbox"


class TestCatalogue:
    def test_le_modele_demande_est_la_v3(self):
        """Le paquet publie sur PyPI retomberait sur la V2 sans le dire : le
        catalogue doit donc nommer explicitement la V3."""
        spec = catalogue.model_spec()
        assert spec.get("t3_model") == "v3"
        assert any("v3" in name for name in spec.get("files", []))

    def test_le_runtime_est_epingle_sur_un_commit(self):
        """Une branche bouge ; un commit non. Sans epinglage, deux
        installations le meme mois ne donneraient pas le meme moteur."""
        spec = catalogue.runtime_spec()
        assert len(str(spec.get("commit", ""))) == 40
        assert spec["commit"] in spec.get("source", "")

    def test_le_francais_est_supporte(self):
        assert catalogue.supports_language("fr")
        assert catalogue.supports_language("FR-fr")
        assert not catalogue.supports_language("xx")

    def test_les_adresses_sont_construites_depuis_le_depot(self):
        urls = dict(catalogue.file_urls())
        assert urls, "le catalogue doit donner des adresses"
        for name, url in urls.items():
            assert url.endswith(name)
            assert url.startswith("https://")

    def test_aucune_taille_inventee(self):
        """La taille du modele n'a pas pu etre verifiee a la source : elle doit
        rester nulle plutot que porter un chiffre plausible."""
        assert catalogue.model_spec().get("size_bytes") is None

    def test_le_catalogue_absent_ne_casse_rien(self, monkeypatch, tmp_path):
        from core import config_loader

        monkeypatch.setattr(config_loader, "CONFIG_DIR", tmp_path)
        assert catalogue.presets(), "un repli minimal doit exister"
        assert catalogue.file_urls() == [], "sans adresse, on ne propose rien"


class TestPrereglages:
    def test_les_cinq_prereglages_existent(self):
        keys = {p.key for p in catalogue.presets()}
        assert {"naturel", "storytelling", "dynamique", "calme", "shorts"} <= keys

    def test_le_prereglage_naturel_reprend_les_valeurs_du_modele(self):
        """0.5 / 0.5 sont les valeurs par defaut du code officiel."""
        naturel = catalogue.preset("naturel")
        assert naturel.exaggeration == 0.5
        assert naturel.cfg_weight == 0.5

    def test_dynamique_est_plus_expressif_que_calme(self):
        assert catalogue.preset("dynamique").exaggeration > catalogue.preset("calme").exaggeration

    def test_les_valeurs_sont_modifiables_a_la_main(self):
        params = catalogue.params_for("naturel", exaggeration=0.9)
        assert params.exaggeration == 0.9
        assert params.preset == "naturel"

    def test_une_valeur_hors_bornes_est_ramenee_et_non_refusee(self):
        """Un curseur ne doit jamais faire echouer une generation."""
        bounds = catalogue.limits()
        params = catalogue.params_for("naturel", exaggeration=99, temperature=-5)
        assert params.exaggeration == bounds["exaggeration"][1]
        assert params.temperature == bounds["temperature"][0]

    def test_une_langue_non_supportee_retombe_sur_le_francais(self):
        assert catalogue.params_for("naturel", language="xx").language == "fr"

    def test_un_prereglage_inconnu_ne_fait_pas_echouer(self):
        assert catalogue.params_for("nexistepas").exaggeration > 0


class TestDecoupage:
    def test_un_texte_court_reste_entier(self):
        assert catalogue.chunks("Bonjour à tous.", 320) == ["Bonjour à tous."]

    def test_un_texte_long_est_decoupe(self):
        texte = "Une phrase de test. " * 40
        morceaux = catalogue.chunks(texte, 200)
        assert len(morceaux) > 1
        assert all(len(m) <= 200 for m in morceaux)

    def test_aucun_mot_n_est_coupe_en_deux(self):
        texte = " ".join(["anticonstitutionnellement"] * 30)
        for morceau in catalogue.chunks(texte, 100):
            for mot in morceau.split():
                assert mot == "anticonstitutionnellement"

    def test_le_texte_est_conserve(self):
        texte = ("Et là, tout change. Mais attendez… Parce que ce qui arrive ensuite "
                 "est complètement inattendu. Vous pensez que c'est terminé ? Pas du tout !")
        assert " ".join(catalogue.chunks(texte, 60)) == " ".join(texte.split())

    def test_la_ponctuation_est_preservee(self):
        """Elle porte le rythme et l'intonation a la lecture."""
        rendu = " ".join(catalogue.chunks("Vraiment ? Oui ! Enfin… peut-être.", 40))
        for signe in "?!…":
            assert signe in rendu

    def test_on_coupe_de_preference_entre_deux_phrases(self):
        morceaux = catalogue.chunks("Première phrase courte. Deuxième phrase courte.", 30)
        assert morceaux[0].endswith(".")

    def test_un_texte_vide_ne_donne_aucun_morceau(self):
        assert catalogue.chunks("   ", 320) == []


class TestCleDeCache:
    def _key(self, **overrides):
        from voice_studio import tts

        params = catalogue.params_for("naturel", **overrides)
        return tts.cache_key("Bonjour", None, 1.0, 1.0, 0.0, params)

    def test_l_expressivite_change_la_cle(self):
        assert self._key(exaggeration=0.5) != self._key(exaggeration=0.8)

    def test_le_rythme_change_la_cle(self):
        assert self._key(cfg_weight=0.5) != self._key(cfg_weight=0.3)

    def test_la_temperature_change_la_cle(self):
        assert self._key(temperature=0.8) != self._key(temperature=1.2)

    def test_la_graine_change_la_cle(self):
        assert self._key(seed=0) != self._key(seed=42)

    def test_la_voix_de_reference_change_la_cle(self):
        assert self._key(reference="") != self._key(reference="/tmp/voix.wav")

    def test_les_memes_reglages_donnent_la_meme_cle(self):
        assert self._key(exaggeration=0.6) == self._key(exaggeration=0.6)

    def test_les_autres_moteurs_ne_sont_pas_affectes(self):
        """Sans reglages, la cle doit rester celle d'avant Chatterbox."""
        from voice_studio import tts

        assert tts.cache_key("Bonjour", None, 1.0, 1.0, 0.0) == tts.cache_key(
            "Bonjour", None, 1.0, 1.0, 0.0, None)


class TestEtatDuModele:
    @pytest.fixture
    def elsewhere(self, monkeypatch, tmp_path):
        monkeypatch.setattr(models, "models_dir", lambda: tmp_path / "chatterbox")
        return tmp_path / "chatterbox"

    def test_rien_n_est_installe_au_depart(self, elsewhere):
        assert not models.is_installed()
        assert len(models.missing_files()) == len(models.expected_files())

    def test_un_fichier_vide_compte_comme_manquant(self, elsewhere):
        """Une page d'erreur enregistree a la place d'un poids ne doit pas
        passer pour un modele installe."""
        elsewhere.mkdir(parents=True)
        for name in models.expected_files():
            (elsewhere / name).write_bytes(b"erreur")
        assert not models.is_installed()

    def test_tous_les_fichiers_presents_valent_installe(self, elsewhere):
        elsewhere.mkdir(parents=True)
        for name in models.expected_files():
            (elsewhere / name).write_bytes(b"x" * 2048)
        assert models.is_installed()
        assert models.installed_size_bytes() == 2048 * len(models.expected_files())

    def test_la_suppression_efface_aussi_les_fichiers_partiels(self, elsewhere):
        elsewhere.mkdir(parents=True)
        name = models.expected_files()[0]
        (elsewhere / name).write_bytes(b"x" * 2048)
        (elsewhere / (name + ".part")).write_bytes(b"x")
        assert models.remove() == 1
        assert not (elsewhere / (name + ".part")).exists()

    def test_le_telechargement_passe_par_le_telechargeur_commun(self, elsewhere, monkeypatch):
        """Reprise, annulation et verification d'espace disque viennent de la,
        et il n'y a pas de second telechargeur dans le projet."""
        appels = []

        def _fake(url, target, on_progress=None, cancel_token=None, opener=None):
            appels.append((url, target.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 4096)

        from voice_studio import downloads

        monkeypatch.setattr(downloads, "download_file", _fake)
        models.install()
        assert len(appels) == len(models.expected_files())
        assert models.is_installed()

    def test_rien_n_est_retelecharge_si_tout_est_la(self, elsewhere, monkeypatch):
        elsewhere.mkdir(parents=True)
        for name in models.expected_files():
            (elsewhere / name).write_bytes(b"x" * 2048)
        from voice_studio import downloads

        monkeypatch.setattr(downloads, "download_file",
                            lambda *a, **k: pytest.fail("aucun téléchargement attendu"))
        models.install()

    def test_sans_adresse_le_telechargement_est_refuse_clairement(self, elsewhere, monkeypatch):
        monkeypatch.setattr(catalogue, "file_urls", lambda: [])
        with pytest.raises(models.ChatterboxModelError):
            models.install()


class TestEtatDuRuntime:
    @pytest.fixture
    def elsewhere(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runtime, "runtime_dir", lambda: tmp_path / "runtime")
        return tmp_path / "runtime"

    def test_rien_n_est_pret_au_depart(self, elsewhere):
        assert not runtime.is_ready()
        assert not runtime.is_outdated()

    def test_un_environnement_sans_marqueur_est_signale_comme_perime(self, elsewhere):
        executable = runtime.python_executable()
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n")
        assert runtime.is_outdated()
        assert not runtime.is_ready()

    def test_le_marqueur_du_bon_commit_rend_l_environnement_pret(self, elsewhere):
        executable = runtime.python_executable()
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n")
        runtime.marker_path().write_text(json.dumps(
            {"commit": catalogue.runtime_spec()["commit"]}), encoding="utf-8")
        assert runtime.is_ready()

    def test_le_selftest_sans_environnement_le_dit_sans_planter(self, elsewhere):
        state = runtime.selftest()
        assert state["ok"] is False
        assert "install" in state["error"].lower()

    def test_une_version_de_python_hors_bornes_est_refusee(self):
        assert runtime._acceptable((3, 11, 5))
        assert not runtime._acceptable((3, 8, 10))
        assert not runtime._acceptable((3, 14, 0))

    def test_le_worker_est_livre_avec_l_application(self):
        assert runtime.worker_script().is_file()


class TestMoteur:
    def test_aucune_voix_tant_que_rien_n_est_installe(self, monkeypatch):
        """Ne jamais afficher une voix qui echouerait a la generation."""
        monkeypatch.setattr(runtime, "is_ready", lambda: False)
        engine = ChatterboxEngine()
        assert engine.available() is False
        assert engine.voices() == []

    def test_le_moteur_dit_pourquoi_il_est_indisponible(self, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: False)
        monkeypatch.setattr(runtime, "is_outdated", lambda: False)
        assert "installé" in ChatterboxEngine().unavailable_reason()

    def test_un_modele_incomplet_est_distingue_d_un_moteur_absent(self, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: False)
        monkeypatch.setattr(models, "missing_files", lambda: ["s3gen.pt"])
        assert "modèle" in ChatterboxEngine().unavailable_reason().lower()

    def test_une_voix_apparait_quand_tout_est_installe(self, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: True)
        voices = ChatterboxEngine().voices()
        assert len(voices) == 1
        assert voices[0].engine == "chatterbox"
        assert voices[0].is_french

    def test_le_moteur_annonce_qu_il_ne_gere_pas_la_vitesse(self):
        """Le modele n'expose aucun reglage de debit : l'interface doit pouvoir
        griser le curseur au lieu de laisser croire qu'il agit."""
        assert ChatterboxEngine().supports_rate is False

    def test_il_est_dans_la_liste_des_moteurs_connus(self):
        from voice_studio import tts

        assert "chatterbox" in [engine.name for engine in tts.all_engines()]
        assert "chatterbox" in tts.ENGINE_LABELS

    def test_les_deux_autres_moteurs_gardent_leur_comportement(self):
        from voice_studio import tts

        names = [engine.name for engine in tts.all_engines()]
        assert names[:2] == ["system", "piper"]


class TestGenerationAvecUnFauxModele:
    """Le contrat entre l'application et le processus de generation.

    Le faux modele rend un signal previsible : on peut donc verifier ce qui lui
    a ete demande, et ce qui a ete ecrit. La qualite de la voix, elle, ne se
    teste qu'avec le vrai modele.
    """

    @pytest.fixture
    def engine(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: True)
        monkeypatch.setattr(models, "models_dir", lambda: tmp_path / "poids")
        monkeypatch.setattr(runtime, "python_executable", lambda: Path(sys.executable))
        log = tmp_path / "appels.jsonl"
        monkeypatch.setenv("CHATTERBOX_FAKE_LOG", str(log))
        monkeypatch.setenv("PYTHONPATH", str(FAKE_PACKAGE))
        engine = ChatterboxEngine()
        engine._log_path = log
        return engine

    def _calls(self, engine) -> list[dict]:
        if not engine._log_path.is_file():
            return []
        return [json.loads(line) for line in
                engine._log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_une_narration_est_produite(self, engine, tmp_path):
        out = tmp_path / "voix.wav"
        engine.synthesize("Bonjour à tous, voici un essai.", str(out),
                          params=catalogue.params_for("naturel"))
        assert out.is_file() and out.stat().st_size > 1000

    def test_le_modele_v3_est_demande(self, engine, tmp_path):
        engine.synthesize("Bonjour.", str(tmp_path / "a.wav"))
        load = next(c for c in self._calls(engine) if c["call"] == "from_local")
        assert load["t3_model"] == "v3"

    def test_le_texte_part_tel_quel_avec_sa_ponctuation(self, engine, tmp_path):
        engine.synthesize("Vraiment ? Oui !", str(tmp_path / "a.wav"))
        dit = " ".join(c["text"] for c in self._calls(engine) if c["call"] == "generate")
        assert "?" in dit and "!" in dit

    def test_le_francais_est_annonce_au_modele(self, engine, tmp_path):
        engine.synthesize("Bonjour.", str(tmp_path / "a.wav"),
                          params=catalogue.params_for("naturel", language="fr"))
        assert all(c["language_id"] == "fr"
                   for c in self._calls(engine) if c["call"] == "generate")

    def test_les_reglages_arrivent_jusqu_au_modele(self, engine, tmp_path):
        engine.synthesize("Bonjour.", str(tmp_path / "a.wav"),
                          params=catalogue.params_for("naturel", exaggeration=0.72,
                                                      cfg_weight=0.31, temperature=0.66))
        call = next(c for c in self._calls(engine) if c["call"] == "generate")
        assert call["exaggeration"] == pytest.approx(0.72)
        assert call["cfg_weight"] == pytest.approx(0.31)
        assert call["temperature"] == pytest.approx(0.66)

    def test_un_long_texte_est_genere_en_plusieurs_appels(self, engine, tmp_path):
        long_text = "Une phrase de narration bien remplie. " * 30
        engine.synthesize(long_text, str(tmp_path / "a.wav"))
        generations = [c for c in self._calls(engine) if c["call"] == "generate"]
        assert len(generations) > 1
        assert all(len(c["text"]) <= catalogue.limits()["max_chars_per_chunk"]
                   for c in generations)

    def test_le_resultat_est_un_seul_fichier_continu(self, engine, tmp_path):
        import wave

        out = tmp_path / "a.wav"
        engine.synthesize("Première partie. " * 20, str(out))
        with wave.open(str(out)) as handle:
            assert handle.getnchannels() == 1
            assert handle.getframerate() == catalogue.sample_rate()
            assert handle.getnframes() > 0

    def test_la_voix_de_reference_est_transmise(self, engine, tmp_path):
        reference = tmp_path / "reference.wav"
        reference.write_bytes(b"RIFF----WAVEfmt ")
        engine.synthesize("Bonjour.", str(tmp_path / "a.wav"),
                          params=catalogue.params_for("naturel", reference=str(reference)))
        call = next(c for c in self._calls(engine) if c["call"] == "generate")
        assert call["audio_prompt_path"] == str(reference)

    def test_une_reference_introuvable_est_refusee_avant_de_lancer(self, engine, tmp_path):
        from voice_studio.tts import TtsError

        with pytest.raises(TtsError) as error:
            engine.synthesize("Bonjour.", str(tmp_path / "a.wav"),
                              params=catalogue.params_for("naturel",
                                                          reference="/absent/voix.wav"))
        assert "introuvable" in str(error.value)

    def test_un_echec_du_modele_devient_un_message_lisible(self, engine, tmp_path,
                                                           monkeypatch):
        from voice_studio.tts import TtsError

        monkeypatch.setenv("CHATTERBOX_FAKE_FAIL", "generate")
        with pytest.raises(TtsError) as error:
            engine.synthesize("Bonjour.", str(tmp_path / "a.wav"))
        message = str(error.value)
        assert "Traceback" not in message
        assert "refusée" in message or "échoué" in message

    def test_la_progression_est_rapportee_morceau_par_morceau(self, engine, tmp_path):
        vus = []
        engine.synthesize("Une phrase. " * 40, str(tmp_path / "a.wav"),
                          on_progress=lambda event: vus.append(event.get("event")))
        assert "loading" in vus
        assert vus.count("chunk") > 1
        assert "done" in vus

    def test_l_annulation_arrete_la_generation(self, engine, tmp_path):
        from core.cancellation import CancelToken
        from utils.errors import CancelledError

        token = CancelToken()
        token.cancel()
        out = tmp_path / "a.wav"
        with pytest.raises(CancelledError):
            engine.synthesize("Une phrase. " * 40, str(out), cancel_token=token)
        assert not out.exists(), "aucun fichier incomplet ne doit rester"

    def test_le_cache_evite_une_seconde_generation(self, engine, tmp_path, monkeypatch):
        from voice_studio import tts

        monkeypatch.setattr(tts, "cache_dir", lambda: tmp_path / "cache")
        (tmp_path / "cache").mkdir()
        voice = engine.voices()[0] if engine.voices() else None
        params = catalogue.params_for("naturel")
        first = tmp_path / "1.wav"
        tts.synthesize("Bonjour à tous.", str(first), voice=voice, params=params)
        avant = len(self._calls(engine))
        second = tmp_path / "2.wav"
        tts.synthesize("Bonjour à tous.", str(second), voice=voice, params=params)
        assert len(self._calls(engine)) == avant, "le cache doit servir le second appel"
        assert second.is_file()

    def test_un_reglage_different_regenere(self, engine, tmp_path, monkeypatch):
        from voice_studio import tts

        monkeypatch.setattr(tts, "cache_dir", lambda: tmp_path / "cache")
        (tmp_path / "cache").mkdir()
        voice = engine.voices()[0] if engine.voices() else None
        tts.synthesize("Bonjour à tous.", str(tmp_path / "1.wav"), voice=voice,
                       params=catalogue.params_for("naturel", exaggeration=0.5))
        avant = len(self._calls(engine))
        tts.synthesize("Bonjour à tous.", str(tmp_path / "2.wav"), voice=voice,
                       params=catalogue.params_for("naturel", exaggeration=0.9))
        assert len(self._calls(engine)) > avant, "l'expressivité a changé : il faut regénérer"


class TestSelftestDuWorker:
    def test_le_worker_repond_sans_le_vrai_modele(self, tmp_path):
        """Le selftest doit dire ce qui est installe. Ici, torch et chatterbox
        ne le sont pas : il doit le DIRE, pas planter."""
        env = dict(os.environ, PYTHONPATH=str(FAKE_PACKAGE))
        result = subprocess.run(
            [sys.executable, str(runtime.worker_script()), "--selftest"],
            capture_output=True, text=True, env=env, timeout=120)
        events = [json.loads(line) for line in result.stdout.splitlines()
                  if line.strip().startswith("{")]
        assert events, "le worker doit toujours rendre un évènement JSON"
        assert events[-1]["event"] in ("selftest", "error")


class TestInterface:
    """Ce que l'ecran montre, et ce qu'il ne montre pas."""

    @pytest.fixture
    def page(self, monkeypatch):
        pytest.importorskip("PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.controller import AppController
        from gui.voice_studio.page import VoiceStudioPage

        app = QApplication.instance() or QApplication([])
        assert app is not None
        page = VoiceStudioPage(AppController())
        page.on_shown()
        return page

    def _select_chatterbox(self, page) -> bool:
        index = page.engine_combo.findData("chatterbox")
        if index < 0:
            return False
        page.engine_combo.setCurrentIndex(index)
        return True

    def test_le_moteur_est_propose_meme_non_installe(self, page, monkeypatch):
        """Le cacher tant qu'il n'est pas la reviendrait à ne jamais le
        proposer -- or il se télécharge depuis l'application."""
        monkeypatch.setattr(runtime, "is_ready", lambda: False)
        page._load_voices()
        assert self._select_chatterbox(page)
        libelle = page.engine_combo.currentText()
        assert "non installé" in libelle

    def test_l_ecran_dit_ce_qui_manque(self, page, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: False)
        page._load_voices()
        self._select_chatterbox(page)
        assert not page.chatterbox_notice.isHidden()
        assert not page.chatterbox_btn.isHidden()

    def test_aucun_reglage_affiche_tant_que_rien_n_est_installe(self, page, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: False)
        page._load_voices()
        self._select_chatterbox(page)
        assert page.chatterbox_panel.isHidden()

    def test_les_reglages_apparaissent_une_fois_installe(self, page, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: True)
        page._load_voices()
        self._select_chatterbox(page)
        assert not page.chatterbox_panel.isHidden()
        assert page.chatterbox_notice.isHidden()

    def test_la_vitesse_est_grisee_pour_chatterbox(self, page, monkeypatch):
        """Le modèle n'expose aucun réglage de débit : un curseur sans effet
        serait un mensonge."""
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: True)
        page._load_voices()
        self._select_chatterbox(page)
        assert not page.rate_combo.isEnabled()

    def test_la_vitesse_revient_pour_les_autres_moteurs(self, page, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: True)
        page._load_voices()
        self._select_chatterbox(page)
        index = page.engine_combo.findData("system")
        if index < 0:
            pytest.skip("aucune voix système sur cette machine")
        page.engine_combo.setCurrentIndex(index)
        assert page.rate_combo.isEnabled()

    def test_les_reglages_partent_vers_le_moteur(self, page, monkeypatch):
        monkeypatch.setattr(runtime, "is_ready", lambda: True)
        monkeypatch.setattr(models, "is_installed", lambda: True)
        page._load_voices()
        self._select_chatterbox(page)
        params = page._engine_params()
        assert params is not None
        assert params.language == "fr"

    def test_les_autres_moteurs_n_ont_pas_de_reglages(self, page):
        index = page.engine_combo.findData("system")
        if index < 0:
            pytest.skip("aucune voix système sur cette machine")
        page.engine_combo.setCurrentIndex(index)
        assert page._engine_params() is None


class TestPanneauDeReglages:
    @pytest.fixture
    def panel(self):
        pytest.importorskip("PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.voice_studio.chatterbox_panel import ChatterboxPanel

        app = QApplication.instance() or QApplication([])
        assert app is not None
        return ChatterboxPanel()

    def test_les_prereglages_sont_proposes(self, panel):
        assert panel.preset_combo.count() == len(catalogue.presets())

    def test_choisir_un_style_change_les_curseurs(self, panel):
        panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("calme"))
        calme = panel.params()
        panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("dynamique"))
        assert panel.params().exaggeration > calme.exaggeration

    def test_les_curseurs_restent_modifiables_a_la_main(self, panel):
        panel.exaggeration.setValue(90)
        assert panel.params().exaggeration == pytest.approx(0.9)

    def test_l_avertissement_apparait_sur_les_valeurs_extremes(self, panel):
        panel.exaggeration.setValue(160)
        assert not panel.warning.isHidden()
        panel.exaggeration.setValue(50)
        assert panel.warning.isHidden()

    def test_les_parametres_avances_sont_replies_par_defaut(self, panel):
        assert panel.advanced.isHidden()
        panel.advanced_check.setChecked(True)
        assert not panel.advanced.isHidden()

    def test_la_graine_zero_veut_dire_aleatoire(self, panel):
        assert panel.seed_spin.value() == 0
        assert panel.params().seed == 0

    def test_la_voix_integree_est_le_mode_par_defaut(self, panel):
        assert panel.params().reference == ""
        assert not panel.pick_btn.isEnabled()

    def test_le_mode_reference_active_les_boutons_et_le_rappel_de_droits(self, panel):
        panel.voice_mode.setCurrentIndex(panel.voice_mode.findData("reference"))
        assert panel.pick_btn.isEnabled()
        assert not panel.rights.isHidden()

    def test_aucun_clonage_automatique_n_est_propose(self, panel):
        """La reference vient d'un fichier choisi a la main, jamais d'une
        recuperation automatique depuis une video ou depuis internet."""
        libelles = " ".join(panel.voice_mode.itemText(i)
                            for i in range(panel.voice_mode.count())).lower()
        assert "cloner" not in libelles and "internet" not in libelles


class TestDetectionDePython:
    """Trouver l'interpreteur, y compris quand le PATH ment.

    DEFAUT REEL SIGNALE EN USAGE : sur un poste ou `python --version`
    repondait « Python 3.12.7 » dans l'invite de commandes, l'application
    compilee affichait « Python introuvable ».
    """

    def test_un_alias_vide_du_microsoft_store_est_ignore(self, tmp_path):
        """Windows pose dans WindowsApps des fichiers de ZERO octet qui ouvrent
        le Store au lieu de lancer Python -- et `shutil.which` les trouve en
        premier."""
        alias = tmp_path / "python.exe"
        alias.write_bytes(b"")
        assert runtime._probe(str(alias)) is None

    def test_un_interpreteur_reel_est_reconnu(self):
        candidate = runtime._probe(sys.executable)
        assert candidate is not None
        assert candidate.version[:2] >= (3, 10)

    def test_le_lanceur_windows_est_essaye_avec_une_version(self, monkeypatch):
        """`py` seul lance la version « par defaut », qui peut sortir de la
        plage acceptee : les versions explicites passent avant."""
        monkeypatch.setattr(runtime, "_is_windows", lambda: True)
        monkeypatch.setattr(runtime, "_windows_install_paths", lambda: [])
        plan = [" ".join(c) for c in runtime.search_plan()]
        assert any(entry.endswith("-3.12") for entry in plan)
        assert any(entry.endswith("-3.11") for entry in plan)

    def test_un_chemin_designe_a_la_main_passe_en_premier(self):
        plan = runtime.search_plan("/mon/python")
        assert plan[0] == ["/mon/python"]

    def test_le_chemin_enregistre_dans_les_reglages_est_essaye(self, monkeypatch):
        from gui import settings_store

        monkeypatch.setattr(settings_store, "get",
                            lambda key: "/choisi/python.exe" if key == "chatterbox_python" else None)
        assert ["/choisi/python.exe"] in runtime.search_plan()

    def test_les_emplacements_d_installation_windows_sont_regardes(self, monkeypatch, tmp_path):
        """Le PATH d'un programme deja lance ne voit pas une installation
        faite apres son demarrage : on regarde donc aussi les dossiers
        habituels, directement."""
        monkeypatch.setattr(runtime, "_is_windows", lambda: True)
        local = tmp_path / "Local"
        (local / "Programs" / "Python" / "Python312").mkdir(parents=True)
        (local / "Programs" / "Python" / "Python312" / "python.exe").write_bytes(b"MZ")
        monkeypatch.setenv("LOCALAPPDATA", str(local))
        trouve = [c[0] for c in runtime._windows_install_paths()]
        assert any(entry.endswith("python.exe") for entry in trouve)

    def test_le_plan_de_recherche_est_sans_doublon(self):
        plan = [" ".join(c) for c in runtime.search_plan("/mon/python")]
        assert len(plan) == len(set(plan))

    def test_le_diagnostic_dit_ou_l_on_a_cherche(self):
        """Un « introuvable » sans dire ou l'on a cherche ne laisse aucune
        prise a l'utilisateur."""
        texte = runtime.describe_search()
        assert texte.startswith("•")
        assert len(texte.splitlines()) >= 1

    def test_le_lanceur_reste_une_commande_complete(self, monkeypatch):
        """« py -3.12 » doit rester en deux morceaux jusqu'a l'appel, sinon
        l'environnement serait cree par le mauvais Python."""
        vu = {}

        def _fake_run(command, **kwargs):
            vu["command"] = command

            class _Result:
                returncode = 0
                stdout = "3.12.7\n"
            return _Result()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        candidate = runtime._probe(["py", "-3.12"])
        assert candidate.executable == "py -3.12"
        assert vu["command"][:2] == ["py", "-3.12"]

    def test_l_entree_standard_est_neutralisee(self, monkeypatch):
        """Une application fenetree n'a pas d'entree standard valable, et le
        processus fils heritait d'un descripteur invalide."""
        vu = {}

        def _fake_run(command, **kwargs):
            vu.update(kwargs)

            class _Result:
                returncode = 0
                stdout = "3.12.7\n"
            return _Result()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        runtime._probe(sys.executable)
        assert vu.get("stdin") == subprocess.DEVNULL
