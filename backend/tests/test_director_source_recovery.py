"""Bounded model corrections keep source authority and actionable failures intact."""
import hashlib
import json

import pytest

from app.agents.director.dialogue_grounding import ground_dialogue
from app.agents.director.material_review import review_references
from app.core.projects.dialogue import DialogueContractError, DialogueLine, verify_dialogue_sources
from app.core.projects.models import Project, Shot
from app.core.prompt_errors import PromptFailureError


def context(script="Visitor: Hello."):
    project = Project(id="p1", name="Test", script_text=script, created_at="", updated_at="")
    shot = Shot(id="s1", project_id="p1", scene_id="sc1", title="Greeting",
                script_beat="A greeting.", duration_s=6, dialogue=["Hello."])
    line = DialogueLine(line_id="l1", speaker_id="visitor", speaker_name="Visitor", text="Hello.",
                        source=dict(kind="script", scene_id="sc1", quote="Visitor: Hello.",
                                    source_hash=hashlib.sha256(script.encode()).hexdigest()))
    return project, shot, line


class Responses:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, system, user, **kwargs):
        self.calls.append((system, user))
        assert self.responses, "No additional inference attempt is allowed"
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)

    async def complete_with_images(self, *args, **kwargs):
        raise AssertionError("These decision-only tests have no new images")


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_missing_script_fails_without_inference_with_explicit_source_action(structured):
    project, shot, line = context("  \n")
    if structured:
        shot = shot.model_copy(update={"dialogue_lines": [line]})
    provider = Responses({"dialogue_lines": [line.model_dump()]})
    with pytest.raises(DialogueContractError) as error:
        await ground_dialogue(project, shot, provider)
    issue = error.value.issues[0]
    assert issue.code == "dialogue_source_missing"
    assert issue.expected and issue.actual
    assert "script" in issue.action.lower() and "explicit" in issue.action.lower()
    assert provider.calls == []


@pytest.mark.parametrize("change,code", [
    ({"scene_id": "elsewhere"}, "dialogue_source_scene_mismatch"),
    ({"source_hash": "old"}, "dialogue_source_stale"),
    ({"quote": "Visitor: Goodbye."}, "dialogue_source_quote_mismatch"),
    ({"occurrence": 2}, "dialogue_source_occurrence_mismatch"),
])
def test_source_rejection_identifies_the_field_and_recovery(change, code):
    project, shot, line = context()
    line = line.model_copy(update={"source": line.source.model_copy(update=change)})
    with pytest.raises(DialogueContractError) as error:
        verify_dialogue_sources(project, shot, [line])
    issue = error.value.issues[0]
    assert issue.code == code
    assert issue.expected and issue.actual and issue.action


def test_speaker_mismatch_has_concrete_source_evidence():
    project, shot, line = context()
    line = line.model_copy(update={"speaker_name": "Another person"})
    with pytest.raises(DialogueContractError) as error:
        verify_dialogue_sources(project, shot, [line])
    issue = error.value.issues[0]
    assert issue.code == "dialogue_source_speaker_mismatch"
    assert "Another person" in issue.expected
    assert "Visitor" in issue.actual


@pytest.mark.asyncio
async def test_grounding_repairs_bad_quote_once_with_concrete_feedback():
    project, shot, line = context()
    bad = line.model_dump()
    bad["source"]["quote"] = "Visitor: Goodbye."
    provider = Responses({"dialogue_lines": [bad]}, {"dialogue_lines": [line.model_dump()]})
    assert await ground_dialogue(project, shot, provider) == [line]
    assert len(provider.calls) == 2
    assert "dialogue_source_quote_mismatch" in provider.calls[1][1]
    assert "Visitor: Goodbye." in provider.calls[1][1]
    assert shot.dialogue_lines is None


@pytest.mark.asyncio
async def test_grounding_schema_failure_is_typed_and_bounded():
    project, shot, _ = context()
    provider = Responses({"dialogue_lines": [{"text": "Hello."}]}, "not JSON")
    with pytest.raises(DialogueContractError) as error:
        await ground_dialogue(project, shot, provider)
    assert error.value.issues[0].code == "dialogue_grounding_structure"
    assert len(provider.calls) == 2
    assert "speaker_id" in provider.calls[1][1]


@pytest.mark.asyncio
async def test_grounding_explicit_unresolved_evidence_does_not_retry():
    project, shot, _ = context()
    provider = Responses({"issues": [{"code": "dialogue_source_unresolved",
                                      "evidence": "Two possible speakers", "action": "Clarify speaker"}]})
    with pytest.raises(DialogueContractError, match="Clarify speaker"):
        await ground_dialogue(project, shot, provider)
    assert len(provider.calls) == 1


def decision(**changes):
    return {"brief": None, "rewrite_prompt": True, "reason": "Use inspected references.",
            "blocking_question": None, **changes}


@pytest.mark.asyncio
async def test_material_decision_repairs_extra_prompt_sections_once():
    project, shot, _ = context()
    provider = Responses(decision(prompt_sections={"summary": "unexpected"}), decision())
    result = await review_references(provider, project, shot, [], [], "sig", lambda: None)
    assert result["decision"]["rewrite_prompt"] is True
    assert len(provider.calls) == 2
    assert "prompt_sections" in provider.calls[1][1]
    assert "extra_forbidden" in provider.calls[1][1]


@pytest.mark.asyncio
async def test_material_decision_invalid_repair_has_typed_structure_failure():
    project, shot, _ = context()
    provider = Responses(decision(prompt_sections={}), "not JSON")
    with pytest.raises(PromptFailureError) as error:
        await review_references(provider, project, shot, [], [], "sig", lambda: None)
    assert error.value.failure_kind == "candidate"
    assert "material_decision_structure" in str(error.value)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_after", [1, 2])
async def test_material_decision_repair_rechecks_source_freshness(changed_after):
    project, shot, _ = context()
    provider = Responses(decision(prompt_sections={}), decision())

    def check_current():
        if len(provider.calls) == changed_after:
            raise ValueError("Picture source changed")

    with pytest.raises(ValueError, match="Picture source changed"):
        await review_references(provider, project, shot, [], [], "sig", check_current)
    assert len(provider.calls) == changed_after
