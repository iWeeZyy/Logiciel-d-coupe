"""Banc d'essai ZeroGPU : ce qui se verifie sans GPU ni reseau.

CE QUE CES TESTS NE PEUVENT PAS FAIRE, et qu'il faut dire : ils ne mesurent
rien. La vitesse reelle, la file d'attente, le quota et la qualite de la voix
ne s'obtiennent qu'en appelant vraiment Hugging Face, ce qu'aucun test
automatique ne fait ici.

CE QU'ILS DEFENDENT, en revanche :
  * l'ISOLATION -- aucun module du Voice Studio existant ne doit dependre de
    ZeroGPU, sinon la promesse « supprimable sans rien restaurer » est fausse ;
  * la limite de 300 caracteres, qui vient d'une troncature SILENCIEUSE du
    Space officiel ;
  * l'association des parametres, ou une erreur enverrait le texte dans le
    champ « temperature » ;
  * l'arithmetique des mesures, seule chose qui servira a decider.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_studio import zerogpu_bench as bench
from voice_studio import zerogpu_catalogue as catalogue
from voice_studio import zerogpu_client, zerogpu_token

REPO = Path(__file__).resolve().parents[1]


class TestIsolation:
    """La condition posee : « je veux pouvoir supprimer entierement ce nouvel
    onglet sans avoir a restaurer l'ancien Voice Studio »."""

    def _sources(self, *directories):
        for directory in directories:
            for path in (REPO / directory).rglob("*.py"):
                if "zerogpu" in path.name or "test_" in path.name:
                    continue
                yield path

    def test_aucun_module_du_voice_studio_n_importe_zerogpu(self):
        coupables = [path.name for path in self._sources("voice_studio")
                     if "zerogpu" in path.read_text(encoding="utf-8")]
        assert not coupables, (
            f"{coupables} dépend(ent) de ZeroGPU : le supprimer casserait "
            "le Voice Studio actuel")

    def test_aucun_moteur_tts_ne_connait_zerogpu(self):
        """ZeroGPU n'est PAS un quatrieme moteur : SAPI, Piper et Chatterbox
        local doivent continuer a fonctionner exactement comme avant."""
        tts = (REPO / "voice_studio" / "tts.py").read_text(encoding="utf-8")
        assert "zerogpu" not in tts.lower()

    def test_la_page_voice_studio_n_est_pas_touchee(self):
        page = (REPO / "gui" / "voice_studio" / "page.py").read_text(encoding="utf-8")
        assert "zerogpu" not in page.lower()

    def test_l_empreinte_dans_la_fenetre_principale_tient_en_trois_lignes(self):
        """Trois lignes a retirer, pas davantage."""
        window = (REPO / "gui" / "main_window.py").read_text(encoding="utf-8")
        lignes = [ligne for ligne in window.splitlines()
                  if ("zerogpu" in ligne.lower() or "ZeroGpuPage" in ligne)
                  and not ligne.strip().startswith("#")]
        assert len(lignes) == 3, lignes

    def test_le_service_ne_depend_pas_du_cache_de_voix(self):
        """Utiliser tts.py ferait entrer ZeroGPU dans le systeme existant."""
        service = (REPO / "voice_studio" / "zerogpu_service.py").read_text(encoding="utf-8")
        assert "import tts" not in service and "from voice_studio import tts" not in service


class TestLimiteDeTrois_Cents:
    """La limite ne vient pas d'une precaution : multilingual_app.py, dans le
    depot officiel de Chatterbox, fait `text_input[:300]` sans rien dire."""

    def test_la_limite_est_de_300_caracteres(self):
        assert catalogue.limits()["max_chars_per_chunk"] == 300

    def test_elle_est_distincte_de_celle_du_chatterbox_local(self):
        """320 en local, 300 ici : partager la valeur serait faux d'un cote."""
        from voice_studio import chatterbox_catalogue

        assert chatterbox_catalogue.limits()["max_chars_per_chunk"] != \
            catalogue.limits()["max_chars_per_chunk"]

    def test_aucun_morceau_ne_depasse_la_limite(self):
        texte = "Une phrase de test assez longue pour forcer le découpage. " * 40
        for piece in catalogue.chunks(texte):
            assert len(piece) <= 300

    def test_aucun_mot_n_est_coupe_en_deux(self):
        texte = "anticonstitutionnellement " * 60
        for piece in catalogue.chunks(texte):
            for mot in piece.split():
                assert mot == "anticonstitutionnellement"

    def test_le_texte_est_conserve(self):
        texte = "Première phrase. Deuxième phrase, plus longue. Troisième !"
        assert " ".join(catalogue.chunks(texte)).split() == texte.split()

    def test_un_texte_vide_ne_donne_aucun_morceau(self):
        assert catalogue.chunks("   ") == []


class TestAssociationDesParametres:
    """Une erreur ici enverrait le script dans le champ « température »."""

    NOMS = ["text_input", "language_id", "audio_prompt_path_input",
            "exaggeration_input", "temperature_input", "seed_num_input", "cfgw_input"]

    def _params(self):
        return catalogue.params_for(language="fr", exaggeration=0.7,
                                    temperature=0.9, cfg_weight=0.4, seed=3)

    def test_association_par_nom_de_parametre(self):
        args = zerogpu_client._arguments(self.NOMS, self._params(), "Bonjour.", [])
        assert [args[0], args[1], args[3], args[4], args[5], args[6]] == \
            ["Bonjour.", "fr", 0.7, 0.9, 3, 0.4]
        # La reference n'est JAMAIS None : voir TestVoixDeReference.
        assert isinstance(args[2], dict)

    def test_association_par_libelle_quand_le_space_ne_nomme_pas(self):
        labels = ["Text to synthesize (max chars 300)", "Language ID",
                  "Reference audio file", "Exaggeration", "Temperature",
                  "Random seed", "CFG/Pace"]
        args = zerogpu_client._arguments(labels, self._params(), "Salut.", [])
        assert [args[0], args[1], args[3]] == ["Salut.", "fr", 0.7]
        assert isinstance(args[2], dict)

    def test_repli_sur_l_ordre_du_catalogue_si_rien_n_est_nomme(self):
        anonymes = [f"#{index}" for index in range(7)]
        args = zerogpu_client._arguments(anonymes, self._params(), "Test.", self.NOMS)
        assert args[0] == "Test." and args[1] == "fr"

    def test_un_parametre_inconnu_recoit_none_et_non_une_valeur_inventee(self):
        args = zerogpu_client._arguments(["text_input", "mystere"],
                                         self._params(), "Bonjour.", [])
        assert args == ["Bonjour.", None]

    def test_la_voix_de_reference_n_est_jamais_laissee_vide(self):
        """Le defaut du Space est casse par l'API : voir TestVoixDeReference."""
        args = zerogpu_client._arguments(["audio_prompt_path_input"],
                                         self._params(), "x", [])
        assert args[0] is not None


class TestChoixDeLEndpoint:
    def test_un_endpoint_nomme_est_prefere(self):
        info = {"named_endpoints": {"/tts": {"parameters": [{"label": "a"}]}},
                "unnamed_endpoints": {}}
        name, parameters = zerogpu_client._pick_endpoint(info, None)
        assert name == "/tts" and len(parameters) == 1

    def test_celui_qui_a_le_plus_de_parametres_l_emporte(self):
        """Un Space expose souvent des fonctions annexes a un seul parametre."""
        info = {"named_endpoints": {
            "/change_language": {"parameters": [{"label": "lang"}]},
            "/generate": {"parameters": [{"label": "a"}, {"label": "b"}]}}}
        name, _ = zerogpu_client._pick_endpoint(info, None)
        assert name == "/generate"

    def test_les_endpoints_anonymes_servent_de_repli(self):
        info = {"named_endpoints": {}, "unnamed_endpoints": {0: {"parameters": [{}]}}}
        name, _ = zerogpu_client._pick_endpoint(info, None)
        assert name == 0

    def test_un_space_sans_api_est_une_erreur_lisible(self):
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client._pick_endpoint({}, None)
        assert "aucune API" in str(erreur.value)

    def test_un_endpoint_demande_mais_absent_est_dit_clairement(self):
        info = {"named_endpoints": {"/autre": {"parameters": []}}}
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client._pick_endpoint(info, "/absent")
        assert "/autre" in str(erreur.value), "les endpoints réels doivent être listés"


class TestMesures:
    def test_le_rtf_est_la_generation_divisee_par_l_audio(self):
        mesure = catalogue.Measure(audio_s=200.0, gpu_s=52.0, total_s=64.0)
        assert mesure.rtf == pytest.approx(0.26)

    def test_le_rtf_total_inclut_l_attente(self):
        mesure = catalogue.Measure(audio_s=200.0, gpu_s=52.0, total_s=64.0)
        assert mesure.rtf_total == pytest.approx(0.32)

    def test_sans_audio_le_rtf_se_tait_au_lieu_d_inventer(self):
        assert catalogue.Measure(audio_s=0.0, gpu_s=52.0).rtf is None

    def test_le_resume_a_la_forme_demandee(self):
        mesure = catalogue.Measure(words=500, audio_s=200.0, gpu_s=52.0, total_s=64.0)
        assert catalogue.summarize(mesure) == (
            "500 mots → 3 min 20 d'audio → 52 s de génération → RTF 0,26")

    def test_les_mots_sont_comptes_comme_un_humain_les_compte(self):
        assert catalogue.word_count("  Bonjour   le\nmonde  ") == 3
        assert catalogue.word_count("") == 0


class TestJournalDuBanc:
    def test_une_ligne_abimee_ne_perd_pas_les_autres(self):
        """C'est tout l'interet du JSON Lines."""
        contenu = ('{"engine": "zerogpu", "rtf": 0.26}\n'
                   '{ceci n\'est pas du JSON\n'
                   '{"engine": "zerogpu", "rtf": 0.30}\n')
        assert len(bench.rows(contenu)) == 2

    def test_la_moyenne_dit_sur_combien_d_essais_elle_porte(self):
        resume = bench.compare([{"engine": "zerogpu", "rtf": 0.2},
                                {"engine": "zerogpu", "rtf": 0.4}])
        assert resume["zerogpu"] == {"runs": 2, "rtf": pytest.approx(0.3)}

    def test_un_essai_sans_rtf_n_entre_pas_dans_la_moyenne(self):
        assert bench.compare([{"engine": "zerogpu", "rtf": None}]) == {}

    def test_les_moteurs_sont_compares_separement(self):
        resume = bench.compare([{"engine": "zerogpu", "rtf": 0.3},
                                {"engine": "chatterbox", "rtf": 28.0}])
        assert set(resume) == {"zerogpu", "chatterbox"}

    def test_une_entree_reprend_les_mesures_sans_les_arrondir_a_faux(self):
        mesure = catalogue.Measure(words=500, audio_s=200.0, gpu_s=52.0, total_s=64.0)
        entree = bench.entry_from(mesure, space="X/Y", label="essai")
        assert entree.rtf == pytest.approx(0.26) and entree.space == "X/Y"

    def test_le_journal_est_relisible_apres_ecriture(self, tmp_path, monkeypatch):
        cible = tmp_path / "journal.jsonl"
        monkeypatch.setattr(bench, "path", lambda: cible)
        mesure = catalogue.Measure(words=10, audio_s=4.0, gpu_s=1.0, total_s=2.0)
        bench.append(bench.entry_from(mesure))
        lignes = bench.load()
        assert len(lignes) == 1 and lignes[0]["words"] == 10
        json.loads(cible.read_text(encoding="utf-8").strip())


class TestJeton:
    def test_la_variable_d_environnement_est_prioritaire(self, monkeypatch):
        monkeypatch.setenv(zerogpu_token.ENV_VAR, "hf_depuis_env")
        assert zerogpu_token.load_token() == "hf_depuis_env"
        assert zerogpu_token.ENV_VAR in zerogpu_token.describe()

    def test_le_fichier_sert_de_repli(self, monkeypatch, tmp_path):
        monkeypatch.delenv(zerogpu_token.ENV_VAR, raising=False)
        cible = tmp_path / "huggingface_token.txt"
        cible.write_text("hf_depuis_fichier\n", encoding="utf-8")
        monkeypatch.setattr(zerogpu_token, "token_file", lambda: cible)
        assert zerogpu_token.load_token() == "hf_depuis_fichier"

    def test_un_bom_windows_ne_corrompt_pas_le_jeton(self, monkeypatch, tmp_path):
        """PowerShell 5.1 ecrit un BOM que .strip() ne retire pas -- la meme
        faute avait deja corrompu la cle YouTube."""
        monkeypatch.delenv(zerogpu_token.ENV_VAR, raising=False)
        cible = tmp_path / "huggingface_token.txt"
        cible.write_bytes(b"\xef\xbb\xbfhf_avec_bom")
        monkeypatch.setattr(zerogpu_token, "token_file", lambda: cible)
        assert zerogpu_token.load_token() == "hf_avec_bom"

    def test_l_absence_de_jeton_ne_leve_jamais(self, monkeypatch, tmp_path):
        monkeypatch.delenv(zerogpu_token.ENV_VAR, raising=False)
        monkeypatch.setattr(zerogpu_token, "token_file", lambda: tmp_path / "absent.txt")
        assert zerogpu_token.load_token() == ""
        assert zerogpu_token.describe() == ""

    def test_le_jeton_n_est_ecrit_nulle_part_dans_le_depot(self):
        """Garde-fou : aucun secret dans le code, le depot ou l'executable."""
        for nom in ("zerogpu_token.py", "zerogpu_client.py", "zerogpu_service.py"):
            source = (REPO / "voice_studio" / nom).read_text(encoding="utf-8")
            assert "hf_" not in source.replace("hf_depuis", "")


class TestMessagesDErreur:
    """Une panne distante doit s'expliquer, jamais s'afficher en trace Python."""

    def test_le_quota_epuise_dit_quand_il_revient(self):
        message = zerogpu_client._explain(Exception("GPU quota exceeded"), "X/Y")
        assert "Quota GPU épuisé" in message and "24 h" in message

    def test_un_space_introuvable_nomme_le_space(self):
        message = zerogpu_client._explain(Exception("404 Not Found"), "X/Y")
        assert "X/Y" in message

    def test_une_coupure_reseau_est_dite_simplement(self):
        message = zerogpu_client._explain(Exception("Connection refused"), "X/Y")
        assert "connexion Internet" in message

    def test_une_erreur_inconnue_ne_montre_pas_de_trace(self):
        message = zerogpu_client._explain(Exception("Traceback ..."), "X/Y")
        assert "Détail technique" in message and not message.startswith("Traceback")


class TestConfiguration:
    def test_le_catalogue_se_lit(self):
        assert catalogue.space_id(), "un Space par défaut doit être proposé"

    def test_les_bornes_sont_celles_du_chatterbox_local(self):
        """Comparer deux exécutions du MEME modele n'a de sens qu'a reglages
        identiques."""
        serre = catalogue.params_for(exaggeration=99.0, temperature=99.0,
                                     cfg_weight=99.0)
        from voice_studio import chatterbox_catalogue

        bornes = chatterbox_catalogue.limits()
        assert serre.exaggeration == bornes["exaggeration"][1]
        assert serre.temperature == bornes["temperature"][1]

    def test_la_langue_par_defaut_est_le_francais(self):
        assert catalogue.params_for().language == "fr"

    def test_un_catalogue_absent_ne_casse_rien(self, monkeypatch, tmp_path):
        import core.config_loader as loader

        monkeypatch.setattr(loader, "CONFIG_DIR", tmp_path)
        assert catalogue.limits()["max_chars_per_chunk"] == 300
        assert catalogue.space_id() == "", "aucune adresse ne doit être inventée"

    def test_les_chiffres_de_quota_portent_leur_date_et_leur_source(self):
        """Ils bougent : les afficher sans dire quand ils ont ete lus serait
        les presenter comme des constantes."""
        note = catalogue.quota_note()
        assert note.get("checked_on") and note.get("source")


class TestAssemblageDeBoutEnBout:
    """Le chemin complet -- decouper, generer, recoller, mesurer -- avec un
    FAUX Space qui rend de vrais fichiers WAV.

    C'est la seule facon honnete de verifier l'assemblage sans GPU : le
    recollage, la duree LUE dans le fichier produit, et le fait qu'un script
    decoupe en quatre morceaux redonne bien UN fichier. Ce que ce test ne
    prouve pas, et ne pretend pas prouver : la vitesse, la file d'attente et la
    qualite de la voix, qui n'existent qu'en appelant vraiment Hugging Face.
    """

    RATE = 24000

    @pytest.fixture
    def faux_space(self, tmp_path, monkeypatch):
        import numpy as np
        import soundfile as sf

        from voice_studio import zerogpu_service

        produits = []

        def _faux_generate_chunk(client, connection, text, params,
                                 on_status=None, cancel_token=None):
            # Un morceau d'audio dont la duree depend du texte : c'est ce qui
            # permet de verifier que la concatenation respecte l'ordre.
            seconds = 0.2 + len(text) / 1000.0
            samples = np.linspace(0, 1, int(seconds * self.RATE), dtype="float32")
            path = tmp_path / f"morceau_{len(produits)}.wav"
            sf.write(str(path), samples, self.RATE, subtype="PCM_16")
            produits.append(str(path))
            if on_status is not None:
                on_status({"phase": "gpu", "in_queue": False})
            return zerogpu_client.ChunkResult(
                index=0, total=0, text=text, path=str(path),
                queue_s=1.5, gpu_s=seconds * 0.3)

        monkeypatch.setattr(zerogpu_client, "generate_chunk", _faux_generate_chunk)
        monkeypatch.setattr(
            zerogpu_service.zerogpu_client, "connect",
            lambda cancel_token=None: (object(), zerogpu_client.Connection(
                space="faux/space", api_name="/generate",
                parameter_names=["text_input"])))
        return tmp_path, produits

    def test_un_script_long_donne_un_seul_fichier(self, faux_space):
        from voice_studio import zerogpu_service

        tmp_path, _ = faux_space
        script = "Une phrase de test suffisamment longue pour être découpée. " * 30
        sortie = tmp_path / "narration.wav"
        resultat = zerogpu_service.generate(script, catalogue.params_for(), str(sortie))

        assert Path(resultat.wav_path).is_file()
        assert resultat.measure.chunks > 1, "le script doit avoir été découpé"

    def test_la_duree_est_lue_dans_le_fichier_et_non_additionnee(self, faux_space):
        """Meme regle que video_service : la duree vient du fichier produit."""
        import soundfile as sf

        from voice_studio import zerogpu_service

        tmp_path, _ = faux_space
        script = "Première phrase. Deuxième phrase un peu plus longue que la première."
        sortie = tmp_path / "narration.wav"
        resultat = zerogpu_service.generate(script, catalogue.params_for(), str(sortie))

        info = sf.info(str(sortie))
        assert resultat.measure.audio_s == pytest.approx(info.duration, abs=0.01)

    def test_les_morceaux_sont_separes_par_un_silence(self, faux_space):
        """Coller deux respirations bout a bout s'entend."""
        import soundfile as sf

        from voice_studio import zerogpu_service

        tmp_path, produits = faux_space
        script = "Une phrase de test suffisamment longue pour être découpée. " * 30
        sortie = tmp_path / "narration.wav"
        zerogpu_service.generate(script, catalogue.params_for(), str(sortie))

        morceaux = sum(sf.info(path).duration for path in produits)
        silences = (len(produits) - 1) * zerogpu_service.JOIN_SILENCE_S
        assert sf.info(str(sortie)).duration == pytest.approx(morceaux + silences, abs=0.05)

    def test_l_attente_et_la_generation_restent_separees(self, faux_space):
        from voice_studio import zerogpu_service

        tmp_path, produits = faux_space
        script = "Une phrase de test suffisamment longue pour être découpée. " * 30
        resultat = zerogpu_service.generate(script, catalogue.params_for(),
                                            str(tmp_path / "narration.wav"))
        assert resultat.measure.queue_s == pytest.approx(1.5 * len(produits))
        assert resultat.measure.gpu_s > 0
        assert resultat.measure.queue_s != resultat.measure.gpu_s

    def test_aucun_fichier_partiel_ne_subsiste(self, faux_space):
        from voice_studio import zerogpu_service

        tmp_path, _ = faux_space
        sortie = tmp_path / "narration.wav"
        zerogpu_service.generate("Bonjour le monde.", catalogue.params_for(), str(sortie))
        assert not list(tmp_path.glob("*.part"))

    def test_la_progression_annonce_le_plan_puis_chaque_morceau(self, faux_space):
        from voice_studio import zerogpu_service

        tmp_path, _ = faux_space
        vus = []
        script = "Une phrase de test suffisamment longue pour être découpée. " * 30
        zerogpu_service.generate(script, catalogue.params_for(),
                                 str(tmp_path / "narration.wav"),
                                 on_progress=lambda event: vus.append(event["event"]))
        assert vus[0] == "plan" and vus[1] == "connected"
        assert "chunk_done" in vus and vus[-1] == "done"

    def test_un_script_vide_est_refuse_avant_tout_appel(self, faux_space):
        from voice_studio import zerogpu_service

        tmp_path, produits = faux_space
        with pytest.raises(zerogpu_client.ZeroGpuError):
            zerogpu_service.generate("   ", catalogue.params_for(),
                                     str(tmp_path / "x.wav"))
        assert not produits, "aucun quota GPU ne doit être dépensé pour rien"

    def test_des_frequences_differentes_sont_refusees_plutot_que_melangees(
            self, tmp_path):
        """Recoller du 24 kHz et du 16 kHz sans reechantillonner donnerait une
        voix au mauvais tempo. Mieux vaut le dire."""
        import numpy as np
        import soundfile as sf

        from voice_studio import zerogpu_service

        premier = tmp_path / "a.wav"
        second = tmp_path / "b.wav"
        sf.write(str(premier), np.zeros(2400, dtype="float32"), 24000)
        sf.write(str(second), np.zeros(1600, dtype="float32"), 16000)
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_service._join([str(premier), str(second)], str(tmp_path / "out.wav"))
        assert "échantillonnage" in str(erreur.value)


class TestAucunAppelArbitraire:
    """« Si la découverte dynamique échoue, l'interface doit afficher une
    erreur claire plutôt que tenter un appel arbitraire. »

    DEFAUT REEL TROUVE EN RELISANT CETTE EXIGENCE : `_arguments` met None
    partout ou il ne reconnait rien. Pour le champ du texte, cela produisait un
    appel qui PART, consomme du quota GPU, et revient avec la voix par defaut
    du Space lisant son propre exemple -- un echec silencieux, et le plus cher
    des trois.
    """

    def _params(self):
        return catalogue.params_for()

    def test_un_space_dont_aucun_parametre_n_est_reconnu_est_refuse(self):
        noms = ["machin", "truc"]
        args = zerogpu_client._arguments(noms, self._params(), "Mon script.", [])
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client.check_arguments(noms, args, "Mon script.")
        assert "aucun de ses paramètres" in str(erreur.value)

    def test_le_message_nomme_les_parametres_reellement_publies(self):
        """Sans eux, l'utilisateur n'a aucune prise pour corriger."""
        noms = ["machin", "truc"]
        args = zerogpu_client._arguments(noms, self._params(), "x", [])
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client.check_arguments(noms, args, "x")
        assert "machin, truc" in str(erreur.value)

    def test_le_message_dit_qu_aucun_quota_n_a_ete_depense(self):
        noms = ["machin"]
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client.check_arguments(noms, [None], "x")
        assert "Aucun appel n'a été envoyé" in str(erreur.value)

    def test_un_space_sans_aucun_parametre_est_refuse(self):
        with pytest.raises(zerogpu_client.ZeroGpuError):
            zerogpu_client.check_arguments([], [], "Mon script.")

    def test_une_signature_valide_passe_toujours(self):
        noms = ["text_input", "language_id", "exaggeration_input"]
        args = zerogpu_client._arguments(noms, self._params(), "Mon script.", [])
        zerogpu_client.check_arguments(noms, args, "Mon script.")

    def test_le_repli_par_libelle_passe_aussi(self):
        noms = ["Text to synthesize (max chars 300)", "Language ID"]
        args = zerogpu_client._arguments(noms, self._params(), "Mon script.", [])
        zerogpu_client.check_arguments(noms, args, "Mon script.")

    def test_la_verification_a_lieu_avant_tout_envoi(self):
        """Le garde-fou ne sert a rien s'il est appele apres submit()."""
        source = (REPO / "voice_studio" / "zerogpu_client.py").read_text(encoding="utf-8")
        assert source.index("check_arguments(connection.parameter_names") < \
            source.index("job = client.submit")


class TestLaBoucleDeSondage:
    """La boucle de generate_chunk, executee POUR DE VRAI.

    DEFAUT REEL SIGNALE EN USAGE : « AttributeError — 'CancelToken' object has
    no attribute 'cancelled' », a chaque generation. `is_cancelled` est une
    PROPRIETE, pas une methode.

    POURQUOI LES TESTS PRECEDENTS NE L'ONT PAS VU, et c'est la vraie lecon :
    le test de bout en bout remplacait `generate_chunk` en entier par un faux.
    La boucle de sondage n'etait donc JAMAIS executee -- un test qui remplace
    la fonction a verifier ne verifie rien de son contenu. Ceux-ci font tourner
    la vraie boucle avec un faux client, et auraient attrape la faute a la
    premiere ligne.
    """

    class _FauxJob:
        """Imite gradio_client.Job : file d'attente puis generation."""

        def __init__(self, path, tours_en_file=2):
            self.path = path
            self.tours = 0
            self.tours_en_file = tours_en_file
            self.annule = False

        def status(self):
            from gradio_client.utils import Status, StatusUpdate

            self.tours += 1
            code = (Status.IN_QUEUE if self.tours <= self.tours_en_file
                    else Status.PROCESSING)
            return StatusUpdate(code=code, rank=0, queue_size=1, eta=None,
                                success=None, time=None, progress_data=None,
                                log=None)

        def done(self):
            return self.tours > self.tours_en_file + 1

        def cancel(self):
            self.annule = True

        def result(self):
            return self.path

    class _FauxClient:
        def __init__(self, job):
            self._job = job
            self.appels = []

        def submit(self, *args, api_name=None):
            self.appels.append((args, api_name))
            return self._job

    @pytest.fixture
    def piece(self, tmp_path):
        import numpy as np
        import soundfile as sf

        chemin = tmp_path / "morceau.wav"
        sf.write(str(chemin), np.zeros(2400, dtype="float32"), 24000)
        return str(chemin)

    def _connexion(self):
        return zerogpu_client.Connection(
            space="faux/space", api_name="/generate",
            parameter_names=["text_input", "language_id"])

    def test_la_boucle_tourne_sans_erreur_avec_un_vrai_jeton(self, piece, monkeypatch):
        """Le test qui manquait : un CancelToken REEL traverse la boucle."""
        from core.cancellation import CancelToken
        from voice_studio import zerogpu_client as module

        monkeypatch.setattr(module, "POLL_S", 0.0)
        job = self._FauxJob(piece)
        resultat = zerogpu_client.generate_chunk(
            self._FauxClient(job), self._connexion(), "Bonjour.",
            catalogue.params_for(), cancel_token=CancelToken())
        assert resultat.path == piece

    def test_l_attente_et_la_generation_sont_bien_separees(self, piece, monkeypatch):
        from voice_studio import zerogpu_client as module

        monkeypatch.setattr(module, "POLL_S", 0.0)
        resultat = zerogpu_client.generate_chunk(
            self._FauxClient(self._FauxJob(piece)), self._connexion(),
            "Bonjour.", catalogue.params_for())
        assert resultat.queue_s >= 0 and resultat.gpu_s >= 0
        assert resultat.total_s == pytest.approx(resultat.queue_s + resultat.gpu_s)

    def test_une_annulation_arrete_la_boucle_et_le_job(self, piece, monkeypatch):
        from core.cancellation import CancelToken
        from utils.errors import CancelledError
        from voice_studio import zerogpu_client as module

        monkeypatch.setattr(module, "POLL_S", 0.0)
        jeton = CancelToken()
        jeton.cancel()
        job = self._FauxJob(piece)
        with pytest.raises(CancelledError):
            zerogpu_client.generate_chunk(
                self._FauxClient(job), self._connexion(), "Bonjour.",
                catalogue.params_for(), cancel_token=jeton)
        assert job.annule, "le job distant doit être annulé, pas seulement abandonné"

    def test_les_etats_sont_rapportes_a_l_interface(self, piece, monkeypatch):
        from voice_studio import zerogpu_client as module

        monkeypatch.setattr(module, "POLL_S", 0.0)
        vus = []
        zerogpu_client.generate_chunk(
            self._FauxClient(self._FauxJob(piece)), self._connexion(),
            "Bonjour.", catalogue.params_for(), on_status=vus.append)
        assert any(etat["in_queue"] for etat in vus), "la file doit être visible"
        assert any(etat["phase"] == "gpu" for etat in vus)

    def test_l_endpoint_decouvert_est_bien_celui_appele(self, piece, monkeypatch):
        from voice_studio import zerogpu_client as module

        monkeypatch.setattr(module, "POLL_S", 0.0)
        client = self._FauxClient(self._FauxJob(piece))
        zerogpu_client.generate_chunk(client, self._connexion(), "Bonjour.",
                                      catalogue.params_for())
        args, api_name = client.appels[0]
        assert api_name == "/generate"
        assert args[0] == "Bonjour." and args[1] == "fr"

    def test_une_reponse_sans_fichier_est_une_erreur_lisible(self, monkeypatch, tmp_path):
        from voice_studio import zerogpu_client as module

        monkeypatch.setattr(module, "POLL_S", 0.0)
        job = self._FauxJob(str(tmp_path / "inexistant.wav"))
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client.generate_chunk(
                self._FauxClient(job), self._connexion(), "Bonjour.",
                catalogue.params_for())
        assert "sans fichier audio" in str(erreur.value)

    def test_le_chemin_est_accepte_sous_ses_trois_formes(self, piece):
        """Un composant Audio de Gradio rend un chemin, mais certains Spaces
        rendent un tuple ou un dictionnaire."""
        assert zerogpu_client._audio_path(piece) == piece
        assert zerogpu_client._audio_path([piece, None]) == piece
        assert zerogpu_client._audio_path({"path": piece}) == piece
        assert zerogpu_client._audio_path(None) == ""


class TestVoixDeReference:
    """La reference EST obligatoire, et ce n'est pas un choix de confort.

    DEFAUT REEL SIGNALE EN USAGE : la connexion reussissait, l'endpoint
    /generate_tts_audio etait bien decouvert, puis chaque morceau echouait sur
    « FileNotFoundError » -- sans autre detail, Gradio ne divulguant que le nom
    de l'exception.

    CAUSE, lue dans multilingual_app.py du depot officiel :

        chosen_prompt = audio_prompt_path_input or default_audio_for_ui(language_id)

    et ce defaut est une ADRESSE HTTPS, pas un fichier. Dans l'interface web,
    Gradio telecharge l'adresse et passe un vrai chemin local a la fonction.
    Par l'API, personne ne fait ce travail : le serveur passe l'adresse telle
    quelle a un chargeur audio, qui echoue. Envoyer None etait donc une panne
    garantie.
    """

    def test_une_reference_existe_pour_chaque_langue_proposee(self):
        """Les langues du menu et celles du catalogue doivent coincider."""
        for langue in ("fr", "en", "es", "de", "it", "pt"):
            assert catalogue.reference_for(langue), f"aucune référence pour {langue}"

    def test_la_reference_francaise_est_celle_du_depot_officiel(self):
        assert catalogue.reference_for("fr").endswith("mtl_prompts/fr_f1.flac")

    def test_une_langue_inconnue_ne_donne_pas_une_reference_inventee(self):
        assert catalogue.reference_for("klingon") == ""

    def test_la_reference_part_sous_la_forme_attendue_par_gradio(self):
        """handle_file() construit la charge utile que le serveur sait résoudre."""
        charge = zerogpu_client._reference_payload(catalogue.params_for(language="fr"))
        assert charge["meta"]["_type"] == "gradio.FileData"
        assert charge["url"].startswith("https://")

    def test_le_fichier_de_l_utilisateur_prime_sur_l_echantillon_officiel(self, tmp_path):
        fichier = tmp_path / "ma_voix.wav"
        fichier.write_bytes(b"RIFF")
        charge = zerogpu_client._reference_payload(
            catalogue.params_for(language="fr", reference=str(fichier)))
        assert charge["orig_name"] == "ma_voix.wav"
        assert "url" not in charge, "un fichier local n'est pas une adresse"

    def test_un_fichier_inexistant_est_dit_clairement(self, tmp_path):
        with pytest.raises(zerogpu_client.ZeroGpuError) as erreur:
            zerogpu_client._reference_payload(
                catalogue.params_for(reference=str(tmp_path / "absent.wav")))
        assert "introuvable" in str(erreur.value)

    def test_sans_aucune_reference_disponible_on_envoie_none(self, monkeypatch):
        """Ne rien envoyer reste preferable a envoyer une adresse inventee."""
        monkeypatch.setattr(catalogue, "reference_for", lambda langue: "")
        assert zerogpu_client._reference_payload(catalogue.params_for()) is None

    def test_le_message_d_erreur_explique_la_panne_du_space(self):
        message = zerogpu_client._explain(Exception("FileNotFoundError"), "X/Y")
        assert "voix de référence" in message
        assert "pas chez toi" in message, "l'utilisateur ne doit pas chercher de son côté"
