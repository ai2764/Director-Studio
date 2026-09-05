import pytest

from app.agents.director.service import build_ref_frame_brief
from app.core.projects.layouts import (
    GptLayoutBrief,
    LayoutProvider,
    LayoutReference,
    LayoutSourceRef,
)
from app.core.projects.models import Project, PromptSections, Shot, ShotStatus


def test_ref_frame_brief_includes_script_beat_and_dialogue():
    project = Project(
        id="prj_x",
        name="t",
        script_text="EXT. METRO - NIGHT\nMAYA holds a broken umbrella.\nMAYA\nOf course.",
        created_at="t",
        updated_at="t",
    )
    shot = Shot(
        id="sht_x",
        project_id="prj_x",
        scene_id="sc01",
        title="Maya Battles Wind",
        script_beat="Medium shot under broken shelter; umbrella flips.",
        duration_s=6.0,
        status=ShotStatus.ref_frame_pending,
        dialogue=["Of course."],
        prompt_sections=PromptSections(),
    )
    brief = build_ref_frame_brief(
        project,
        shot,
        ref_labels=[
            "SCENE environment plate (Metro)",
            "CHARACTER identity still (Maya, master)",
        ],
    )
    assert "STORY / SCRIPT CONTEXT" in brief
    assert "broken umbrella" in brief
    assert "Of course." in brief
    assert "Medium shot under broken shelter" in brief
    assert "SCENE environment plate" in brief
    assert "CHARACTER identity still" in brief
    assert "three-view" in brief.lower() or "poster" in brief.lower()
    assert "feet on floor" in brief.lower() or "physically" in brief.lower()
    assert "blocking" in brief.lower()


def test_gpt_layout_accepts_more_than_three_ordered_sources():
    refs = [
        LayoutSourceRef(role="actor", asset_id=f"actor_{index}")
        for index in range(4)
    ]

    brief = GptLayoutBrief(
        generation_prompt=(
            "Image1 controls actor one. Image2 controls actor two. "
            "Image3 controls actor three. Image4 controls actor four. "
            "Return one cinematic image."
        ),
        source_refs=refs,
    )
    layout = LayoutReference(
        id="lref_gpt",
        provider="gpt",
        source_refs=refs,
    )

    assert [ref.asset_id for ref in brief.source_refs] == [
        f"actor_{index}" for index in range(4)
    ]
    assert layout.provider == LayoutProvider.gpt


def test_gpt_layout_accepts_a_text_only_brief_without_sources():
    brief = GptLayoutBrief(
        generation_prompt="Create one cinematic establishing plate of an empty review room.",
        source_refs=[],
    )

    assert brief.source_refs == []


def test_comfy_layout_still_rejects_four_sources():
    refs = [
        LayoutSourceRef(role="actor", asset_id=f"actor_{index}")
        for index in range(4)
    ]

    with pytest.raises(ValueError, match="at most 3"):
        LayoutReference(id="lref_comfy", provider="comfy", source_refs=refs)
