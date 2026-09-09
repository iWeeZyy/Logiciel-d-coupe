"""Pont Radar -> Content Factory.

Ce que ces tests protegent avant tout : le Radar ne doit jamais laisser croire
qu'un contenu trouve est reutilisable, ni contourner la barriere de droits deja
en place dans youtube/downloader.py.
"""
import pytest

from radar.bridge import RIGHTS_NOTICE, SourceNotAvailable, build_request
from radar.models import KIND_CLIP, KIND_LIVE, KIND_SHORT, KIND_VOD, Opportunity


def _youtube(title="Un titre", url="https://youtu.be/v1"):
    return Opportunity(platform="youtube", content_id="v1", kind=KIND_SHORT,
                       creator_key="youtube:UC1", title=title, url=url)


def _twitch(kind=KIND_CLIP):
    return Opportunity(platform="twitch", content_id="c1", kind=kind,
                       creator_key="twitch:T1", title="Un clip",
                       url="https://clips.twitch.tv/c1")


def test_a_youtube_content_is_prepared_as_a_youtube_source():
    request = build_request([_youtube()])

    assert request.source_kind == "youtube"
    assert request.source == "https://youtu.be/v1"
    assert not request.needs_local_file


def test_a_twitch_clip_is_prepared_as_a_twitch_source():
    """Twitch propose lui-meme le telechargement d'un clip (menu Partager).

    Ce test verifiait l'inverse : le pont refusait les clips, au motif errone
    qu'aucun telechargement officiel n'existait. La premisse etait fausse, pas
    le code -- elle est corrigee ici.
    """
    request = build_request([_twitch()])

    assert request.source_kind == "twitch"
    assert request.source == "https://clips.twitch.tv/c1"
    assert not request.needs_local_file


def test_a_vod_or_a_live_is_still_refused_with_an_explanation():
    """Twitch ne propose de telechargement que pour les clips."""
    for kind in (KIND_VOD, KIND_LIVE):
        with pytest.raises(SourceNotAvailable) as excinfo:
            build_request([_twitch(kind=kind)])

        message = str(excinfo.value)
        assert "que pour les clips" in message
        assert "disposez légalement du fichier" in message


def test_the_rights_notice_never_says_a_download_grants_rights():
    from radar.bridge import RIGHTS_NOTICE

    assert "pas une licence" in RIGHTS_NOTICE
    assert "libre de droit" not in RIGHTS_NOTICE.lower()


def test_a_local_file_the_user_owns_is_accepted_for_any_platform(tmp_path):
    video = tmp_path / "mon_enregistrement.mp4"
    video.write_bytes(b"\x00")
    opportunity = _twitch(kind=KIND_LIVE)

    request = build_request([opportunity], {opportunity.key: str(video)})

    assert request.needs_local_file and request.source == str(video)


def test_a_missing_local_file_is_reported_rather_than_passed_along(tmp_path):
    opportunity = _twitch()

    with pytest.raises(SourceNotAvailable) as excinfo:
        build_request([opportunity], {opportunity.key: str(tmp_path / "absent.mp4")})

    assert "introuvable" in str(excinfo.value)


def test_several_contents_at_once_are_refused_with_the_reason():
    # Le pipeline decoupe UNE video longue : melanger deux sources dans un
    # projet melangerait des transcriptions sans rapport.
    with pytest.raises(SourceNotAvailable) as excinfo:
        build_request([_youtube(), _twitch()])

    assert "une source à la fois" in str(excinfo.value)


def test_an_empty_selection_is_refused():
    with pytest.raises(SourceNotAvailable):
        build_request([])


def test_the_project_name_survives_a_title_full_of_punctuation():
    request = build_request([_youtube(title='Un "titre" : avec / des \\ caractères ?')])

    assert "/" not in request.project_name and ":" not in request.project_name
    assert request.project_name.strip() == request.project_name
    assert request.project_name


def test_an_untitled_content_still_gets_a_usable_project_name():
    assert build_request([_youtube(title="")]).project_name


def test_the_rights_notice_never_claims_a_content_is_free_to_use():
    lowered = RIGHTS_NOTICE.lower()

    assert "ne signifie pas disposer des droits" in lowered
    assert "libre de droits" not in lowered
