"""Regression coverage for the kid project's recovery failures."""
import json

import pytest

from app.agents.director.chat import _run_tools
from app.agents.director.chat_orchestrator import _StoryboardSubmissionBudget
from app.agents.director.context_io import load_agent_context
from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.service import DirectorService, _script_hash
from app.core.projects.store import create_project, list_shots, load_project, save_project, save_shot
from app.core.projects.models import Shot
from test_harness_grounding_contract import Orchestrator, draft


class VerdictProvider:
    def __init__(self, verdict):
        self.verdict = verdict

    async def complete(self, *args, **kwargs):
        return json.dumps(self.verdict)


def service(verdict):
    return DirectorService(plan_provider=VerdictProvider(verdict), orchestrator=Orchestrator())


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["harness", "legacy"])
async def test_failed_storyboard_cannot_rewrite_its_acceptance_source(tmp_projects_dir, runtime):
    project = create_project("keep intent", "A child enters a vehicle, then it drives away.")
    svc = service({"valid": False, "issues": ["The entrance beat is missing."]})
    save_args = {"expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}
    rewrite = {"script": "A child is already seated in a moving vehicle."}
    if runtime == "harness":
        turn = BackendTurn(project.id, "15s", svc, None)
        rejected = await turn.tool({"name": "save_storyboard", "arguments": save_args, "call_id": "save"})
        assert rejected["ok"] is False
        blocked = await turn.tool({"name": "set_script", "arguments": rewrite, "call_id": "escape"})
        offered = {t["function"]["name"] for t in turn.context()["tools"]}
        assert "set_script" not in offered
    else:
        budget, payloads = _StoryboardSubmissionBudget(), []
        await _run_tools(project_id=project.id, tools=[{"name": "save_storyboard", "args": save_args}],
                         svc=svc, actions=[], storyboard_budget=budget, result_payloads=payloads)
        assert payloads[-1]["ok"] is False
        await _run_tools(project_id=project.id, tools=[{"name": "set_script", "args": rewrite}],
                         svc=svc, actions=[], storyboard_budget=budget, result_payloads=payloads)
        blocked = payloads[-1]
    assert load_project(project.id).script_text == project.script_text
    assert not list_shots(project.id)
    assert blocked["ok"] is False


@pytest.mark.asyncio
async def test_new_turn_still_allows_requested_script_edit(tmp_projects_dir):
    project = create_project("new authoring turn", "Original story.")
    failed = BackendTurn(project.id, "Make a storyboard", service({"valid": False, "issues": ["Missing action."]}), None)
    rejected = await failed.tool({"name": "save_storyboard", "call_id": "save", "arguments": {
        "expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}})
    assert rejected["ok"] is False
    turn = BackendTurn(project.id, "Rewrite the script", service({"valid": True, "issues": []}), None)
    result = await turn.tool({"name": "set_script", "arguments": {"script": "New story."}, "call_id": "edit"})
    assert result["ok"] is True
    assert load_project(project.id).script_text == "New story."
    assert load_project(project.id).script_locked is False


@pytest.mark.asyncio
async def test_successful_save_does_not_lock_user_authoring(tmp_projects_dir):
    project = create_project("author can change direction", "A seed rests.")
    turn = BackendTurn(project.id, "Save the storyboard then update the script", service({"valid": True, "issues": []}), None)
    saved = await turn.tool({"name": "save_storyboard", "call_id": "save", "arguments": {
        "expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}})
    assert saved["ok"] is True
    changed = await turn.tool({"name": "set_script", "call_id": "edit", "arguments": {"script": "A seed sprouts."}})
    assert changed["ok"] is True
    assert load_project(project.id).script_text == "A seed sprouts."


@pytest.mark.asyncio
async def test_generation_risk_is_saved_and_reported_without_changing_story(tmp_projects_dir):
    project = create_project("creative risk", "A seed rests.")
    warning = "The continuous push-in may produce unstable scale; this is a generation risk, not a contradiction."
    turn = BackendTurn(project.id, "Keep the push-in", service({"valid": True, "issues": [], "warnings": [warning]}), None)
    result = await turn.tool({"name": "save_storyboard", "call_id": "save", "arguments": {
        "expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}})
    assert result["ok"] is True, result
    assert list_shots(project.id)[0].camera_motion == "push in"
    assert load_project(project.id).script_text == "A seed rests."
    assert result["warnings"] == [warning]
    assert load_agent_context(project.id).extra["storyboard_review"]["warnings"] == [warning]


@pytest.mark.asyncio
async def test_warning_does_not_override_real_requirement_failure(tmp_projects_dir):
    project = create_project("hard requirement", "A seed must remain visible.")
    turn = BackendTurn(project.id, "Keep the seed visible", service({"valid": False,
        "issues": ["The required seed is absent."], "warnings": ["Motion may be difficult."]}), None)
    result = await turn.tool({"name": "save_storyboard", "call_id": "save", "arguments": {
        "expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}})
    assert result["ok"] is False
    assert "The required seed is absent." in result["error"]
    assert not list_shots(project.id)


@pytest.mark.asyncio
async def test_many_advisories_cannot_make_a_valid_candidate_fail(tmp_projects_dir):
    project = create_project("many risks", "A seed rests.")
    warnings = [f"Generation risk {index}" for index in range(17)]
    turn = BackendTurn(project.id, "Plan", service({"valid": True, "issues": [], "warnings": warnings}), None)
    result = await turn.tool({"name": "save_storyboard", "call_id": "save", "arguments": {
        "expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}})
    assert result["ok"] is True, result
    assert result["warnings"] == warnings


@pytest.mark.asyncio
async def test_replacement_reports_risks_before_user_confirms(tmp_projects_dir):
    from app.agents.director.tool_handlers.project import _load_storyboard_replacement
    project = create_project("preview risk", "A seed rests.")
    old = Shot(id="existing", project_id=project.id, scene_id="room", title="Old",
               script_beat="A seed rests.", duration_s=5)
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    warning = "A moving camera may lose the seed; generation risk only."
    turn = BackendTurn(project.id, "Replace the storyboard", service({"valid": True, "issues": [], "warnings": [warning]}), None)
    result = await turn.tool({"name": "save_storyboard", "call_id": "propose", "arguments": {
        "expected_script_hash": _script_hash(project.script_text), "shots": [draft()]}})
    assert result["confirmation_required"] is True
    assert result["warnings"] == [warning]
    assert warning in result["reply"]
    assert _load_storyboard_replacement(project.id)["warnings"] == [warning]
    assert list_shots(project.id) == [old]
