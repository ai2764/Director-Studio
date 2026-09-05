import json

import pytest

from app.core.projects.layouts import (
    LayoutReference,
    LayoutSourceRef,
    mirror_legacy_layout_fields,
)
from app.core.projects.models import (
    ProjectMode,
    PromptSections,
    RefRole,
    Shot,
    ShotStatus,
)
from app.core.projects.store import (
    create_project,
    list_projects,
    list_shots,
    load_project,
    load_shot,
    save_project,
    save_shot,
)


def make_shot(**updates) -> Shot:
    """Create a valid shot with task-specific fields overridden."""
    payload = {
        "id": "sht_layout_test",
        "project_id": "prj_layout_test",
        "scene_id": "sc_layout_test",
        "title": "Layout test",
        "script_beat": "A layout test beat",
        "duration_s": 8.0,
    }
    payload.update(updates)
    return Shot(**payload)


def test_create_and_load_project(tmp_projects_dir):
    project = create_project("Demo", "INT. ROOM - DAY\nHello.")
    assert project.id.startswith("prj_")
    assert project.name == "Demo"
    assert project.script_text.startswith("INT.")
    assert project.shot_ids == []

    loaded = load_project(project.id)
    assert loaded is not None
    assert loaded.id == project.id
    assert loaded.name == "Demo"

    listed = list_projects()
    assert any(p.id == project.id for p in listed)


def test_old_project_defaults_to_director(tmp_projects_dir):
    project = create_project("Legacy", "INT. ROOM")
    path = tmp_projects_dir / project.id / "project.json"
    raw = json.loads(path.read_text())
    raw.pop("mode", None)
    path.write_text(json.dumps(raw))
    assert load_project(project.id).mode == ProjectMode.director


def test_save_and_list_shots(tmp_projects_dir):
    project = create_project("With Shots", "script")
    shot = Shot(
        id="sht_aabbccddeeff",
        project_id=project.id,
        scene_id="sc01",
        title="Open",
        script_beat="walk in",
        duration_s=8.0,
        status=ShotStatus.draft,
        prompt_sections=PromptSections(),
    )
    save_shot(shot)

    loaded = load_shot(project.id, shot.id)
    assert loaded is not None
    assert loaded.title == "Open"
    assert loaded.project_id == project.id

    shots = list_shots(project.id)
    assert len(shots) == 1
    assert shots[0].id == shot.id

    # Project may track shot ids after save
    project.shot_ids = [shot.id]
    save_project(project)
    reloaded = load_project(project.id)
    assert reloaded is not None
    assert shot.id in reloaded.shot_ids


def test_legacy_shot_without_camera_brief_loads_with_empty_compatible_defaults(
    tmp_projects_dir,
):
    project = create_project("Legacy camera", "script")
    shot_path = tmp_projects_dir / project.id / "shots" / "sht_legacy_camera.json"
    shot_path.parent.mkdir(parents=True, exist_ok=True)
    shot_path.write_text(
        json.dumps(
            {
                "id": "sht_legacy_camera",
                "project_id": project.id,
                "scene_id": "sc01",
                "title": "Old shot",
                "script_beat": "An old persisted action.",
                "duration_s": 6.0,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_shot(project.id, "sht_legacy_camera")

    assert loaded is not None
    assert loaded.shot_type == ""
    assert loaded.camera_angle == ""
    assert loaded.camera_motion == ""
    assert loaded.composition == ""


def test_load_missing_returns_none(tmp_projects_dir):
    assert load_project("prj_missing0000") is None
    assert load_shot("prj_missing0000", "sht_missing0000") is None


def test_legacy_shot_exposes_one_layout_reference():
    shot = Shot(
        id="s1",
        project_id="p1",
        scene_id="sc1",
        title="Entry",
        script_beat="A enters",
        duration_s=8,
        layout_asset_id="lay_old",
        layout_review_status="approved",
        ref_frame_job_id="job_old",
    )

    assert len(shot.layout_refs) == 1
    assert shot.layout_refs[0].asset_id == "lay_old"
    assert shot.layout_refs[0].review_status == "usable"
    assert shot.layout_refs[0].selected_for_h3 is True


def test_layout_reference_rejects_more_than_three_sources():
    refs = [
        LayoutSourceRef(role=RefRole.actor, asset_id=f"a{i}")
        for i in range(4)
    ]

    with pytest.raises(ValueError, match="at most 3 source images"):
        LayoutReference(id="lr1", source_refs=refs)


def test_multiple_layout_refs_survive_store_round_trip(tmp_projects_dir):
    project = create_project("Multi-layout", "script")
    shot = make_shot(
        project_id=project.id,
        layout_refs=[
            LayoutReference(id="lr_before", purpose="before entry"),
            LayoutReference(id="lr_after", purpose="after entry"),
        ],
    )

    save_shot(shot)
    loaded = load_shot(shot.project_id, shot.id)

    assert loaded is not None
    assert [item.id for item in loaded.layout_refs] == ["lr_before", "lr_after"]


def test_mirror_legacy_fields_does_not_prefer_repair_needed_layout():
    shot = make_shot(
        layout_refs=[
            LayoutReference(
                id="lr_first",
                asset_id="lay_first",
                job_id="job_first",
                review_status="pending_review",
            ),
            LayoutReference(
                id="lr_repair",
                asset_id="lay_repair",
                job_id="job_repair",
                review_status="usable_with_repair",
                selected_for_h3=True,
            ),
        ]
    )

    mirrored = mirror_legacy_layout_fields(shot)

    assert mirrored.layout_asset_id == "lay_first"
    assert mirrored.ref_frame_job_id == "job_first"
    assert mirrored.layout_review_status == "pending_review"


def test_mirror_legacy_fields_can_project_one_compatibility_primary_layout():
    shot = make_shot(
        layout_refs=[
            LayoutReference(
                id="lr_approved",
                asset_id="lay_approved",
                job_id="job_approved",
                review_status="usable",
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lr_replacement",
                job_id="job_replacement",
                review_status=None,
            ),
        ]
    )

    mirrored = mirror_legacy_layout_fields(
        shot,
        compatibility_primary_layout_id="lr_replacement",
    )

    assert mirrored.layout_asset_id is None
    assert mirrored.ref_frame_job_id == "job_replacement"
    assert mirrored.layout_review_status is None
