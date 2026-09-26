"""A language edit names saved lines; the server keeps their spoken words."""

import hashlib

import pytest
from pydantic import ValidationError

from app.agents.director.planner import ShotRevisionSubmission
from app.core.projects.dialogue import apply_dialogue_update, verify_dialogue_sources
from app.core.projects.models import Project, Shot


WORDS = "你喜欢我什么啊？"
SCRIPT = f"Kira: {WORDS}"


def line_payload(*, kind="script"):
    return {
        "line_id": "kira-1", "speaker_id": "kira", "speaker_name": "Kira",
        "text": WORDS, "language": "", "source": {
            "kind": kind, "source_hash": hashlib.sha256(SCRIPT.encode()).hexdigest(),
            "scene_id": "scene-1", "quote": SCRIPT, "occurrence": 0,
        },
    }


def shot(*, lines=True):
    return Shot(id="s1", project_id="p1", scene_id="scene-1", title="Question",
                script_beat="Kira asks.", duration_s=6, dialogue=[WORDS],
                dialogue_lines=[line_payload()] if lines else None,
                meta={"prompt_dialogue_signature": "stale", "prompt_dialogue_contract": {"old": True},
                      "dialogue_grounding": {"old": True}, "other": "keep"})


def test_typed_revision_accepts_language_only_patch_without_retyping_words():
    revision = ShotRevisionSubmission.model_validate({
        "shot_id": "s1", "dialogue_language_updates": [
            {"line_id": "kira-1", "language": "Chinese"}],
    })
    assert revision.model_dump(exclude_unset=True)["dialogue_language_updates"] == [
        {"line_id": "kira-1", "language": "Chinese"}]


def test_typed_revision_rejects_null_language_patch():
    with pytest.raises(ValidationError, match="dialogue_language_updates"):
        ShotRevisionSubmission.model_validate({"shot_id": "s1", "dialogue_language_updates": None})


def test_language_patch_preserves_raw_words_script_source_and_clears_stale_contract():
    original = shot()
    changed = apply_dialogue_update(original, {"dialogue_language_updates": [
        {"line_id": "kira-1", "language": "Chinese"}]})
    assert changed.dialogue == original.dialogue
    assert changed.dialogue_lines[0].text == WORDS
    assert changed.dialogue_lines[0].language == "Chinese"
    assert changed.dialogue_lines[0].source == original.dialogue_lines[0].source
    assert changed.meta == {"other": "keep"}
    assert original.meta["prompt_dialogue_signature"] == "stale"
    project = Project(id="p1", name="Kira", script_text=SCRIPT, created_at="", updated_at="")
    verify_dialogue_sources(project, changed, changed.dialogue_lines)


def test_language_patch_rehashes_existing_authored_revision_without_changing_words():
    original = apply_dialogue_update(shot(lines=False), {"dialogue_lines": [line_payload()]})
    old_hash = original.dialogue_lines[0].source.source_hash
    changed = apply_dialogue_update(original, {"dialogue_language_updates": [
        {"line_id": "kira-1", "language": "Português"}]})
    assert changed.dialogue == original.dialogue
    assert changed.dialogue_lines[0].text == original.dialogue_lines[0].text
    assert changed.dialogue_lines[0].source.kind == "shot_revision"
    assert changed.dialogue_lines[0].source.source_hash != old_hash
    project = Project(id="p1", name="Kira", script_text="", created_at="", updated_at="")
    verify_dialogue_sources(project, changed, changed.dialogue_lines)


@pytest.mark.parametrize("other", ["dialogue", "dialogue_lines"])
def test_typed_revision_rejects_language_patch_mixed_with_words_or_lines(other):
    payload = {"shot_id": "s1", "dialogue_language_updates": [
        {"line_id": "kira-1", "language": "Chinese"}]}
    payload[other] = [WORDS] if other == "dialogue" else [line_payload()]
    with pytest.raises(ValidationError, match="dialogue_language_updates"):
        ShotRevisionSubmission.model_validate(payload)


@pytest.mark.parametrize("updates, expected", [
    ([{"line_id": "kira-1", "language": "Chinese"},
      {"line_id": "kira-1", "language": "English"}], "duplicate"),
    ([{"line_id": "missing", "language": "Chinese"}], "unknown"),
])
def test_language_patch_rejects_duplicate_or_unknown_line_ids(updates, expected):
    original = shot()
    with pytest.raises(ValueError, match=expected):
        apply_dialogue_update(original, {"dialogue_language_updates": updates})
    assert original.dialogue_lines[0].language == ""


def test_language_patch_requires_saved_attributed_lines():
    original = shot(lines=False)
    with pytest.raises(ValueError, match="attributed"):
        apply_dialogue_update(original, {"dialogue_language_updates": [
            {"line_id": "kira-1", "language": "Chinese"}]})
    assert original.dialogue == [WORDS]


def test_core_language_patch_rejects_mixed_word_update():
    original = shot()
    with pytest.raises(ValueError, match="dialogue_language_updates"):
        apply_dialogue_update(original, {"dialogue_language_updates": [
            {"line_id": "kira-1", "language": "Chinese"}], "dialogue": ["Different words"]})
    assert original.dialogue == [WORDS]


def test_language_patch_cannot_also_change_scene_source():
    original = shot()
    with pytest.raises(ValueError, match="dialogue_language_updates"):
        apply_dialogue_update(original, {"dialogue_language_updates": [
            {"line_id": "kira-1", "language": "Chinese"}], "scene_id": "new-scene"})
    assert original.dialogue_lines[0].source.scene_id == "scene-1"
