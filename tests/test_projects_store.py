import json

from projects.store import create_project_folder, list_projects, load_project, write_manifest


def _write_results(folder, clips):
    with open(folder / "results.json", "w", encoding="utf-8") as f:
        json.dump(clips, f)


def test_create_project_folder_is_slugified_and_unique(tmp_path):
    folder1 = create_project_folder("Podcast Entrepreneur !", projects_dir=tmp_path)
    assert folder1.exists()
    assert "podcast-entrepreneur" in folder1.name.lower()

    folder2 = create_project_folder("Podcast Entrepreneur !", projects_dir=tmp_path)
    assert folder1 != folder2  # timestamp differencie deux projets de meme nom


def test_list_projects_reads_manifest_and_counts_clips(tmp_path):
    folder = create_project_folder("Interview Business", projects_dir=tmp_path)
    write_manifest(folder, "Interview Business", "interview.mp4", "local", {"clip_duration": 45})
    _write_results(folder, [
        {"clip": "clip_01.mp4", "language": "fr"},
        {"clip": "clip_02.mp4", "language": "fr"},
    ])

    projects = list_projects(projects_dir=tmp_path)

    assert len(projects) == 1
    assert projects[0].name == "Interview Business"
    assert projects[0].clip_count == 2
    assert projects[0].language == "fr"
    assert projects[0].source_kind == "local"


def test_list_projects_ignores_folders_without_manifest(tmp_path):
    (tmp_path / "not_a_project").mkdir()
    assert list_projects(projects_dir=tmp_path) == []


def test_list_projects_sorted_most_recent_first(tmp_path):
    older = create_project_folder("Ancien", projects_dir=tmp_path)
    write_manifest(older, "Ancien", "a.mp4", "local", {}, )
    # Force une date anterieure pour eviter toute ambiguite liee a la resolution
    # de l'horodatage du nom de dossier (secondes).
    manifest_path = older / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = "2020-01-01T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    newer = create_project_folder("Recent", projects_dir=tmp_path)
    write_manifest(newer, "Recent", "b.mp4", "local", {})

    projects = list_projects(projects_dir=tmp_path)
    assert [p.name for p in projects] == ["Recent", "Ancien"]


def test_load_project_returns_full_clip_data(tmp_path):
    folder = create_project_folder("Stream CS2", projects_dir=tmp_path)
    write_manifest(folder, "Stream CS2", "https://youtube.com/watch?v=abc", "youtube",
                    {"clip_duration": 30}, source_url="https://youtube.com/watch?v=abc")
    _write_results(folder, [{"clip": "clip_01.mp4", "score": 92, "language": "fr"}])

    project = load_project(folder)

    assert project.summary.name == "Stream CS2"
    assert project.summary.source_kind == "youtube"
    assert project.source_url == "https://youtube.com/watch?v=abc"
    assert project.clips == [{"clip": "clip_01.mp4", "score": 92, "language": "fr"}]
    assert project.settings_used == {"clip_duration": 30}
