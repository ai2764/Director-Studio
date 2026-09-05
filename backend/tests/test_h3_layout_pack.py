"""H3 submit packs refs in picture_index order; layout is not forced to ref_0."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.core.projects.models import (
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
    ShotStatus,
)
from app.core.projects.layouts import (
    ClipTailFrameOrigin,
    LayoutReference,
    LayoutReviewStatus,
)
from app.core.schemas import LibraryAsset


def _png(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Noisy enough to exceed resolve_asset_image 2KB placeholder filter
    im = Image.new("RGB", (256, 256), color)
    pixels = [
        ((x * 3 + y * 5 + color[0]) % 256, color[1], color[2])
        for y in range(256)
        for x in range(256)
    ]
    im.putdata(pixels)
    im.save(path)


def _asset(kind: str, aid: str, name: str, files: dict[str, str], lib: Path) -> None:
    adir = lib / kind / aid
    adir.mkdir(parents=True, exist_ok=True)
    for k, fn in files.items():
        _png(adir / fn, (10, 20, 30) if kind == "layouts" else (100, 80, 60))
    asset = LibraryAsset(
        id=aid,
        kind=kind,
        name=name,
        notes="",
        pipeline_id="external",
        job_id="",
        created_at="t",
        files=files,
        meta={},
    )
    (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")


def test_collect_h3_images_follows_picture_index(tmp_path, monkeypatch):
    from app.config import settings
    from app.api import projects as projects_api

    lib = tmp_path / "library"
    monkeypatch.setattr(settings, "library_root", lib)
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")

    _asset("layouts", "lay_1", "layout", {"layout": "layout.png"}, lib)
    _asset("actors", "act_1", "girl", {"master": "master.png"}, lib)
    _asset("scenes", "scn_1", "hall", {"master": "master.png"}, lib)

    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="b",
        duration_s=5.0,
        status=ShotStatus.approved,
        layout_asset_id="lay_1",
        layout_review_status="approved",
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1, file_key="master"),
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_1",
                picture_index=2,
                file_key="layout",
            ),
            ShotRef(role=RefRole.scene, asset_id="scn_1", picture_index=3, file_key="master"),
        ],
        prompt_sections=PromptSections(
            subject_definitions="s",
            summary="s",
            retention_analysis="r",
            detailed_description="d",
            overall_soundscape="o",
            non_diegetic_music="m",
        ),
    )

    images = projects_api._collect_h3_images(shot)
    assert list(images.keys()) == ["ref_0", "ref_1", "ref_2"]
    assert images["ref_0"][0] == "master.png"
    assert images["ref_1"][0] == "layout.png"
    assert images["ref_2"][0] == "master.png"
    assert len(images["ref_1"][1]) > 2048


def test_collect_h3_images_keeps_same_asset_with_different_file_keys(
    tmp_path, monkeypatch
):
    """Two angles from one scene asset are two intentional H3 Pictures."""
    from app.config import settings
    from app.api import projects as projects_api

    lib = tmp_path / "library"
    monkeypatch.setattr(settings, "library_root", lib)
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")

    _asset(
        "scenes",
        "scn_1",
        "cafe",
        {"left_view": "left.png", "right_view": "right.png"},
        lib,
    )
    shot = Shot(
        id="sht_angles",
        project_id="prj_1",
        scene_id="sc01",
        title="angles",
        script_beat="reverse angle",
        duration_s=5.0,
        refs=[
            ShotRef(
                role=RefRole.scene,
                asset_id="scn_1",
                picture_index=1,
                file_key="left_view",
            ),
            ShotRef(
                role=RefRole.scene,
                asset_id="scn_1",
                picture_index=2,
                file_key="right_view",
            ),
        ],
        prompt_sections=PromptSections(
            subject_definitions="s",
            summary="s",
            retention_analysis="r",
            detailed_description="d",
            overall_soundscape="o",
            non_diegetic_music="m",
        ),
    )

    images = projects_api._collect_h3_images(shot)
    assert [value[0] for value in images.values()] == ["left.png", "right.png"]


def test_ensure_layout_ref_appends_without_shifting():
    from app.api.projects import _ensure_shot_layout_ref

    shot = Shot(
        id="sht_2",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="b",
        duration_s=5.0,
        status=ShotStatus.approved,
        layout_asset_id="lay_9",
        layout_review_status="approved",
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_1", picture_index=2),
        ],
        prompt_sections=PromptSections(
            subject_definitions="s",
            summary="s",
            retention_analysis="r",
            detailed_description="d",
            overall_soundscape="o",
            non_diegetic_music="m",
        ),
    )
    fixed = _ensure_shot_layout_ref(shot)
    by_role = {r.role: r for r in fixed.refs}
    assert by_role[RefRole.actor].picture_index == 1
    assert by_role[RefRole.scene].picture_index == 2
    assert by_role[RefRole.layout_ref_frame].picture_index == 3
    assert by_role[RefRole.layout_ref_frame].asset_id == "lay_9"
    assert by_role[RefRole.layout_ref_frame].file_key == "layout"


def _multi_layout_shot(*, base_ref_count: int = 4) -> Shot:
    return Shot(
        id="sht_multi_layout",
        project_id="prj_1",
        scene_id="sc01",
        title="Two states",
        script_beat="Chen crosses the glass doorway.",
        duration_s=6.0,
        refs=[
            ShotRef(
                role=RefRole.other,
                asset_id=f"base_{index}",
                picture_index=index,
            )
            for index in range(1, base_ref_count + 1)
        ],
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="before entry",
                state_description="doorway empty",
                time_hint="before Chen enters",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lr_after",
                asset_id="lay_after",
                purpose="post-entry blocking",
                state_description="Chen outside glass",
                time_hint="after Chen enters",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
        ],
    )


def test_current_layout_receives_real_picture_index():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot().model_copy(
        update={
            "layout_asset_id": "lay_after",
            "layout_refs": [
                _multi_layout_shot().layout_refs[0].model_copy(
                    update={"selected_for_h3": False}
                ),
                _multi_layout_shot().layout_refs[1],
            ],
        }
    )
    packed = sync_selected_layout_refs(shot)

    layouts = [
        ref for ref in packed.refs if ref.role == RefRole.layout_ref_frame
    ]
    assert [(ref.asset_id, ref.picture_index) for ref in layouts] == [
        ("lay_after", 5),
    ]
    assert [ref.picture_index for ref in packed.refs] == list(range(1, 6))


def test_existing_picture_for_current_layout_is_preserved():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=2)
    first = shot.layout_refs[0].model_copy(update={"selected_for_h3": False})
    shot = shot.model_copy(
        update={
            "layout_asset_id": "lay_after",
            "layout_refs": [first, shot.layout_refs[1]],
            "refs": [
                *shot.refs,
                ShotRef(
                    role=RefRole.layout_ref_frame,
                    asset_id="lay_after",
                    picture_index=3,
                    file_key="layout",
                ),
            ]
        }
    )

    packed = sync_selected_layout_refs(shot)

    assert [
        ref.asset_id
        for ref in packed.refs
        if ref.role == RefRole.layout_ref_frame
    ] == ["lay_after"]


def test_h3_pack_omits_rejected_layout():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot()
    rejected = shot.layout_refs[0].model_copy(
        update={"review_status": LayoutReviewStatus.reject}
    )
    shot = shot.model_copy(update={"layout_refs": [rejected, shot.layout_refs[1]]})

    packed = sync_selected_layout_refs(shot)

    assert [ref.asset_id for ref in packed.refs[-1:]] == ["lay_after"]


def test_usable_with_repair_layout_is_used_without_a_selection_gate():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot()
    repaired = shot.layout_refs[0].model_copy(
        update={"review_status": LayoutReviewStatus.usable_with_repair}
    )
    shot = shot.model_copy(update={"layout_refs": [repaired, shot.layout_refs[1]]})

    packed = sync_selected_layout_refs(shot)
    assert packed.refs[-1].asset_id == "lay_after"


def test_explicit_current_marker_wins_when_legacy_primary_is_missing():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=1)
    unselected = shot.layout_refs[1].model_copy(
        update={"selected_for_h3": False}
    )
    shot = shot.model_copy(
        update={
            "layout_refs": [shot.layout_refs[0], unselected],
            "refs": [
                *shot.refs,
                ShotRef(
                    role=RefRole.layout_ref_frame,
                    asset_id="lay_after",
                    picture_index=2,
                ),
            ],
        }
    )

    packed = sync_selected_layout_refs(shot)

    assert [ref.asset_id for ref in packed.refs] == ["base_1", "lay_before"]


def test_h3_pack_uses_only_the_shot_current_layout():
    """Generating alternatives must not submit conflicting compositions together."""
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=1).model_copy(
        update={
            "layout_asset_id": "lay_after",
            "layout_refs": [
                _multi_layout_shot().layout_refs[0].model_copy(
                    update={"selected_for_h3": False}
                ),
                _multi_layout_shot().layout_refs[1],
            ],
        }
    )

    packed = sync_selected_layout_refs(shot)

    assert [(ref.asset_id, ref.picture_index) for ref in packed.refs] == [
        ("base_1", 1),
        ("lay_after", 2),
    ]


def test_h3_pack_keeps_multiple_explicitly_active_layouts():
    """An appended compatible state must travel beside the primary Layout."""
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=1).model_copy(
        update={"layout_asset_id": "lay_after"}
    )

    packed = sync_selected_layout_refs(shot)

    assert [(ref.asset_id, ref.picture_index) for ref in packed.refs] == [
        ("base_1", 1),
        ("lay_before", 2),
        ("lay_after", 3),
    ]


def test_h3_pack_does_not_restore_history_when_there_is_no_current_layout():
    """Deleting the current Layout must not silently reactivate an old version."""
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=1).model_copy(
        update={
            "layout_asset_id": None,
            "layout_refs": [
                layout.model_copy(update={"selected_for_h3": False})
                for layout in _multi_layout_shot().layout_refs
            ],
        }
    )

    packed = sync_selected_layout_refs(shot)

    assert [(ref.asset_id, ref.picture_index) for ref in packed.refs] == [
        ("base_1", 1)
    ]


def test_selecting_a_historical_layout_adds_it_to_the_active_layout_set():
    """The compatibility selection API may explicitly activate two Layouts."""
    from app.core.projects.layouts import sync_selected_layout_refs
    from app.core.projects.transitions import select_layout_reference

    shot = _multi_layout_shot(base_ref_count=1).model_copy(
        update={"layout_asset_id": "lay_before"}
    )

    switched = select_layout_reference(shot, "lr_after", True)
    packed = sync_selected_layout_refs(switched)

    assert packed.layout_asset_id == "lay_after"
    assert [layout.selected_for_h3 for layout in packed.layout_refs] == [True, True]
    assert [ref.asset_id for ref in packed.refs] == [
        "base_1",
        "lay_before",
        "lay_after",
    ]


def test_pending_generated_layout_is_included_in_h3_pack_by_default():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=1)
    pending = shot.layout_refs[0].model_copy(
        update={
            "review_status": LayoutReviewStatus.pending_review,
            "selected_for_h3": False,
        }
    )
    shot = shot.model_copy(
        update={
            "layout_asset_id": "lay_before",
            "layout_refs": [pending],
        }
    )

    packed = sync_selected_layout_refs(shot)

    assert [(ref.asset_id, ref.picture_index) for ref in packed.refs] == [
        ("base_1", 1),
        ("lay_before", 2),
    ]


def test_h3_pack_does_not_silently_truncate_above_nine():
    from app.core.projects.layouts import sync_selected_layout_refs

    shot = _multi_layout_shot(base_ref_count=9)

    with pytest.raises(ValueError) as exc_info:
        sync_selected_layout_refs(shot)

    message = str(exc_info.value)
    assert "H3 supports at most 9 Picture references" in message
    assert "lay_after" in message and "post-entry blocking" in message


def test_selected_layout_prompt_context_uses_actual_picture_numbers():
    from app.core.projects.layouts import selected_layout_prompt_context

    context = selected_layout_prompt_context(_multi_layout_shot())

    assert context == [
        {
            "asset_id": "lay_before",
            "picture_index": 5,
            "purpose": "before entry",
            "state_description": "doorway empty",
            "time_hint": "before Chen enters",
            "origin_kind": "",
            "visible_transition_required": False,
        },
        {
            "asset_id": "lay_after",
            "picture_index": 6,
            "purpose": "post-entry blocking",
            "state_description": "Chen outside glass",
            "time_hint": "after Chen enters",
            "origin_kind": "",
            "visible_transition_required": False,
        },
    ]


def test_selected_clip_tail_layout_requires_visible_transition():
    from app.core.projects.layouts import selected_layout_prompt_context

    tail = _multi_layout_shot().layout_refs[0].model_copy(
        update={
            "origin": ClipTailFrameOrigin(
                source_shot_id="sht_previous",
                source_job_id="job_previous",
                source_generation=1,
                output_kind="enhanced",
                output_key="video",
                source_filename="previous.mp4",
            )
        }
    )
    shot = _multi_layout_shot().model_copy(
        update={"layout_refs": [tail, _multi_layout_shot().layout_refs[1]]}
    )

    context = selected_layout_prompt_context(shot)

    assert context[0]["origin_kind"] == "clip_tail_frame"
    assert context[0]["visible_transition_required"] is True


def test_layout_prompt_signature_changes_with_grounding_state():
    from app.core.projects.layouts import layout_prompt_signature

    shot = _multi_layout_shot()
    original = layout_prompt_signature(shot)
    changed_layout = shot.layout_refs[1].model_copy(
        update={"purpose": "changed blocking"}
    )
    changed = shot.model_copy(
        update={"layout_refs": [shot.layout_refs[0], changed_layout]}
    )

    assert original == layout_prompt_signature(shot)
    assert original != layout_prompt_signature(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state_description", "Mia now stands behind the chair"),
        ("time_hint", "the held silence before the first line"),
    ],
)
def test_layout_prompt_signature_changes_when_prompt_grounding_changes(field, value):
    """Editing Layout direction must invalidate an already-written H3 prompt."""
    from app.core.projects.layouts import layout_prompt_signature

    shot = _multi_layout_shot().model_copy(
        update={"layout_asset_id": "lay_after"}
    )
    original = layout_prompt_signature(shot)
    changed_layout = shot.layout_refs[1].model_copy(update={field: value})
    changed = shot.model_copy(
        update={"layout_refs": [shot.layout_refs[0], changed_layout]}
    )

    assert original != layout_prompt_signature(changed)
