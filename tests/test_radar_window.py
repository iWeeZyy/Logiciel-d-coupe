"""La fenetre de temps du Radar : ce qui est demande, et ce qui est affiche.

DEUX SYMPTOMES SIGNALES EN USAGE REEL, trois causes distinctes.

« Des clips datent de plus de 7 jours. »
  La liste de l'interface n'etait bornee par RIEN. Le selecteur de periode ne
  commandait que la fenetre du prochain scan et les compteurs du bandeau : tout
  ce qui avait ete enregistre depuis toujours restait affiche. Le bandeau, lui,
  disait vrai -- les deux affichaient donc deux choses differentes.

« J'ai regarde la chaine Twitch et des clips ne figurent pas sur le Radar. »
  Deux causes, independantes :
    - /clips ordonne les resultats PAR NOMBRE DE VUES, et une seule page etait
      demandee : au-dela du plafond, un clip recent mais encore peu vu
      n'apparaissait jamais ;
    - `max_results_per_creator` etait ecrit dans config/radar.json mais lu
      NULLE PART, donc le plafond restait celui du moteur quoi qu'on y mette.

Une quatrieme correction, de precaution plutot que de symptome constate : les
bornes de date partaient au format `isoformat()` brut
(« ...33.123456+00:00 »), la ou Twitch comme YouTube documentent la forme a
suffixe Z. Une borne qu'une API n'arrive pas a lire n'est pas signalee, elle
est ignoree.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from radar.engine import PERIODS, since_iso
from radar.models import KIND_CLIP, Creator, rfc3339
from radar.platforms.twitch import CLIP_WINDOW_MARGIN, CLIPS_PAGE_SIZE, TwitchAdapter

NOW = datetime(2026, 9, 16, 5, 22, 33, 123456, tzinfo=timezone.utc)


def _creator():
    return Creator(platform="twitch", platform_id="T1", username="sardoche",
                   display_name="Sardoche")


class FauxTwitch(TwitchAdapter):
    """Adaptateur reel, transport simule, UNE REPONSE PAR APPEL.

    Different du FakeTwitch de test_radar_twitch.py, qui renvoie toujours la
    meme chose : la pagination ne peut se tester qu'avec des reponses qui se
    suivent.
    """

    def __init__(self, pages, games=None):
        super().__init__(client_id="id", client_secret="secret")
        self._pages = list(pages)
        self._games_payload = games or {"data": []}
        self.calls = []
        self._token = "jeton"
        self._token_expires_at = 1e12

    def _get(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == "games":
            return self._games_payload
        return self._pages.pop(0) if self._pages else {"data": []}

    @property
    def clip_calls(self):
        return [params for endpoint, params in self.calls if endpoint == "clips"]


def _clip(identifier, views=1):
    return {"id": identifier, "title": identifier, "url": f"https://clips.twitch.tv/{identifier}",
            "created_at": "2026-09-16T04:00:00Z", "duration": 30.4, "view_count": views,
            "game_id": "", "creator_name": "qqn", "video_id": ""}


def _page(identifiers, cursor=""):
    payload = {"data": [_clip(i) for i in identifiers]}
    if cursor:
        payload["pagination"] = {"cursor": cursor}
    return payload


# --------------------------------------------------- format des bornes

class TestFormatDesBornes:
    def test_le_suffixe_est_z_et_non_un_decalage_numerique(self):
        assert rfc3339(NOW) == "2026-09-16T05:22:33Z"

    def test_les_microsecondes_disparaissent(self):
        """Twitch ignore deja les secondes ; les microsecondes n'ont aucun sens
        et sont ce qui rend la chaine illisible pour son analyseur."""
        assert "." not in rfc3339(NOW)

    def test_un_instant_dans_un_autre_fuseau_est_ramene_en_utc(self):
        paris = timezone(timedelta(hours=2))
        assert rfc3339(NOW.astimezone(paris)) == "2026-09-16T05:22:33Z"

    @pytest.mark.parametrize("periode", list(PERIODS))
    def test_toutes_les_periodes_sortent_au_meme_format(self, periode):
        valeur = since_iso(periode, NOW)
        assert valeur.endswith("Z") and "+" not in valeur and "." not in valeur

    def test_la_comparaison_avec_une_date_d_api_redevient_juste(self):
        """LE POINT NON EVIDENT : ces chaines sont comparees telles quelles, en
        SQL et en Python, a des dates d'API qui finissent toutes par Z.
        Comparer « ...33Z » a « ...33.123456+00:00 » revient a comparer 'Z' a
        '.', ce qui n'a aucun sens."""
        seuil = since_iso("24h", NOW)
        recent = rfc3339(NOW - timedelta(hours=1))
        ancien = rfc3339(NOW - timedelta(days=30))
        assert recent >= seuil
        assert not (ancien >= seuil)


# ------------------------------------------------------ bornes de /clips

class TestFenetreDemandeeATwitch:
    def test_les_deux_bornes_sont_envoyees(self, monkeypatch):
        """/clips n'applique sa periode que si les deux bornes sont donnees."""
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page(["a"])])
        adapter._clips(_creator(), since_iso("7j", NOW), 50)

        params = adapter.clip_calls[0]
        assert params["started_at"] == "2026-09-09T05:22:33Z"
        assert "ended_at" in params

    def test_la_borne_de_fin_prend_une_marge_sur_l_horloge_locale(self, monkeypatch):
        """« Maintenant » selon le PC, pas selon Twitch : une machine en retard
        exclurait les clips des dernieres minutes, c'est-a-dire exactement ceux
        qu'on cherche."""
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page(["a"])])
        adapter._clips(_creator(), since_iso("24h", NOW), 50)

        assert adapter.clip_calls[0]["ended_at"] == rfc3339(NOW + CLIP_WINDOW_MARGIN)
        assert CLIP_WINDOW_MARGIN > timedelta(0)

    def test_les_bornes_sont_dans_le_bon_ordre(self, monkeypatch):
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page(["a"])])
        adapter._clips(_creator(), since_iso("6h", NOW), 50)
        params = adapter.clip_calls[0]
        assert params["started_at"] < params["ended_at"]


# --------------------------------------------------------- pagination

class TestPaginationDesClips:
    def test_la_deuxieme_page_est_demandee(self, monkeypatch):
        """LA CAUSE DES CLIPS MANQUANTS : Helix ordonne par nombre de vues. Ne
        lire que la premiere page, c'est ne garder que les plus vus de la
        fenetre -- un clip recent et encore peu vu n'apparait jamais."""
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        pages = [_page([f"a{i}" for i in range(CLIPS_PAGE_SIZE)], cursor="SUITE"),
                 _page(["b0", "b1"])]
        adapter = FauxTwitch(pages)

        clips = adapter._raw_clips(_creator(), since_iso("24h", NOW), CLIPS_PAGE_SIZE + 2)

        assert len(adapter.clip_calls) == 2
        assert adapter.clip_calls[1]["after"] == "SUITE"
        assert [c["id"] for c in clips][-2:] == ["b0", "b1"]

    def test_la_premiere_page_ne_porte_aucun_curseur(self, monkeypatch):
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page(["a"])])
        adapter._raw_clips(_creator(), since_iso("24h", NOW), 50)
        assert "after" not in adapter.clip_calls[0]

    def test_une_page_incomplete_arrete_la_lecture(self, monkeypatch):
        """Meme quand Twitch renvoie quand meme un curseur : une page plus
        courte que demandee signifie qu'il n'y a plus rien apres."""
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page(["a", "b"], cursor="SUITE"), _page(["c"])])
        clips = adapter._raw_clips(_creator(), since_iso("24h", NOW), 100)
        assert len(adapter.clip_calls) == 1
        assert [c["id"] for c in clips] == ["a", "b"]

    def test_l_absence_de_curseur_arrete_la_lecture(self, monkeypatch):
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page([f"a{i}" for i in range(CLIPS_PAGE_SIZE)])])
        adapter._raw_clips(_creator(), since_iso("24h", NOW), CLIPS_PAGE_SIZE * 3)
        assert len(adapter.clip_calls) == 1

    def test_un_curseur_qui_se_repete_ne_boucle_pas_indefiniment(self, monkeypatch):
        """Garde-fou : une API qui renverrait toujours le meme curseur ne doit
        pas figer l'application."""
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        pages = [_page([f"p{n}-{i}" for i in range(CLIPS_PAGE_SIZE)], cursor="MEME")
                 for n in range(50)]
        adapter = FauxTwitch(pages)
        adapter._raw_clips(_creator(), since_iso("24h", NOW), CLIPS_PAGE_SIZE * 3)
        assert len(adapter.clip_calls) <= 4

    def test_le_plafond_demande_est_respecte(self, monkeypatch):
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        pages = [_page([f"a{i}" for i in range(CLIPS_PAGE_SIZE)], cursor="SUITE"),
                 _page([f"b{i}" for i in range(CLIPS_PAGE_SIZE)], cursor="ENCORE")]
        adapter = FauxTwitch(pages)
        clips = adapter._raw_clips(_creator(), since_iso("24h", NOW), 120)
        assert len(clips) == 120
        assert adapter.clip_calls[1]["first"] == 20

    def test_la_pagination_ne_change_rien_au_contenu_d_une_opportunite(self, monkeypatch):
        """LA REGRESSION A EVITER : seule la RECUPERATION change, pas la
        conversion en Opportunity."""
        monkeypatch.setattr("radar.platforms.twitch._utc_now", lambda: NOW)
        adapter = FauxTwitch([_page(["abc"])])
        found = adapter._clips(_creator(), since_iso("24h", NOW), 50)
        assert len(found) == 1
        assert found[0].kind == KIND_CLIP
        assert found[0].content_id == "abc"
        assert found[0].duration_s == 30, "30,4 s arrondi, pas tronque"


# ------------------------------------------------ ce qui est AFFICHE

class TestListeBorneeParLaPeriode:
    """LE SYMPTOME PRINCIPAL : la liste montrait tout ce qui avait ete
    enregistre depuis toujours, quelle que soit la periode choisie."""

    @pytest.fixture
    def page(self, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.radar.radar_page import RadarPage
        from radar.store import RadarStore

        QApplication.instance() or QApplication([])
        monkeypatch.setattr("radar.store.radar_db_path", lambda: tmp_path / "radar.sqlite3")
        return RadarPage()

    def test_la_liste_demande_une_borne_de_date(self, page):
        vu = []
        page.store.list_opportunities = lambda **kwargs: vu.append(kwargs) or []
        page._refresh_tab("twitch")

        assert vu, "la liste doit interroger la base"
        assert all("since" in appel and appel["since"] for appel in vu), \
            "sans borne, un clip trouve il y a trois semaines reste affiche"

    def test_la_borne_suit_le_selecteur_de_periode(self, page):
        vu = []
        page.store.list_opportunities = lambda **kwargs: vu.append(kwargs) or []

        for index in range(page.period_combo.count()):
            vu.clear()
            page.period_combo.setCurrentIndex(index)
            page._refresh_tab("twitch")
            attendu = since_iso(page.period_combo.itemData(index))
            # A la seconde pres : les deux appels ne tombent pas forcement dans
            # la meme seconde d'horloge.
            assert vu[0]["since"][:16] == attendu[:16]

    def test_changer_la_periode_reaffiche_sans_rescanner(self, page, monkeypatch):
        """Ce selecteur n'avait AUCUN effet visible avant un nouveau scan --
        pas meme sur les compteurs du bandeau, qui l'utilisent pourtant."""
        appels = {"listes": 0, "bandeau": 0, "scan": 0}
        monkeypatch.setattr(page, "_refresh_lists", lambda: appels.__setitem__("listes", appels["listes"] + 1))
        monkeypatch.setattr(page, "_refresh_dashboard", lambda: appels.__setitem__("bandeau", appels["bandeau"] + 1))
        monkeypatch.setattr(page, "_start_scan", lambda: appels.__setitem__("scan", appels["scan"] + 1))

        page.period_combo.setCurrentIndex(
            (page.period_combo.currentIndex() + 1) % page.period_combo.count())

        assert appels["listes"] == 1
        assert appels["bandeau"] == 1
        assert appels["scan"] == 0, "changer la periode ne redemande rien a la plateforme"

    def test_le_message_vide_nomme_la_periode(self, page):
        """« Vide » veut dire deux choses maintenant : rien du tout, ou rien
        d'assez recent. Sans le dire, on cherche un scan qui a pourtant
        fonctionne."""
        from PySide6.QtWidgets import QLabel

        page.store.list_opportunities = lambda **kwargs: []
        page._refresh_tab("twitch")

        textes = " ".join(w.text() for w in page.findChildren(QLabel))
        assert "Aucun contenu sur les" in textes
        assert "Élargissez la période" in textes


class TestPlafondParCreateur:
    """`max_results_per_creator` etait ecrit dans config/radar.json et lu nulle
    part : le scan gardait le defaut du moteur quoi qu'on y mette."""

    @pytest.fixture
    def page(self, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.radar.radar_page import RadarPage

        QApplication.instance() or QApplication([])
        monkeypatch.setattr("radar.store.radar_db_path", lambda: tmp_path / "radar.sqlite3")
        return RadarPage()

    def test_la_valeur_de_la_configuration_est_lue(self, page):
        page.config = {"scan": {"max_results_per_creator": 200}}
        assert page._max_results_per_creator() == 200

    @pytest.mark.parametrize("valeur", [None, "", "beaucoup", 0, -5, {}])
    def test_une_valeur_absente_ou_illisible_laisse_le_defaut_du_moteur(self, page, valeur):
        """None, et pas une seconde valeur par defaut ecrite ici : deux defauts
        concurrents finissent par diverger."""
        page.config = {"scan": {"max_results_per_creator": valeur}}
        assert page._max_results_per_creator() is None

    def test_le_fil_de_scan_transmet_le_plafond(self):
        from gui.radar.radar_page import ScanThread

        vu = {}

        class FauxMoteur:
            def scan(self, **kwargs):
                vu.update(kwargs)
                return object()

        fil = ScanThread(FauxMoteur(), ("twitch",), "24h", None, max_results=200)
        fil.run()
        assert vu["max_results"] == 200

    def test_sans_plafond_le_fil_n_impose_rien(self):
        """Le moteur garde sa propre valeur par defaut : le fil ne doit pas en
        porter une seconde."""
        from gui.radar.radar_page import ScanThread

        vu = {}

        class FauxMoteur:
            def scan(self, **kwargs):
                vu.update(kwargs)
                return object()

        fil = ScanThread(FauxMoteur(), ("twitch",), "24h", None, max_results=None)
        fil.run()
        assert "max_results" not in vu


class TestTroncatureDeLaListe:
    """« Un clip ne figure pas sur le Radar » a une troisieme cause possible,
    purement d'affichage : il a bien ete trouve, il est au-dela de la coupe.
    Couper en silence est ce qui rend le symptome indiscernable d'un scan
    rate."""

    @pytest.fixture
    def page(self, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.radar.radar_page import RadarPage

        QApplication.instance() or QApplication([])
        monkeypatch.setattr("radar.store.radar_db_path", lambda: tmp_path / "radar.sqlite3")
        return RadarPage()

    def _opportunites(self, nombre):
        from radar.models import Opportunity

        return [Opportunity(platform="twitch", content_id=f"c{i}", kind=KIND_CLIP,
                            creator_key="twitch:T1", title=f"Clip {i}",
                            url=f"https://clips.twitch.tv/c{i}",
                            published_at="2026-09-16T04:00:00Z")
                for i in range(nombre)]

    def test_on_relit_plus_large_que_ce_qu_on_dessine(self, page):
        """Sinon le tri porterait sur un echantillon deja coupe."""
        from gui.radar.radar_page import LIST_DISPLAY_LIMIT, LIST_FETCH_LIMIT

        assert LIST_FETCH_LIMIT > LIST_DISPLAY_LIMIT
        vu = []
        page.store.list_opportunities = lambda **kwargs: vu.append(kwargs) or []
        page._refresh_tab("twitch")
        assert vu[0]["limit"] == LIST_FETCH_LIMIT

    def test_une_liste_coupee_le_dit(self, page):
        from PySide6.QtWidgets import QLabel

        from gui.radar.radar_page import LIST_DISPLAY_LIMIT

        trouves = self._opportunites(LIST_DISPLAY_LIMIT + 7)
        page.store.list_opportunities = lambda **kwargs: (
            trouves if kwargs.get("platform") == "twitch" else [])
        page._refresh_tab("twitch")

        textes = " ".join(w.text() for w in page.findChildren(QLabel))
        assert f"sur {len(trouves)} trouvés" in textes

    def test_une_liste_entiere_n_annonce_rien(self, page):
        from PySide6.QtWidgets import QLabel

        from gui.radar.radar_page import LIST_DISPLAY_LIMIT

        trouves = self._opportunites(LIST_DISPLAY_LIMIT)
        page.store.list_opportunities = lambda **kwargs: (
            trouves if kwargs.get("platform") == "twitch" else [])
        page._refresh_tab("twitch")

        textes = " ".join(w.text() for w in page.findChildren(QLabel))
        assert "trouvés sur cette période" not in textes
