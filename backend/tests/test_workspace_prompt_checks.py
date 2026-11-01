import pytest
from fastapi import HTTPException

from app.core.projects.models import Shot, ShotRef, PromptSections
from app.core.projects.prompt_checks import brief_signature, check_prompt, stamp_prompt


def shot_with_prompt():
    shot = Shot(id="s1", project_id="p1", scene_id="room", title="Entrance", script_beat="Enter the room.", duration_s=5,
                refs=[ShotRef(role="actor", asset_id="actor", picture_index=1)],
                prompt_sections=PromptSections(subject_definitions="Actor <Picture 1>", summary="Enter.",
                    retention_analysis="Preserve identity.", detailed_description="Walk through the door.",
                    overall_soundscape="Footsteps.", non_diegetic_music="None."))
    return stamp_prompt(shot)


def codes(shot):
    return {issue["code"] for issue in check_prompt(shot)["errors"]}


def test_picture_coverage_and_unknown_tags():
    shot = shot_with_prompt()
    assert check_prompt(shot)["valid"]
    shot.refs.append(ShotRef(role="scene", asset_id="room", picture_index=2))
    assert {"prompt_stale", "unused_picture"} <= codes(shot)
    shot.prompt_sections.summary = "<Picture 2> and <Picture 3>"
    assert "unused_picture" not in codes(shot)
    assert "unknown_picture" in codes(shot)


@pytest.mark.parametrize("field,value", [("script_beat", "Run."), ("duration_s", 8), ("camera_motion", "pan"),
                                        ("dialogue", ["Hello."])])
def test_authored_changes_invalidate_prompt(field, value):
    shot = shot_with_prompt()
    setattr(shot, field, value)
    assert check_prompt(shot)["stale"]
    stamp_prompt(shot)
    assert not check_prompt(shot)["stale"]


def test_status_output_and_title_do_not_invalidate_content():
    shot = shot_with_prompt()
    old = brief_signature(shot)
    shot.title = "New label"
    shot.h3_job_id = "job1"
    assert brief_signature(shot) == old


@pytest.mark.parametrize("text,code", [("<Picture two>", "reference_tag_format"), ("Picture 1", "reference_tag_delimiters"),
                                     ("<Picture1>", "reference_tag_format"), ("</d>hello<d>", "dialogue_tag_format"),
                                     ("<d><d>hello</d></d>", "dialogue_tag_format"), ("<Audio 1>", "audio_bindings"),
                                     ("Broken \ufffd", "encoding")])
def test_obvious_format_errors(text, code):
    shot = shot_with_prompt()
    shot.prompt_sections.summary = text
    assert code in codes(shot)


def test_legacy_warning_and_cleared_stale_prompt():
    shot = shot_with_prompt()
    shot.meta = {}
    check = check_prompt(shot)
    assert check["valid"] and not check["stale"]
    assert check["warnings"][0]["code"] == "legacy_unverified"
    shot.meta["prompt_brief_stale"] = True
    shot.prompt_sections = PromptSections()
    assert {"prompt_stale", "missing_sections"} <= codes(shot)
