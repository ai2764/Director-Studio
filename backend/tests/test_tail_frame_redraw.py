"""Redraw extracted tail-frame Layouts from Image1."""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from PIL import Image

from app.agents.director.chat import DIRECTOR_TOOL_SCHEMAS, _run_tools
from app.agents.director.service import build_tail_frame_revision_brief
from app.agents.director.visual_direction import analyze_ref_frame
from app.core.projects.layouts import (
    ClipTailFrameOrigin,
    LayoutBrief,
    LayoutReference,
    LayoutReviewStatus,
    LayoutSourceRef,
)
from app.core.projects.models import RefRole, Shot, ShotStatus
from app.core.projects.store import create_project, load_shot, save_project, save_shot


ORIGIN = ClipTailFrameOrigin(
    source_shot_id="sht_shot2",
    source_job_id="job_h3_v2",
    source_generation=2,
    output_kind="enhanced",
    output_key="video",
    source_filename="take.mp4",
)


def _schema_revise() -> dict:
    return next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "revise_ref_frame"
    )


def test_revise_schema_exposes_additional_source_refs():
    props = _schema_revise()["function"]["parameters"]["properties"]
    extra = props["additional_source_refs"]
    assert extra["maxItems"] == 2
    assert extra["items"]["required"] == ["role", "asset_id"]


def _tail_layout() -> LayoutReference:
    return LayoutReference(
        id="lref_tail",
        asset_id="lay_tail",
        purpose="cross-shot visual continuity from sht_shot2",
        state_description="final visible state of sht_shot2",
        time_hint="transition from sht_shot2",
        review_status=LayoutReviewStatus.pending_review,
        selected_for_h3=False,
        origin=ORIGIN,
    )


def _generated_layout() -> LayoutReference:
    return LayoutReference(
        id="lref_generated",
        asset_id="lay_generated",
        purpose="primary composition",
        review_status=LayoutReviewStatus.usable,
        selected_for_h3=True,
    )


class _CaptureService:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.briefs: list[LayoutBrief] = []

    async def queue_reference_frame(self, shot_id: str, *, brief, force=False):
        self.briefs.append(brief)
        current = load_shot(self.project_id, shot_id)
        assert current is not None
        replacement = LayoutReference(
            id="lref_redraw",
            asset_id="lay_redraw",
            purpose=brief.purpose,
            state_description=brief.state_description,
            time_hint=brief.time_hint,
            source_refs=list(brief.source_refs),
            review_status=LayoutReviewStatus.pending_review,
            selected_for_h3=False,
        )
        updated = current.model_copy(
            update={"layout_refs": [*current.layout_refs, replacement]}
        )
        save_shot(updated)
        return updated


@pytest.mark.asyncio
async def test_tail_frame_redraw_uses_extracted_layout_as_image1(tmp_projects_dir):
    project = create_project("Tail redraw", "INT. HALL")
    shot = Shot(
        id="sht_shot3",
        project_id=project.id,
        scene_id="sc01",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_refs=[_tail_layout()],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    svc = _CaptureService(project.id)

    await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "revise_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": "lref_tail",
                    "feedback": "remove motion blur and keep corridor geography",
                    "additional_source_refs": [
                        {"role": "actor", "asset_id": "act_kai"},
                        {"role": "scene", "asset_id": "scn_hall"},
                    ],
                },
            }
        ],
        svc=svc,
        actions=[],
        user_feedback="去运动模糊，走廊结构别变。",
    )

    brief = svc.briefs[0]
    assert [ref.role for ref in brief.source_refs] == [
        RefRole.layout_ref_frame,
        RefRole.actor,
        RefRole.scene,
    ]
    assert [ref.asset_id for ref in brief.source_refs] == [
        "lay_tail",
        "act_kai",
        "scn_hall",
    ]
    notes = (brief.source_refs[0].notes or "").lower()
    assert "do not recreate a contact sheet" in notes
    assert "motion blur" in notes
    saved = load_shot(project.id, shot.id)
    assert saved is not None
    original, replacement = saved.layout_refs
    assert original.review_status == LayoutReviewStatus.usable_with_repair
    assert original.selected_for_h3 is False
    assert original.superseded_by == "lref_redraw"
    assert original.review_feedback == "remove motion blur and keep corridor geography"
    assert replacement.revision_of == "lref_tail"
    assert replacement.review_status == LayoutReviewStatus.pending_review
    assert replacement.selected_for_h3 is True
    assert saved.layout_asset_id == replacement.asset_id


@pytest.mark.asyncio
async def test_tail_frame_redraw_rejects_more_than_two_additional_sources(
    tmp_projects_dir,
):
    project = create_project("Too many sources", "INT. HALL")
    shot = Shot(
        id="sht_shot3",
        project_id=project.id,
        scene_id="sc01",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_refs=[_tail_layout()],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    svc = _CaptureService(project.id)

    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "revise_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": "lref_tail",
                    "feedback": "add identity, wardrobe, and a prop",
                    "additional_source_refs": [
                        {"role": "actor", "asset_id": "act_kai"},
                        {"role": "costume", "asset_id": "cos_coat"},
                        {"role": "prop", "asset_id": "prp_key"},
                    ],
                },
            }
        ],
        svc=svc,
        actions=[],
        user_feedback="再加演员、衣服和钥匙",
    )

    assert svc.briefs == []
    saved = load_shot(project.id, shot.id)
    assert saved is not None
    assert [layout.id for layout in saved.layout_refs] == ["lref_tail"]
    assert any("at most" in note.lower() or "3" in note for note in notes)


@pytest.mark.asyncio
async def test_ordinary_generated_layout_is_not_reused_as_qwen_image1(
    tmp_projects_dir,
):
    project = create_project("Ordinary revise", "INT. HALL")
    shot = Shot(
        id="sht_shot3",
        project_id=project.id,
        scene_id="sc01",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_generated",
        layout_refs=[_generated_layout()],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    svc = _CaptureService(project.id)

    await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "revise_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": "lref_generated",
                    "feedback": "人物站位过近",
                },
            }
        ],
        svc=svc,
        actions=[],
        user_feedback="人物太靠前，重新生成。",
    )

    brief = svc.briefs[0]
    assert not any(
        ref.role == RefRole.layout_ref_frame and ref.asset_id == "lay_generated"
        for ref in brief.source_refs
    )
    saved = load_shot(project.id, shot.id)
    assert saved is not None
    assert saved.layout_refs[0].review_status == LayoutReviewStatus.reject


@pytest.mark.asyncio
async def test_tail_frame_redraw_with_zero_additional_sources(tmp_projects_dir):
    project = create_project("Zero extra sources", "INT. HALL")
    shot = Shot(
        id="sht_shot3",
        project_id=project.id,
        scene_id="sc01",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_refs=[_tail_layout()],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    svc = _CaptureService(project.id)

    await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "revise_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": "lref_tail",
                    "feedback": "keep the corridor geography",
                },
            }
        ],
        svc=svc,
        actions=[],
        user_feedback="走廊结构别变。",
    )

    brief = svc.briefs[0]
    assert [ref.role for ref in brief.source_refs] == [RefRole.layout_ref_frame]
    assert [ref.asset_id for ref in brief.source_refs] == ["lay_tail"]
    saved = load_shot(project.id, shot.id)
    assert saved is not None
    original, replacement = saved.layout_refs
    assert original.review_status == LayoutReviewStatus.usable_with_repair
    assert original.selected_for_h3 is False
    assert replacement.revision_of == "lref_tail"
    assert replacement.review_status == LayoutReviewStatus.pending_review
    assert replacement.selected_for_h3 is True


@pytest.mark.asyncio
async def test_tail_frame_feedback_reaches_visual_direction_and_compiled_prompt():
    feedback = "remove motion blur and keep corridor geography"
    layout_brief = build_tail_frame_revision_brief(
        _tail_layout(),
        [],
        feedback=feedback,
    )
    payload = {
        "shot_type": "wide shot",
        "camera": "eye level",
        "scene_lock": ["preserve corridor geography"],
        "characters": [],
        "forbidden": ["contact sheet"],
        "generation_prompt": (
            "Use Image1 to preserve corridor geography. "
            "remove motion blur and keep corridor geography."
        ),
    }
    calls: list[dict] = []

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return json.dumps(payload)

    buf = BytesIO()
    Image.new("RGB", (64, 36), "navy").save(buf, format="PNG")
    shot = Shot(
        id="sht_shot3",
        project_id="prj_tail",
        scene_id="sc01",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
    )
    result = await analyze_ref_frame(
        shot,
        images={"ref_0": ("layout.png", buf.getvalue())},
        captions=["Image1 layout_ref_frame extracted tail frame"],
        layout_brief=layout_brief,
        feedback=feedback,
        model="qwen-test",
        ollama=_VisionClient(),
    )

    prompt = calls[0]["prompt"]
    assert "CONTINUITY REDRAW" in prompt
    assert "HUMAN FEEDBACK" in prompt
    assert feedback in prompt
    assert "REPAIR REVIEW" not in prompt
    assert result.review_image_used is False
    assert result.review_feedback == feedback
    assert feedback in result.compiled_prompt
    assert "Image1" in result.compiled_prompt
