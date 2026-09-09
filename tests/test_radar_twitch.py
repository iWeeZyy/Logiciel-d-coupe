"""Adaptateur Twitch Helix.

Aucun appel reseau : les reponses de l'API sont fournies a la main, mais tout
le code de parsing, d'authentification et de gestion d'erreur est reellement
execute.
"""
from datetime import datetime, timedelta, timezone

import pytest

from radar.models import KIND_CLIP, KIND_LIVE, KIND_VOD, Creator
from radar.platforms.twitch import (
    TwitchAdapter,
    load_credentials,
    parse_query,
    parse_twitch_duration,
    stream_potential,
)
from utils.errors import TwitchApiError, TwitchConfigError

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class FakeTwitch(TwitchAdapter):
    """Adaptateur reel, transport simule."""

    def __init__(self, responses):
        super().__init__(client_id="id", client_secret="secret")
        self.responses = responses
        self.calls = []
        self._token = "jeton"
        self._token_expires_at = 1e12

    def _get(self, endpoint, params):
        self.calls.append((endpoint, params))
        value = self.responses.get(endpoint)
        if isinstance(value, Exception):
            raise value
        return value or {"data": []}


def _creator():
    return Creator(platform="twitch", platform_id="T1", username="streamera",
                   display_name="Streamer A")


def _iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


# ----------------------------------------------------------- resolution

@pytest.mark.parametrize("query,expected", [
    ("https://www.twitch.tv/streamera", "streamera"),
    ("twitch.tv/streamerb/videos", "streamerb"),
    ("@streamera", "streamera"),
    ("StreamerA", "streamera"),
    ("", ""),
])
def test_the_three_accepted_forms_are_recognised(query, expected):
    assert parse_query(query) == expected


def test_a_channel_is_resolved_into_the_common_creator_shape():
    adapter = FakeTwitch({"users": {"data": [{
        "id": "T1", "login": "streamera", "display_name": "Streamer A",
        "profile_image_url": "avatar.png",
    }]}})

    creator = adapter.resolve_creator("https://www.twitch.tv/streamera")

    assert creator.platform == "twitch" and creator.platform_id == "T1"
    assert creator.key == "twitch:T1"
    assert creator.url == "https://www.twitch.tv/streamera"


def test_the_follower_count_stays_unknown_rather_than_wrong():
    # Il demande un endpoint et un scope que le flux applicatif n'a pas :
    # afficher 0 serait faux, et exiger une autorisation utilisateur ne se
    # justifie pas pour de la simple surveillance.
    adapter = FakeTwitch({"users": {"data": [{"id": "T1", "login": "a", "display_name": "A"}]}})

    assert adapter.resolve_creator("a").follower_count is None


def test_an_unknown_streamer_yields_no_creator():
    assert FakeTwitch({"users": {"data": []}}).resolve_creator("inconnu") is None


# ------------------------------------------------------------------ scan

def test_a_live_stream_is_reported_without_inventing_a_duration():
    # Un direct n'a pas de duree finale : en mettre une laisserait croire a un
    # contenu termine. Ce qu'on connait est son anciennete.
    adapter = FakeTwitch({"streams": {"data": [{
        "id": "S1", "title": "Live du soir", "game_name": "Just Chatting",
        "viewer_count": 18420, "started_at": _iso(2.2),
        "thumbnail_url": "https://x/{width}x{height}.jpg",
    }]}})

    found = adapter.scan(_creator(), _iso(24))
    live = [o for o in found if o.kind == KIND_LIVE]

    assert len(live) == 1
    assert live[0].viewer_count == 18420 and live[0].is_live
    assert live[0].duration_s is None
    assert "{width}" not in live[0].thumbnail_url


def test_clips_keep_the_name_of_whoever_created_them():
    # Le createur d'un clip n'est pas forcement le streamer : l'information
    # compte pour le credit.
    adapter = FakeTwitch({"clips": {"data": [{
        "id": "C1", "title": "Le moment", "url": "https://clips.twitch.tv/C1",
        "created_at": _iso(0.75), "duration": 28.5, "view_count": 8400,
        "creator_name": "UnSpectateur", "video_id": "V9",
    }]}})

    clips = [o for o in adapter.scan(_creator(), _iso(24)) if o.kind == KIND_CLIP]

    assert clips[0].view_count == 8400 and clips[0].duration_s == 28
    assert clips[0].extra["clip_creator"] == "UnSpectateur"


def test_vods_older_than_the_period_are_filtered_out():
    # L'endpoint /videos n'accepte pas de date de debut : le filtrage est a
    # notre charge, sinon un scan "24 h" ramenerait des VOD vieilles de mois.
    adapter = FakeTwitch({"videos": {"data": [
        {"id": "V1", "title": "Recente", "published_at": _iso(3), "duration": "4h32m10s",
         "view_count": 900, "url": "u1", "thumbnail_url": ""},
        {"id": "V2", "title": "Ancienne", "published_at": _iso(300), "duration": "2h",
         "view_count": 9000, "url": "u2", "thumbnail_url": ""},
    ]}})

    adapter.content_kinds = (KIND_VOD,)   # les VOD ne sont plus cherchées par défaut
    vods = [o for o in adapter.scan(_creator(), _iso(24)) if o.kind == KIND_VOD]

    assert [v.content_id for v in vods] == ["V1"]
    assert vods[0].duration_s == 4 * 3600 + 32 * 60 + 10


def test_the_live_radar_asks_for_every_channel_at_once():
    # Une requete par chaine multiplierait les appels sans rien apporter.
    adapter = FakeTwitch({"streams": {"data": [{"user_id": "T1", "viewer_count": 5}]}})
    creators = [Creator(platform="twitch", platform_id=f"T{i}", username=f"s{i}")
                for i in range(30)]

    live = adapter.live_streams(creators)

    assert len(adapter.calls) == 1
    assert "twitch:T1" in live


@pytest.mark.parametrize("value,expected", [
    ("4h32m10s", 16330), ("1h", 3600), ("45m", 2700), ("30s", 30), ("", None), ("abc", None),
])
def test_twitch_durations_are_parsed(value, expected):
    assert parse_twitch_duration(value) == expected


# ------------------------------------------------------- authentification

def test_without_credentials_the_platform_says_it_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr("radar.platforms.twitch.credentials_path", lambda: tmp_path / "absent.txt")

    status = TwitchAdapter().status()

    assert not status.available
    assert "dev.twitch.tv" in status.reason


def test_the_error_message_names_the_exact_file_expected(monkeypatch, tmp_path):
    # Deviner cet emplacement a deja fait perdre du temps avec la cle YouTube.
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    target = tmp_path / "twitch_credentials.txt"
    monkeypatch.setattr("radar.platforms.twitch.credentials_path", lambda: target)

    with pytest.raises(TwitchConfigError) as excinfo:
        load_credentials()

    assert str(target) in str(excinfo.value)


def test_credentials_are_read_from_a_file_with_a_bom(monkeypatch, tmp_path):
    # PowerShell 5.1 ecrit un BOM avec -Encoding utf8, et ce caractere invisible
    # n'est pas retire par strip(). Piege deja rencontre en usage reel.
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    path = tmp_path / "twitch_credentials.txt"
    path.write_text("﻿mon_id\nmon_secret\n", encoding="utf-8")
    monkeypatch.setattr("radar.platforms.twitch.credentials_path", lambda: path)

    assert load_credentials() == ("mon_id", "mon_secret")


def test_the_environment_wins_over_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "env_id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "env_secret")

    assert load_credentials() == ("env_id", "env_secret")


def test_comments_in_the_credentials_file_are_ignored(monkeypatch, tmp_path):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    path = tmp_path / "twitch_credentials.txt"
    path.write_text("# Client ID puis Client Secret\nmon_id\nmon_secret\n", encoding="utf-8")
    monkeypatch.setattr("radar.platforms.twitch.credentials_path", lambda: path)

    assert load_credentials() == ("mon_id", "mon_secret")


def test_a_rate_limit_is_reported_readably():
    adapter = FakeTwitch({"clips": TwitchApiError("Limite de requêtes Twitch atteinte.")})

    with pytest.raises(TwitchApiError) as excinfo:
        adapter.scan(_creator(), _iso(24))

    assert "Limite" in str(excinfo.value)


# ------------------------------------------------- Stream Potential Score

def test_a_stream_above_its_usual_audience_scores_higher():
    above, ratio = stream_potential(12400, 9000)
    below, _ = stream_potential(4000, 9000)

    assert above > below
    assert ratio == pytest.approx(1.38, abs=0.01)


def test_without_history_no_percentage_is_invented():
    score, ratio = stream_potential(12400, None)

    assert score > 0
    assert ratio is None, "sans historique on ne peut pas dire si c'est beaucoup pour lui"


def test_an_offline_streamer_has_no_stream_potential():
    assert stream_potential(None, 9000) == (0.0, None)


# ------------------------------------------------- types de contenu

def test_vods_are_not_searched_by_default():
    # Une rediffusion de quatre heures n'a rien qui designe le moment
    # interessant, et Twitch n'offre aucun moyen officiel de la recuperer.
    from radar.platforms.twitch import DEFAULT_CONTENT_KINDS

    assert DEFAULT_CONTENT_KINDS == (KIND_CLIP, KIND_LIVE)


def test_a_disabled_kind_triggers_no_request_at_all():
    # Filtrer apres coup consommerait une requete par createur et par scan
    # pour un resultat jete.
    adapter = FakeTwitch({
        "clips": {"data": [{"id": "C1", "title": "T", "created_at": _iso(1),
                            "duration": 20, "view_count": 100, "url": "u"}]},
        "streams": {"data": []},
        "videos": {"data": [{"id": "V1", "title": "VOD", "published_at": _iso(1),
                             "duration": "2h", "view_count": 50, "url": "u",
                             "thumbnail_url": ""}]},
    })

    adapter.scan(_creator(), _iso(24))
    endpoints = [endpoint for endpoint, _ in adapter.calls]

    assert "videos" not in endpoints, "aucune requête VOD quand le type est désactivé"
    assert "clips" in endpoints and "streams" in endpoints


def test_asking_only_for_clips_returns_only_clips():
    adapter = FakeTwitch({
        "clips": {"data": [{"id": "C1", "title": "T", "created_at": _iso(1),
                            "duration": 20, "view_count": 100, "url": "u"}]},
        "streams": {"data": [{"id": "S1", "title": "Live", "viewer_count": 900,
                              "started_at": _iso(2), "thumbnail_url": ""}]},
    })
    adapter.content_kinds = (KIND_CLIP,)

    found = adapter.scan(_creator(), _iso(24))

    assert [o.kind for o in found] == [KIND_CLIP]
    assert "streams" not in [endpoint for endpoint, _ in adapter.calls]


def test_vods_can_still_be_enabled_for_whoever_wants_them():
    adapter = FakeTwitch({"videos": {"data": [
        {"id": "V1", "title": "VOD", "published_at": _iso(1), "duration": "2h",
         "view_count": 50, "url": "u", "thumbnail_url": ""}]}})
    adapter.content_kinds = (KIND_VOD,)

    assert [o.kind for o in adapter.scan(_creator(), _iso(24))] == [KIND_VOD]


def test_the_configuration_file_drives_the_kinds():
    """Le Radar livré ne cherche que des clips.

    Les directs ont été retirés du réglage par défaut après usage réel : un
    direct très suivi score très haut et occupait le haut de la liste à la
    place des clips, qui sont le contenu recherché. Ils restent activables en
    ajoutant "live" à cette liste.
    """
    from core.config_loader import load_radar_config

    kinds = (load_radar_config().get("twitch", {}) or {}).get("content_kinds")

    assert kinds == ["clip"], "config/radar.json est la source de ce réglage"


def test_a_broken_configuration_file_never_blocks_the_radar(monkeypatch, tmp_path):
    from core import config_loader

    broken = tmp_path / "radar.json"
    broken.write_text("{ ceci n'est pas du json", encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_DIR", tmp_path)

    assert config_loader.load_radar_config() == {}


# ------------------------------------------------------------ nom du jeu

def _clip_payload(clip_id, game_id):
    return {"id": clip_id, "title": f"Clip {clip_id}", "url": f"https://clips.twitch.tv/{clip_id}",
            "created_at": _iso(1), "duration": 30.0, "view_count": 10, "game_id": game_id}


def test_the_game_is_shown_by_its_name_and_not_by_its_number():
    # /clips ne renvoie qu'un identifiant numerique. L'afficher tel quel montre
    # un nombre qui ne dit rien -- et en faisait meme un hashtag.
    adapter = FakeTwitch({
        "clips": {"data": [_clip_payload("C1", "132735846")]},
        "games": {"data": [{"id": "132735846", "name": "Just Chatting"}]},
    })

    clips = [o for o in adapter.scan(_creator(), _iso(24)) if o.kind == KIND_CLIP]

    assert clips[0].category == "Just Chatting"


def test_the_names_of_every_game_are_asked_in_one_request():
    adapter = FakeTwitch({
        "clips": {"data": [_clip_payload("C1", "111"), _clip_payload("C2", "222"),
                           _clip_payload("C3", "111")]},
        "games": {"data": [{"id": "111", "name": "Jeu A"}, {"id": "222", "name": "Jeu B"}]},
    })

    adapter.scan(_creator(), _iso(24))

    games_calls = [c for c in adapter.calls if c[0] == "games"]
    assert len(games_calls) == 1
    assert sorted(games_calls[0][1]["id"]) == ["111", "222"]


def test_a_game_already_resolved_is_not_asked_again():
    adapter = FakeTwitch({
        "clips": {"data": [_clip_payload("C1", "111")]},
        "games": {"data": [{"id": "111", "name": "Jeu A"}]},
    })

    adapter.scan(_creator(), _iso(24))
    adapter.scan(_creator(), _iso(24))

    assert len([c for c in adapter.calls if c[0] == "games"]) == 1


def test_a_game_name_that_cannot_be_resolved_leaves_no_category():
    # Mieux vaut un clip sans categorie qu'un nombre presente comme une
    # categorie -- et l'echec ne doit pas interrompre le scan.
    adapter = FakeTwitch({
        "clips": {"data": [_clip_payload("C1", "999")]},
        "games": TwitchApiError("API indisponible"),
    })

    clips = [o for o in adapter.scan(_creator(), _iso(24)) if o.kind == KIND_CLIP]

    assert clips and clips[0].category == ""


def test_an_unknown_game_id_is_not_asked_again_either():
    adapter = FakeTwitch({
        "clips": {"data": [_clip_payload("C1", "999")]},
        "games": {"data": []},                     # Twitch ne connait pas cet id
    })

    adapter.scan(_creator(), _iso(24))
    adapter.scan(_creator(), _iso(24))

    assert len([c for c in adapter.calls if c[0] == "games"]) == 1


def test_a_clip_without_a_game_never_triggers_a_lookup():
    adapter = FakeTwitch({"clips": {"data": [_clip_payload("C1", "")]}})

    adapter.scan(_creator(), _iso(24))

    assert not [c for c in adapter.calls if c[0] == "games"]
