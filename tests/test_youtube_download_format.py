"""Selection de format et fichier de sortie du telechargement (yt-dlp).

Tests purs : aucune requete reseau, aucun appel a yt-dlp. Ils portent sur la
chaine de format construite et sur la facon dont le fichier produit est
retrouve.
"""
from __future__ import annotations

from youtube.downloader import build_format, configured_max_height, find_downloaded_file


class TestFormat:
    def test_la_meilleure_video_n_est_plus_limitee_au_mp4(self):
        """YouTube ne sert en MP4 que ses flux H.264 ; le 1440p, le 2160p et
        souvent le 1080p60 n'existent qu'en VP9 ou AV1. Exiger du MP4 plafonnait
        donc la qualite sans le dire."""
        selector = build_format()
        assert "[ext=mp4]" not in selector
        assert selector.startswith("bestvideo*")

    def test_l_audio_reste_en_aac_pour_un_mp4_valide(self):
        """L'Opus des pistes WebM se remuxe mal en MP4 et fait echouer la
        fusion ; l'AAC que YouTube propose toujours passe proprement."""
        assert "bestaudio[ext=m4a]" in build_format()

    def test_un_repli_existe_si_l_aac_manque(self):
        selector = build_format()
        assert selector.count("/") >= 2, "plusieurs replis attendus"
        assert selector.endswith("best")

    def test_le_plafond_de_definition_s_applique_a_toutes_les_branches(self):
        selector = build_format(1080)
        assert selector.count("[height<=1080]") == 3

    def test_aucun_plafond_par_defaut(self):
        assert "[height<=" not in build_format()
        assert "[height<=" not in build_format(0)

    def test_le_plafond_vient_de_la_configuration(self):
        assert configured_max_height() == 0, "config/youtube.json : aucune limite par défaut"


class TestFichierProduit:
    def test_le_mp4_est_trouve(self, tmp_path):
        (tmp_path / "abc123.mp4").write_bytes(b"x")
        found = find_downloaded_file(str(tmp_path), "abc123")
        assert found is not None and found.suffix == ".mp4"

    def test_un_autre_conteneur_n_est_plus_pris_pour_un_echec(self, tmp_path):
        """Quand la fusion se rabat sur un autre conteneur, le fichier existe :
        l'ancien code annoncait un echec alors que le telechargement avait
        reussi."""
        (tmp_path / "abc123.mkv").write_bytes(b"x")
        found = find_downloaded_file(str(tmp_path), "abc123")
        assert found is not None and found.suffix == ".mkv"

    def test_le_mp4_est_prefere_quand_les_deux_existent(self, tmp_path):
        (tmp_path / "abc123.mkv").write_bytes(b"x")
        (tmp_path / "abc123.mp4").write_bytes(b"x")
        assert find_downloaded_file(str(tmp_path), "abc123").suffix == ".mp4"

    def test_aucun_fichier_renvoie_none(self, tmp_path):
        assert find_downloaded_file(str(tmp_path), "abc123") is None

    def test_une_autre_video_n_est_pas_confondue(self, tmp_path):
        (tmp_path / "autre.mp4").write_bytes(b"x")
        assert find_downloaded_file(str(tmp_path), "abc123") is None
