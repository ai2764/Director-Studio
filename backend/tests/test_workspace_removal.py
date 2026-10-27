from types import SimpleNamespace

import pytest

from app.core.projects import shot_removal as removal
from app.core.projects.models import Shot, ShotVideoContext
from app.core.projects.store import create_project, save_project, save_shot, load_shot, load_project, list_shots, project_dir


@pytest.fixture
def board():
    project = create_project("Manual removal", "")
    shots = [Shot(id=f"s{i}", project_id=project.id, scene_id="room", title=f"Shot {i}",
        script_beat=f"Action {i}", duration_s=5, refs=[{"role": "actor", "asset_id": "library_actor", "picture_index": 1}],
        meta={"user_notes": "Keep this text"}) for i in range(3)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [s.id for s in shots]}))
    return project, shots


def test_removal_and_undo_preserve_content_ids_and_output_files(board):
    project, shots = board
    artifact = project_dir(project.id) / "jobs" / "finished" / "video.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"unchanged test artifact")
    before = {s.id: s.model_dump() for s in shots}
    result = removal.remove_shot(project.id, shots[1].id, shots[1])
    assert [s.id for s in result["shots"]] == ["s0", "s2"]
    assert load_shot(project.id, "s1") is None
    assert artifact.read_bytes() == b"unchanged test artifact"
    restored = removal.restore_shot(project.id, result["archive_id"])
    assert [s.id for s in restored["shots"]] == ["s0", "s1", "s2"]
    assert {s.id: s.model_dump() for s in restored["shots"]} == before


@pytest.mark.parametrize("explicit", [True, False])
def test_source_deletion_is_blocked_for_explicit_or_implicit_continuation(board, explicit):
    project, shots = board
    shots[1].video_context = ShotVideoContext(mode="previous_shot", source_shot_id="s0" if explicit else None)
    save_shot(shots[1])
    with pytest.raises(ValueError, match="continu"):
        removal.remove_shot(project.id, "s0")
    assert load_project(project.id).shot_ids == ["s0", "s1", "s2"]
    assert load_shot(project.id, "s0") == shots[0]


def test_stale_delete_and_running_generation_are_rejected(board, monkeypatch):
    project, shots = board
    changed = shots[0].model_copy(update={"title": "New edit"})
    save_shot(changed)
    with pytest.raises(ValueError, match="changed"):
        removal.remove_shot(project.id, "s0", shots[0])
    changed.h3_job_id = "job_running"
    save_shot(changed)
    monkeypatch.setattr(removal, "load_job", lambda _id: SimpleNamespace(status="running"))
    with pytest.raises(ValueError, match="generating"):
        removal.remove_shot(project.id, "s0")
    assert load_shot(project.id, "s0") == changed


def test_undo_uses_saved_neighbors_and_never_overwrites_later_edits(board):
    project, shots = board
    result = removal.remove_shot(project.id, "s1")
    extra = shots[0].model_copy(update={"id": "extra"})
    save_shot(extra)
    current = load_project(project.id)
    save_project(current.model_copy(update={"shot_ids": [*current.shot_ids, "extra"]}))
    removal.restore_shot(project.id, result["archive_id"])
    assert load_project(project.id).shot_ids == ["s0", "s1", "s2", "extra"]
    changed = load_shot(project.id, "s1").model_copy(update={"title": "Edited after restore"})
    save_shot(changed)
    assert removal.restore_shot(project.id, result["archive_id"])["shot"] == changed


def test_deleting_last_shot_does_not_resurrect_legacy_orphans():
    project = create_project("Last shot", "")
    shot = Shot(id="last", project_id=project.id, scene_id="room", title="Last", script_beat="Action", duration_s=5)
    orphan = shot.model_copy(update={"id": "legacy_orphan"})
    save_shot(shot); save_shot(orphan)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    result = removal.remove_shot(project.id, shot.id)
    assert result["shots"] == [] and list_shots(project.id) == []
    assert load_shot(project.id, orphan.id) == orphan
    assert [s.id for s in removal.restore_shot(project.id, result["archive_id"])["shots"]] == [shot.id]


def test_restore_rejects_path_traversal_and_missing_dependency(board):
    project, shots = board
    with pytest.raises(ValueError, match="Invalid"):
        removal.restore_shot(project.id, "../../another-project")
    shots[1].video_context = ShotVideoContext(mode="previous_shot", source_shot_id="s0")
    save_shot(shots[1])
    target = removal.remove_shot(project.id, "s1")
    removal.remove_shot(project.id, "s0")
    with pytest.raises(ValueError):
        removal.restore_shot(project.id, target["archive_id"])
    assert load_project(project.id).shot_ids == ["s2"]


def test_restoring_unrelated_shot_preserves_legacy_implicit_source(board):
    project, shots = board
    shots[2].video_context = ShotVideoContext(mode="previous_shot")
    save_shot(shots[2])
    result = removal.remove_shot(project.id, "s0")
    removal.restore_shot(project.id, result["archive_id"])
    assert load_shot(project.id, "s2").video_context.source_shot_id is None
    assert load_project(project.id).shot_ids == ["s0", "s1", "s2"]


def test_old_undo_delivery_cannot_undo_a_later_deletion(board):
    project, _shots = board
    first = removal.remove_shot(project.id, "s1")
    removal.restore_shot(project.id, first["archive_id"])
    second = removal.remove_shot(project.id, "s1")
    with pytest.raises(ValueError, match="latest"):
        removal.restore_shot(project.id, first["archive_id"])
    assert load_shot(project.id, "s1") is None
    removal.restore_shot(project.id, second["archive_id"])
    assert load_shot(project.id, "s1") is not None
