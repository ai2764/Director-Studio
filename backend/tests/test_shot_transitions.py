import pytest
from app.core.projects.models import (
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
    ShotStatus,
)
from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
from app.core.projects import transitions
from app.core.projects.transitions import apply_transition, assert_h3_submittable


def _empty_prompt() -> PromptSections:
    return PromptSections(
        subject_definitions="s",
        summary="s",
        retention_analysis="r",
        detailed_description="d",
        overall_soundscape="o",
        non_diegetic_music="n",
    )


def _shot_with_two_layouts() -> Shot:
    return Shot(
        id="sht_multi_layout",
        project_id="prj_1",
        scene_id="sc01",
        title="Two states",
        script_beat="actor enters after the empty-room composition",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="empty-room composition",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lr_after",
                asset_id="lay_after",
                purpose="post-entry blocking",
                review_status=LayoutReviewStatus.pending_review,
                selected_for_h3=True,
            ),
        ],
    )


def test_reject_one_layout_does_not_change_sibling():
    shot = _shot_with_two_layouts()

    updated = transitions.review_layout_reference(
        shot,
        "lr_after",
        LayoutReviewStatus.reject,
        "actor appears too early",
    )

    assert shot.layout_refs[1].review_status == LayoutReviewStatus.pending_review
    assert shot.layout_refs[1].selected_for_h3 is True
    assert updated.layout_refs[0] == shot.layout_refs[0]
    assert updated.layout_refs[1].review_status == LayoutReviewStatus.reject
    assert updated.layout_refs[1].review_feedback == "actor appears too early"
    assert updated.layout_refs[1].selected_for_h3 is False


def test_rejected_layout_cannot_be_selected():
    shot = _shot_with_two_layouts()
    rejected = shot.layout_refs[1].model_copy(
        update={
            "review_status": LayoutReviewStatus.reject,
            "selected_for_h3": False,
        }
    )
    shot = shot.model_copy(update={"layout_refs": [shot.layout_refs[0], rejected]})

    with pytest.raises(ValueError, match="rejected Layout cannot be selected"):
        transitions.select_layout_reference(shot, "lr_after", True)


def test_rejected_layout_already_bound_as_picture_fails_h3_preflight():
    shot = Shot(
        id="sht_reject_after_bind",
        project_id="prj_1",
        scene_id="sc01",
        title="Rejected bound Layout",
        script_beat="actor enters",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_bound_reject",
                picture_index=1,
            )
        ],
        layout_refs=[
            LayoutReference(
                id="lr_bound_reject",
                asset_id="lay_bound_reject",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            )
        ],
        prompt_sections=_empty_prompt(),
    )
    rejected = transitions.review_layout_reference(
        shot,
        "lr_bound_reject",
        LayoutReviewStatus.reject,
        "identity drift",
    )

    assert rejected.layout_refs[0].review_status == LayoutReviewStatus.reject
    assert rejected.refs[0].asset_id == "lay_bound_reject"
    with pytest.raises(
        ValueError,
        match=r"lr_bound_reject.*lay_bound_reject",
    ):
        assert_h3_submittable(rejected)


def test_duplicate_layout_asset_association_fails_h3_preflight_as_ambiguous():
    shot = Shot(
        id="sht_ambiguous_layout_asset",
        project_id="prj_1",
        scene_id="sc01",
        title="Ambiguous Layout asset",
        script_beat="actor enters",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_shared",
                picture_index=1,
            )
        ],
        layout_asset_id="lay_shared",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lr_shared_rejected",
                asset_id="lay_shared",
                review_status=LayoutReviewStatus.reject,
                selected_for_h3=False,
            ),
            LayoutReference(
                id="lr_shared_usable",
                asset_id="lay_shared",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
        ],
        prompt_sections=_empty_prompt(),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"ambiguous.*lay_shared.*lr_shared_rejected.*lr_shared_usable"
        ),
    ):
        assert_h3_submittable(shot)


def test_repair_layout_requires_recorded_human_override_before_selection():
    shot = _shot_with_two_layouts()
    reviewed = transitions.review_layout_reference(
        shot,
        "lr_after",
        LayoutReviewStatus.usable_with_repair,
        "minor hand cleanup",
    )
    assert reviewed.layout_refs[1].selected_for_h3 is False

    with pytest.raises(ValueError, match="human override"):
        transitions.select_layout_reference(reviewed, "lr_after", True)

    overridden = transitions.review_layout_reference(
        reviewed,
        "lr_after",
        LayoutReviewStatus.usable_with_repair,
        "accept hand artifact for this run",
        human_override=True,
    )
    assert overridden.layout_refs[1].selected_for_h3 is False
    assert overridden.meta["layout_human_overrides"]["lr_after"] is True

    selected = transitions.select_layout_reference(overridden, "lr_after", True)
    assert selected.layout_refs[1].selected_for_h3 is True
    assert selected.layout_asset_id == "lay_after"


def test_legacy_reject_targets_only_mirrored_compatibility_layout():
    shot = _shot_with_two_layouts().model_copy(
        update={
            "layout_asset_id": "lay_before",
            "layout_review_status": "approved",
        }
    )

    updated = apply_transition(
        shot,
        "reject_layout",
        feedback="wrong empty-room geography",
        status=ShotStatus.needs_review,
    )

    assert updated.layout_refs[0].review_status == LayoutReviewStatus.reject
    assert updated.layout_refs[0].selected_for_h3 is False
    assert updated.layout_refs[1].review_status == LayoutReviewStatus.pending_review


def test_legacy_approve_replaces_asset_on_mirrored_compatibility_layout():
    shot = Shot(
        id="sht_legacy_replace",
        project_id="prj_1",
        scene_id="sc01",
        title="Replacement",
        script_beat="replacement composition",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_old",
        layout_review_status="pending_review",
        layout_refs=[
            LayoutReference(
                id="lr_compat",
                asset_id="lay_old",
                review_status=LayoutReviewStatus.pending_review,
            )
        ],
    )

    updated = apply_transition(
        shot,
        "approve_layout",
        layout_asset_id="lay_replacement",
    )

    assert updated.layout_refs[0].asset_id == "lay_replacement"
    assert updated.layout_refs[0].review_status == LayoutReviewStatus.usable
    assert updated.layout_refs[0].selected_for_h3 is True
    assert updated.layout_asset_id == "lay_replacement"


def test_legacy_approve_targets_existing_requested_layout_without_overwriting_sibling():
    shot = Shot(
        id="sht_legacy_existing_target",
        project_id="prj_1",
        scene_id="sc01",
        title="Existing target",
        script_beat="approve the second composition",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_a",
        layout_review_status="pending_review",
        layout_refs=[
            LayoutReference(
                id="lr_a",
                asset_id="lay_a",
                purpose="opening composition",
                review_status=LayoutReviewStatus.pending_review,
            ),
            LayoutReference(
                id="lr_b",
                asset_id="lay_b",
                purpose="revealed composition",
                review_status=LayoutReviewStatus.pending_review,
            ),
        ],
    )

    updated = apply_transition(
        shot,
        "approve_layout",
        layout_asset_id="lay_b",
        feedback="use the revealed composition",
    )

    assert [(layout.id, layout.asset_id) for layout in updated.layout_refs] == [
        ("lr_a", "lay_a"),
        ("lr_b", "lay_b"),
    ]
    assert updated.layout_refs[0].review_status == LayoutReviewStatus.pending_review
    assert updated.layout_refs[1].review_status == LayoutReviewStatus.usable
    assert updated.layout_refs[1].selected_for_h3 is True
    assert updated.layout_asset_id == "lay_b"


def test_legacy_approve_does_not_rewrite_a_multi_layout_for_unknown_asset():
    shot = Shot(
        id="sht_legacy_unknown_target",
        project_id="prj_1",
        scene_id="sc01",
        title="Unknown target",
        script_beat="approve a requested composition",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_a",
        layout_review_status="pending_review",
        layout_refs=[
            LayoutReference(id="lr_a", asset_id="lay_a"),
            LayoutReference(id="lr_b", asset_id="lay_b"),
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"does not match an existing LayoutReference.*lay_unknown",
    ):
        apply_transition(
            shot,
            "approve_layout",
            layout_asset_id="lay_unknown",
        )

    assert [layout.asset_id for layout in shot.layout_refs] == ["lay_a", "lay_b"]


def test_can_submit_without_layout_when_refs_present():
    """Layout reference-frame is optional for H3; no Gate-2 approve required."""
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1)],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_review_status=None,
        layout_asset_id=None,
    )
    assert_h3_submittable(shot)  # does not raise


def test_submit_h3_clears_legacy_optional_layout_block():
    shot = Shot(
        id="sht_layout_optional",
        project_id="prj_1",
        scene_id="sc01",
        title="Optional layout",
        script_beat="actor crosses the room",
        duration_s=8.0,
        status=ShotStatus.blocked,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1)],
        prompt_sections=_empty_prompt(),
        blocked_reasons=["ref_frame requires a scene library ref"],
    )

    queued = apply_transition(shot, "submit_h3")

    assert queued.status == ShotStatus.queued
    assert queued.blocked_reasons == []


def test_can_submit_from_needs_review_without_approve():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1)],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id=None,
    )
    assert_h3_submittable(shot)


def test_h3_picture_indices_must_be_contiguous_from_one():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_1", picture_index=3),
        ],
        prompt_sections=_empty_prompt(),
        dialogue=[],
    )

    with pytest.raises(ValueError, match="contiguous"):
        assert_h3_submittable(shot)


def test_layout_asset_is_only_used_when_selected_in_refs():
    """A generated composition reference is a candidate, not an implicit H3 input."""
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1)],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id="lay_candidate",
    )

    assert_h3_submittable(shot)


def test_cannot_submit_without_any_refs():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.approved,
        refs=[],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_review_status="pending_review",
    )
    with pytest.raises(ValueError, match="at least one image ref"):
        assert_h3_submittable(shot)


def test_approve_shot_from_ref_frame_pending():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.ref_frame_pending,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1)],
        prompt_sections=_empty_prompt(),
        dialogue=[],
    )
    out = apply_transition(shot, "approve_shot")
    assert out.status == ShotStatus.approved


def test_layout_approve_moves_to_needs_review():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id="lay_1",
        layout_review_status="pending_review",
    )
    out = apply_transition(shot, "approve_layout")
    assert out.status == ShotStatus.needs_review
    assert out.layout_review_status == "approved"
    assert any(r.role == RefRole.layout_ref_frame and r.picture_index == 1 for r in out.refs)


def test_layout_need_not_be_picture_1():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="b",
        duration_s=8.0,
        status=ShotStatus.approved,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1),
            ShotRef(role=RefRole.layout_ref_frame, asset_id="lay_1", picture_index=2),
        ],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id="lay_1",
        layout_review_status="approved",
    )
    assert_h3_submittable(shot)


def test_approve_layout_payload_cannot_clobber_gate_fields():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_1", picture_index=1)],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id="lay_1",
        layout_review_status="pending_review",
    )
    out = apply_transition(
        shot,
        "approve_layout",
        status=ShotStatus.approved,
        layout_review_status="rejected",
        refs=[],
        feedback="looks good",
    )
    assert out.status == ShotStatus.needs_review
    assert out.layout_review_status == "approved"
    layout = next(r for r in out.refs if r.role == RefRole.layout_ref_frame)
    actor = next(r for r in out.refs if r.role == RefRole.actor)
    assert actor.picture_index == 1
    assert layout.picture_index == 2
    assert out.feedback == "looks good"


def test_reject_layout_rejects_illegal_status():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[],
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id="lay_1",
        layout_review_status="pending_review",
    )
    with pytest.raises(ValueError, match="reject_layout status"):
        apply_transition(shot, "reject_layout", status=ShotStatus.approved)
    out = apply_transition(shot, "reject_layout", status=ShotStatus.needs_review)
    assert out.status == ShotStatus.needs_review
    assert out.layout_review_status == "rejected"


def test_empty_prompt_fails_assert_h3_submittable():
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.approved,
        refs=[
            ShotRef(role=RefRole.layout_ref_frame, asset_id="lay_1", picture_index=1),
        ],
        prompt_sections=PromptSections(),  # all empty
        dialogue=[],
        layout_asset_id="lay_1",
        layout_review_status="approved",
    )
    with pytest.raises(ValueError, match="prompt sections must be non-empty"):
        assert_h3_submittable(shot)


def test_more_than_nine_refs_fails_assert_h3_submittable():
    # 10 refs; use model_construct so count guard is exercised independent of index bounds.
    refs = [
        ShotRef.model_construct(
            role=RefRole.layout_ref_frame if i == 1 else RefRole.prop,
            asset_id="lay_1" if i == 1 else f"p_{i}",
            picture_index=i,
            notes="",
        )
        for i in range(1, 11)
    ]
    assert len(refs) == 10
    shot = Shot(
        id="sht_1",
        project_id="prj_1",
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.approved,
        refs=refs,
        prompt_sections=_empty_prompt(),
        dialogue=[],
        layout_asset_id="lay_1",
        layout_review_status="approved",
    )
    with pytest.raises(ValueError, match="at most 9"):
        assert_h3_submittable(shot)
