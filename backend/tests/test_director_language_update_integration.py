"""Language-only agent tool uses current source metadata, never retypes speech."""
import pytest

from app.agents.director.service import DirectorService
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from test_dialogue_language_updates import SCRIPT, WORDS, line_payload, shot as make_shot


def setup_shot(*, cached=False, stale=False):
    project = create_project("Kira metadata edit", SCRIPT + (" Changed." if stale else ""))
    shot = make_shot().model_copy(update={"project_id": project.id})
    if cached:
        shot = shot.model_copy(update={"dialogue_lines": None, "meta": {
            "prompt_dialogue_contract": {"lines": [line_payload()]}}})
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    return project, shot


@pytest.mark.parametrize("cached", [False, True])
def test_language_only_tool_preserves_spoken_words_and_provenance(cached):
    project, original = setup_shot(cached=cached)
    DirectorService(plan_provider=None).revise_shot(project.id, {
        "shot_id": original.id, "dialogue_language_updates": [
            {"line_id": "kira-1", "language": "Chinese"}]})
    saved = load_shot(project.id, original.id)
    assert saved.dialogue == [WORDS]
    assert saved.dialogue_lines[0].text == WORDS
    assert saved.dialogue_lines[0].language == "Chinese"
    assert saved.dialogue_lines[0].source.model_dump() == line_payload()["source"]
    assert "prompt_dialogue_contract" not in saved.meta


def test_language_tool_rejects_stale_cached_source_without_saving():
    project, original = setup_shot(cached=True, stale=True)
    with pytest.raises(ValueError, match="stale"):
        DirectorService(plan_provider=None).revise_shot(project.id, {
            "shot_id": original.id, "dialogue_language_updates": [
                {"line_id": "kira-1", "language": "Chinese"}]})
    assert load_shot(project.id, original.id) == original


@pytest.mark.parametrize("stale", [False, True])
def test_agent_can_see_only_current_cached_line_ids_for_language_patch(stale):
    import json
    from app.agents.director.chat_context import project_context_blob
    project, original = setup_shot(cached=True, stale=stale)
    context = json.loads(project_context_blob(project, [original], message="shot 1"))
    details = context["shots"][0]
    if stale:
        assert details["dialogue_lines"] is None
    else:
        assert details["dialogue_lines"][0]["line_id"] == "kira-1"
    assert load_shot(project.id, original.id) == original
