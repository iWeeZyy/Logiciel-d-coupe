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
        assert args == ["Bonjour.", "fr", None, 0.7, 0.9, 3, 0.4]

    def test_association_par_libelle_quand_le_space_ne_nomme_pas(self):
        labels = ["Text to synthesize (max chars 300)", "Language ID",
                  "Reference audio file", "Exaggeration", "Temperature",
                  "Random seed", "CFG/Pace"]
        args = zerogpu_client._arguments(labels, self._params(), "Salut.", [])
        assert args == ["Salut.", "fr", None, 0.7, 0.9, 3, 0.4]

    def test_repli_sur_l_ordre_du_catalogue_si_rien_n_est_nomme(self):
        anonymes = [f"#{index}" for index in range(7)]
        args = zerogpu_client._arguments(anonymes, self._params(), "Test.", self.NOMS)
        assert args[0] == "Test." and args[1] == "fr"

    def test_un_parametre_inconnu_recoit_none_et_non_une_valeur_inventee(self):
        args = zerogpu_client._arguments(["text_input", "mystere"],
                                         self._params(), "Bonjour.", [])
        assert args == ["Bonjour.", None]

    def test_la_voix_de_reference_absente_vaut_none(self):
        args = zerogpu_client._arguments(["audio_prompt_path_input"],
                                         self._params(), "x", [])
        assert args == [None]


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
