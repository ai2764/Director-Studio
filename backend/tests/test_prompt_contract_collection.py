"""Independent protocol defects reach the same bounded repair request."""
import json

import pytest

from app.agents.director import dialogue_preflight
from app.core.h3.dialogue_binding import compile_dialogue_draft
from app.core.prompt_errors import PromptFailureError
from test_dialogue_serialization import draft, line
from test_director_material_review import material_shot, Orchestrator, Provider, sections


def validate(raw, **bindings):
    collector = getattr(dialogue_preflight, "collect_prompt_contract_errors", None)
    assert callable(collector), "Collect dialogue/schema and independent Picture defects together"
    with collector(raw, **bindings):
        return compile_dialogue_draft(dialogue_preflight.parse_dialogue_draft(raw), [line()])


def test_malformed_binding_and_picture_syntax_reach_one_repair():
    candidate = {"prompt_sections": draft("The visitor (Picture 1) turns. {{speech:l1}}").prompt_sections.model_dump(),
                 "dialogue_uses": [dict(line_id="l1", speaker_id="p1", block_indexes=[0])]}
    raw = json.dumps(candidate)
    with pytest.raises(PromptFailureError) as caught:
        validate(raw, required_picture_indices=[1], submitted_picture_indices=[1])
    error = caught.value
    assert error.failure_kind == "contract"
    assert "line_ids" in str(error)
    assert "<Picture 1>" in str(error)
    assert {issue.code for issue in error.issues} == {"prompt_schema_invalid", "picture_binding_invalid"}


def test_wrong_speaker_and_layout_defect_preserve_typed_dialogue_issue():
    candidate = draft("{{speech:l1}}", [dict(line_ids=["l1"], speaker_id="p2", block_indexes=[0])])
    with pytest.raises(PromptFailureError) as caught:
        validate(candidate.model_dump_json(), required_layout_indices=[2], submitted_picture_indices=[2])
    issues = caught.value.issues
    assert issues[0].code == "dialogue_speaker_mismatch"
    assert issues[0].line_id == "l1"
    assert issues[0].expected == "p1" and issues[0].actual == "p2"
    assert "selected Layout" in str(caught.value)
    assert "<Picture 2>" in str(caught.value)


def test_unknown_picture_remains_rejected_alongside_unknown_dialogue():
    raw = json.dumps({"prompt_sections": draft("<Picture 9> {{speech:other}}").prompt_sections.model_dump()})
    with pytest.raises(PromptFailureError) as caught:
        validate(raw, submitted_picture_indices=[1])
    assert "dialogue_unknown_line" in str(caught.value)
    assert "unsubmitted Picture tags: <Picture 9>" in str(caught.value)


def test_valid_placeholder_candidate_compiles_without_rewriting_prose():
    detail = "A reflection (Picture 1) fades beside <Picture 1>. {{speech:l1}} Hold."
    raw = json.dumps({"prompt_sections": draft(detail).prompt_sections.model_dump()})
    result = validate(raw, required_picture_indices=[1], submitted_picture_indices=[1])
    assert result.prompt_sections.detailed_description == (
        "A reflection (Picture 1) fades beside <Picture 1>. "
        "(p1: Visitor) <d>[Português] Olá… atenção: vem!</d> Hold.")
    assert result.dialogue_uses[0].speaker_id == "p1"


def test_unreadable_json_retains_original_failure_without_invented_picture_defects():
    with pytest.raises(ValueError) as caught:
        validate("not json", required_picture_indices=[1], submitted_picture_indices=[1])
    assert "missing required Picture" not in str(caught.value)


@pytest.mark.asyncio
async def test_ordinary_writer_repairs_metadata_and_picture_tags_in_its_single_repair(material_shot):
    from app.agents.director.service import DirectorService
    from app.core.projects.store import load_shot
    project, shot, _, _ = material_shot
    orch = Orchestrator()

    class MalformedThenRepairProvider(Provider):
        writer_requests = []

        async def complete(self, system, user, **kwargs):
            if "reference review decision" in system.lower():
                return await super().complete(system, user, **kwargs)
            self.writer_requests.append(user)
            payload = sections()
            payload["detailed_description"] = "Camera arcs slowly. {{speech:l1}} Hold the empty doorway."
            if len(self.writer_requests) == 1 or "missing required Picture binding: <Picture 1>" not in user:
                payload["subject_definitions"] = payload["subject_definitions"].replace("<", "(").replace(">", ")")
            response = {"prompt_sections": payload}
            if len(self.writer_requests) == 1:
                response["dialogue_uses"] = [dict(line_id="l1", speaker_id="char_1", block_indexes=[0])]
            return json.dumps(response)

    provider = MalformedThenRepairProvider(orch)
    saved = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert len(provider.writer_requests) == 2
    assert "line_ids" in provider.writer_requests[1]
    assert "missing required Picture binding: <Picture 9>" in provider.writer_requests[1]
    assert saved.prompt_sections.detailed_description == (
        "Camera arcs slowly. (char_1: watchmaker) <d>[English] Hello.</d> Hold the empty doorway.")
    assert saved.dialogue == shot.dialogue
    assert load_shot(project.id, shot.id).prompt_sections == saved.prompt_sections
