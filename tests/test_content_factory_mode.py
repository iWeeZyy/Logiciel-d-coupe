"""Mode Content Factory : nombre de clips automatique, bilan de production,
score moyen d'un projet.

Tests purs : ni video, ni interface.
"""
from content_factory import planning
from content_factory.report import ProductionReport, build_report
from core.models import ClipResult
from projects.store import average_score


# ------------------------------------------------- nombre de clips auto

def test_a_longer_video_gets_more_clips():
    assert planning.suggested_clip_count(3600) > planning.suggested_clip_count(1800)


def test_the_count_is_bounded_at_both_ends():
    # Une video de dix heures ne doit pas proposer cent cinquante clips, et une
    # video de deux minutes doit quand meme en proposer quelques-uns.
    assert planning.suggested_clip_count(36000) == planning.MAX_CLIPS
    assert planning.suggested_clip_count(60) == planning.MIN_CLIPS
    assert planning.suggested_clip_count(0) == planning.MIN_CLIPS


def test_the_proposed_count_is_explained_not_dropped_from_the_sky():
    text = planning.describe(1800)

    assert str(planning.suggested_clip_count(1800)) in text
    assert "30 min" in text


# ------------------------------------------------------ bilan de fin

def _clip(index, thumbnails=(), subtitles=(), metadata=None):
    return ClipResult(
        index=index, file_name=f"clips/clip_{index:02d}.mp4", start=0.0, end=30.0,
        duration=30.0, score=80.0, scores={"viral": 80.0}, transcript="", language="fr",
        thumbnails=list(thumbnails), subtitles=list(subtitles), metadata=metadata or {},
    )


def test_the_report_counts_what_was_really_produced():
    clips = [
        _clip(1, thumbnails=["a.jpg", "b.jpg"], subtitles=["a.srt"],
              metadata={"titles": [{"text": "T"}], "description": "D"}),
        _clip(2, thumbnails=["c.jpg"], metadata={"titles": [{"text": "T"}]}),
    ]

    report = build_report(clips, elapsed_s=1080)

    assert report.clips == 2
    assert report.thumbnails == 3
    assert report.titles == 2
    assert report.descriptions == 1, "un seul clip a une description"
    assert report.subtitle_files == 1


def test_a_disabled_module_is_never_reported_as_produced():
    # Annoncer "10 titres generes" alors que le module etait eteint serait un
    # mensonge d'interface.
    report = build_report([_clip(1), _clip(2)])

    assert report.clips == 2
    assert report.titles == 0 and report.thumbnails == 0
    assert all("titre" not in line for line in report.lines())
    assert all("miniature" not in line for line in report.lines())


def test_empty_categories_are_omitted_from_the_summary():
    lines = ProductionReport(clips=3).lines()

    assert lines == ["3 clip(s) généré(s)"]


def test_the_total_time_is_shown_in_a_readable_unit():
    assert "18 min" in " ".join(ProductionReport(clips=1, elapsed_s=1080).lines())
    assert "1 h" in " ".join(ProductionReport(clips=1, elapsed_s=3700).lines())
    assert all("Temps" not in line for line in ProductionReport(clips=1).lines())


# --------------------------------------------- score moyen d'un projet

def test_the_project_average_uses_the_viral_score_already_stored():
    results = [{"score": 90.0}, {"score": 80.0}, {"score": 70.0}]

    assert average_score(results) == 80.0


def test_an_empty_project_has_no_average_rather_than_zero():
    assert average_score([]) is None
    assert average_score([{"clip": "x.mp4"}]) is None, "aucun score exploitable"


def test_a_malformed_score_is_ignored_instead_of_breaking_the_list():
    assert average_score([{"score": 90.0}, {"score": None}, {"score": "haut"}]) == 90.0
