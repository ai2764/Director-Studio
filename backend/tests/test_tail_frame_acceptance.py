"""Accept extracted tail-frame Layouts into H3 Pictures and prompts."""

from __future__ import annotations

import pytest

from app.agents.director.chat import _run_tools
from app.core.h3.prompt import compose_h3_prompt, validate_h3_prompt
from app.core.projects.layouts import (
    ClipTailFrameOrigin,
    LayoutReference,
    LayoutReviewStatus,
    selected_layout_prompt_context,
    sync_selected_layout_refs,
)
from app.core.projects.models import PromptSections, RefRole, Shot, ShotRef, ShotStatus
from app.core.projects.store import create_project, load_shot, save_project, save_shot


ORIGIN = ClipTailFrameOrigin(
    source_shot_id="sht_shot2",
    source_job_id="job_h3_v2",
    source_generation=2,
    output_kind="enhanced",
    output_key="video",
    source_filename="take.mp4",
)


def _tail_layout(**updates) -> LayoutReference:
    layout = LayoutReference(
        id="lref_tail",
        asset_id="lay_tail",
        purpose="cross-shot visual continuity from sht_shot2",
        state_description="final visible state of sht_shot2 for continuity into sht_shot3",
        time_hint="transition from sht_shot2 into this shot",
        review_status=LayoutReviewStatus.pending_review,
        selected_for_h3=False,
        origin=ORIGIN,
    )
    return layout.model_copy(update=updates) if updates else layout


def _shot(project_id: str, **updates) -> Shot:
    shot = Shot(
        id="sht_shot3",
        project_id=project_id,
        scene_id="sc01",
        title="shot3",
        script_beat="Kai opens the archive door.",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_refs=[_tail_layout()],
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_kai", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_hall", picture_index=2),
        ],
    )
    if updates:
        shot = shot.model_copy(update=updates)
    save_shot(shot)
    return shot


def _continuity_sections(picture_index: int) -> PromptSections:
    tag = f"<Picture {picture_index}>"
    return PromptSections(
        subject_definitions=(
            f"{tag} preserves the final blocking, wardrobe state, corridor "
            "geography, and cold lighting carried from shot2 into this shot."
        ),
        summary="Kai continues through the doorway without resetting geography.",
        retention_analysis="Hold attention on the door reveal.",
        detailed_description="0-6 seconds: Kai opens the door and steps inside.",
        overall_soundscape="Quiet rain and fluorescent hum.",
        non_diegetic_music="No non-diegetic music.",
    )


@pytest.mark.asyncio
async def test_accepting_tail_frame_assigns_picture_index_and_rewrites_prompt(
    tmp_projects_dir,
):
    project = create_project("Tail accept", "INT. ARCHIVE")
    shot = _shot(project.id)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    prompt_calls: list[str] = []

    class _Service:
        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            prompt_calls.append(shot_id)
            current = load_shot(project.id, shot_id)
            assert current is not None
            context = selected_layout_prompt_context(current)
            assert context[0]["picture_index"] == 3
            assert "continuity" in context[0]["purpose"]
            updated = current.model_copy(
                update={"prompt_sections": _continuity_sections(3)}
            )
            save_shot(updated)
            return updated

    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "accept_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": "lref_tail",
                    "feedback": "use this continuity frame",
                },
            }
        ],
        svc=_Service(),
        actions=[],
        user_feedback="这张直接用",
    )

    saved = load_shot(project.id, shot.id)
    assert saved is not None
    layout = saved.layout_refs[0]
    assert layout.review_status == LayoutReviewStatus.usable
    assert layout.selected_for_h3 is True
    packed = sync_selected_layout_refs(saved)
    layout_ref = next(ref for ref in packed.refs if ref.asset_id == "lay_tail")
    assert layout_ref.picture_index == 3
    assert prompt_calls == [shot.id]
    assert touched == {shot.id}
    text = compose_h3_prompt(saved.prompt_sections)
    assert "<Picture 3>" in text
    assert "shot2" in text
    validate_h3_prompt(
        text,
        [],
        required_picture_indices=[3],
        submitted_picture_indices=[1, 2, 3],
    )
    assert any("Selected Layout lref_tail" in note for note in notes)
    reply = "\n".join(notes)
    assert "Current H3 Picture order:" in reply
    assert "Picture 1 — Actor: act_kai" in reply
    assert "Picture 2 — Scene: scn_hall" in reply
    assert "Picture 3 — Tail frame from sht_shot2: lay_tail (layout)" in reply


@pytest.mark.asyncio
async def test_accept_then_explicit_write_prompt_only_rewrites_once(tmp_projects_dir):
    project = create_project("Tail accept once", "INT. ARCHIVE")
    shot = _shot(project.id)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    prompt_calls: list[str] = []

    class _Service:
        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            prompt_calls.append(shot_id)
            current = load_shot(project.id, shot_id)
            assert current is not None
            return current

    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "accept_ref_frame",
                "args": {"shot_id": shot.id, "layout_ref_id": "lref_tail"},
            },
            {"name": "write_prompt", "args": {"shot_id": shot.id}},
        ],
        svc=_Service(),
        actions=[],
        user_feedback="这张直接用",
    )

    assert prompt_calls == [shot.id]
    assert any("already" in note.lower() for note in notes)


@pytest.mark.asyncio
async def test_nine_picture_overflow_keeps_tail_frame_reviewed_unselected(
    tmp_projects_dir,
):
    project = create_project("Overflow", "INT. ARCHIVE")
    refs = [
        ShotRef(
            role=RefRole.actor if i % 2 else RefRole.scene,
            asset_id=f"asset_{i}",
            picture_index=i,
        )
        for i in range(1, 10)
    ]
    shot = _shot(project.id, refs=refs)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    prompt_calls: list[str] = []

    class _Service:
        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            prompt_calls.append(shot_id)
            current = load_shot(project.id, shot_id)
            assert current is not None
            return current

    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "accept_ref_frame",
                "args": {"shot_id": shot.id, "layout_ref_id": "lref_tail"},
            }
        ],
        svc=_Service(),
        actions=[],
        user_feedback="这张直接用",
    )

    saved = load_shot(project.id, shot.id)
    assert saved is not None
    layout = saved.layout_refs[0]
    assert layout.review_status == LayoutReviewStatus.usable
    assert layout.selected_for_h3 is False
    assert not any(ref.asset_id == "lay_tail" for ref in saved.refs)
    assert prompt_calls == []
    joined = "\n".join(notes)
    assert "at most 9" in joined or "inventory" in joined.lower()
    assert "lay_tail" in joined or "lref_tail" in joined


@pytest.mark.parametrize(
    "claim",
    [
        "<Picture 3> is the first frame of the next clip.",
        "<Picture 3> is a last-frame socket for shot2.",
        "<Picture 3> activates only at the beginning of the shot.",
        "<Picture 3> keeps the corridor state for 0-3 seconds.",
    ],
)
def test_prompt_validation_rejects_endpoint_and_time_window_claims(claim):
    sections = PromptSections(
        subject_definitions=claim,
        summary="Kai continues through the doorway.",
        retention_analysis="Hold attention on the door.",
        detailed_description="0-6 seconds: Kai opens the door.",
        overall_soundscape="Quiet rain.",
        non_diegetic_music="No music.",
    )
    with pytest.raises(ValueError):
        validate_h3_prompt(
            compose_h3_prompt(sections),
            [],
            required_picture_indices=[3],
            submitted_picture_indices=[3],
        )


def test_prompt_validation_allows_tail_frame_provenance_without_endpoint_claim():
    sections = PromptSections(
        subject_definitions=(
            "<Picture 3> preserves blocking visible in the last frame of shot2 "
            "throughout this clip."
        ),
        summary="Kai continues through the doorway.",
        retention_analysis="Hold attention on the door.",
        detailed_description="0-6 seconds: Kai opens the door.",
        overall_soundscape="Quiet rain.",
        non_diegetic_music="No music.",
    )

    validate_h3_prompt(
        compose_h3_prompt(sections),
        [],
        required_picture_indices=[3],
        submitted_picture_indices=[3],
    )
