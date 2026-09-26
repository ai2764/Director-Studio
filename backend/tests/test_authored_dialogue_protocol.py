"""Authored speech stays raw; H3 markup belongs only in generated prompts."""

import pytest
from pydantic import ValidationError

from app.agents.director.planner import ShotDraft, ShotRevisionSubmission
from app.agents.director.prompt_repair import repair_request
from app.core.projects.dialogue import (
    DialogueContractError,
    DialogueIssue,
    apply_dialogue_update,
)
from app.core.projects.models import Shot


def shot_payload(**changes):
    return {
        "id": "s1", "project_id": "p1", "scene_id": "scene1", "title": "Kira",
        "script_beat": "Kira asks a question.", "duration_s": 6, **changes,
    }


def line_payload(text):
    return {
        "line_id": "l1", "speaker_id": "kira", "speaker_name": "Kira", "text": text,
        "language": "Chinese", "source": {
            "kind": "script", "source_hash": "a" * 64, "scene_id": "scene1",
            "quote": f"Kira: {text}", "occurrence": 0,
        },
    }


def draft_payload(dialogue):
    return {
        "scene_id": "scene1", "title": "Kira", "script_beat": "Kira asks a question.",
        "shot_type": "close-up", "camera_angle": "eye level", "camera_motion": "still",
        "composition": "Kira at center", "duration_s": 6, "dialogue": dialogue,
    }


def test_revise_shot_rejects_h3_speech_wrapper_before_saving():
    from app.agents.director.service import DirectorService
    from app.core.projects.store import create_project, load_shot, save_project, save_shot

    project = create_project("Kira", "Kira asks a question.")
    shot = Shot(**{**shot_payload(dialogue=["你喜欢我什么啊？"]), "project_id": project.id})
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    with pytest.raises((ValueError, ValidationError), match="(?i)(dialogue|markup|protocol)"):
        DirectorService(plan_provider=None).revise_shot(project.id, {
            "shot_id": shot.id, "dialogue": ["<d>[zh] kira你喜欢我什么啊？</d>"],
        })

    assert load_shot(project.id, shot.id).dialogue == ["你喜欢我什么啊？"]


def test_explicit_structured_dialogue_rejects_h3_markup():
    original = Shot(**shot_payload(dialogue=["你喜欢我什么啊？"]))
    with pytest.raises(ValueError, match="(?i)(dialogue|markup|protocol)"):
        apply_dialogue_update(original, {
            "dialogue_lines": [line_payload("<d>[Chinese] 你喜欢我什么啊？</d>")],
        })
    assert original.dialogue == ["你喜欢我什么啊？"]


def test_storyboard_submission_rejects_h3_markup_in_raw_dialogue():
    with pytest.raises(ValidationError, match="(?i)(dialogue|markup|protocol)"):
        ShotDraft.model_validate(draft_payload(["<d>[Chinese] 你喜欢我什么啊？</d>"]))


def test_revision_submission_rejects_h3_markup_in_structured_line():
    with pytest.raises(ValidationError, match="(?i)(dialogue|markup|protocol)"):
        ShotRevisionSubmission.model_validate({
            "shot_id": "s1", "dialogue_lines": [line_payload("<d>[Chinese] 你喜欢我什么啊？</d>")],
        })


def test_plain_angle_bracket_prose_and_legacy_project_read_remain_usable():
    prose = "Kira asks whether 2 < 3 > 1, then points at <未来>."
    original = Shot(**shot_payload(dialogue=[prose]))
    assert apply_dialogue_update(original, {"dialogue": [prose]}).dialogue == [prose]
    assert ShotDraft.model_validate(draft_payload([prose])).dialogue == [prose]
    legacy = Shot.model_validate(shot_payload(dialogue=["<d>[zh] 旧台词</d>"]))
    assert legacy.dialogue == ["<d>[zh] 旧台词</d>"]


def test_dialogue_error_omits_empty_expected_and_actual_but_keeps_action():
    error = DialogueContractError([DialogueIssue(
        code="dialogue_source_missing", line_id="l1", action="Attribute Kira's words.",
    )])
    assert error.failure_kind == "contract"
    assert error.issues[0].line_id == "l1"
    assert "dialogue_source_missing" in str(error)
    assert "l1" in str(error)
    assert "Attribute Kira's words." in str(error)
    assert "expected ''" not in str(error)
    assert "got ''" not in str(error)


def test_repair_request_includes_structured_dialogue_issue_without_rewriting_prose():
    original = "The close-up holds on Kira's hesitation."
    request = repair_request(original, {
        "error": "Dialogue attribution failed", "rejected_candidate": '{"prompt_sections": {}}',
        "issues": [{"code": "dialogue_speaker_mismatch", "line_id": "l1",
                    "expected": "kira", "actual": "tao", "evidence": "Kira: 你喜欢我什么啊？",
                    "action": "Bind the speech block to Kira."}],
    })
    assert original in request
    assert "dialogue_speaker_mismatch" in request
    assert "l1" in request
    assert "Bind the speech block to Kira." in request
    assert "Keep all valid content" in request
