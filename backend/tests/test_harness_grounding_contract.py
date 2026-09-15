import pytest
from contextlib import asynccontextmanager
from jsonschema import Draft202012Validator

from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.service import DirectorService
from app.agents.director.tool_schema import director_tool_schemas
from app.core.projects.store import create_project, list_shots


def draft():
    return dict(scene_id="room", title="A seed", script_beat="A seed rests.",
                shot_type="close-up", camera_angle="eye level", camera_motion="push in",
                composition="seed centered", duration_s=8, dialogue=[], asset_matches=[])


class ValidationProvider:
    async def complete(self, *args, **kwargs):
        return '{"valid": true, "issues": []}'


class Orchestrator:
    @asynccontextmanager
    async def llm_session(self, **kwargs):
        yield self

    async def ensure_llm_ready(self):
        pass


def test_patch_schema_requires_indices_and_rejects_invented_roles(tmp_projects_dir):
    project = create_project("schema", "")
    schema = next(t["function"]["parameters"] for t in director_tool_schemas(project)
                  if t["function"]["name"] == "patch_shot_refs")
    validate = Draft202012Validator(schema)
    binding = dict(role="actor", asset_id="actor", file_key="master")
    payload = {"updates": [{"shot_id": "shot", "refs": [binding]}]}
    assert list(validate.iter_errors(payload))
    binding["picture_index"] = 1
    assert not list(validate.iter_errors(payload))
    binding["role"] = "actor_wardrobe"
    assert list(validate.iter_errors(payload))


@pytest.mark.parametrize("stale", [False, True])
def test_inspection_does_not_inject_planning(tmp_projects_dir, stale):
    from app.agents.director.chat_orchestrator import sanitize_tools_for_pipeline
    from app.core.projects.models import Shot
    project = create_project("Read images only", "A new story.")
    shots = [Shot(id="old", project_id=project.id, scene_id="old", title="Old", script_beat="Old beat", duration_s=6)] if stale else []
    requested = [{"name": "inspect_asset", "args": {"asset_id": "actor", "file_key": "master"}}]
    actual, notes = sanitize_tools_for_pipeline(requested, project=project, shots=shots)
    assert actual == requested
    assert not notes


def test_finish_removes_unconfirmed_storyboard_claim(tmp_projects_dir):
    project = create_project("No save", "")
    turn = BackendTurn(project.id, "Plan", DirectorService(plan_provider=ValidationProvider(), orchestrator=Orchestrator()), None)
    result = turn.finish({"reply": "Storyboard completed: created 3 shots.", "thinking": ""})
    assert "created 3 shots" not in result.reply
    assert "No storyboard save was confirmed" in result.reply


def test_terminal_failure_does_not_restore_false_claim_around_text_tool_call(tmp_projects_dir):
    project = create_project("Terminal failure", "")
    turn = BackendTurn(project.id, "Write prompts", DirectorService(plan_provider=ValidationProvider(), orchestrator=Orchestrator()), None)
    turn.terminal_failure = "Shot 2 prompt failed"
    result = turn.finish({"reply": 'Shot 2 prompt completed. <tool_call>{"name":"write_prompt"}</tool_call>'})
    assert "Shot 2 prompt completed" not in result.reply
    assert "Shot 2 prompt failed" in result.reply
    assert "not executed" in result.reply


@pytest.mark.asyncio
async def test_failed_save_budget_blocks_append_but_allows_status(tmp_projects_dir):
    project = create_project("budget", "")
    turn = BackendTurn(project.id, "Create a storyboard", DirectorService(plan_provider=ValidationProvider(), orchestrator=Orchestrator()), None)
    for i in range(3):
        await turn.dispatch("context", {})
        result = await turn.dispatch("tool", {"name": "save_storyboard", "call_id": str(i),
            "arguments": {"expected_script_hash": f"wrong-{i}", "shots": [draft()]}})
        assert not result["ok"]
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"name": "append_shot", "call_id": "bypass",
        "arguments": {"expected_script_hash": "e3b0c44298fc1c14",
                      "expected_last_shot_id": None, "shot": draft()}})
    assert not result["ok"]
    assert not list_shots(project.id)
    status = await turn.dispatch("tool", {"name": "get_status", "call_id": "status", "arguments": {}})
    assert status["ok"]


@pytest.mark.asyncio
async def test_successful_third_save_remains_usable_and_failure_is_reported(tmp_projects_dir):
    project = create_project("partial completion", "")
    turn = BackendTurn(project.id, "Create a storyboard and write prompts", DirectorService(plan_provider=ValidationProvider(), orchestrator=Orchestrator()), None)
    for i, hash_value in enumerate(["wrong-1", "wrong-2", "e3b0c44298fc1c14"]):
        await turn.dispatch("context", {})
        result = await turn.dispatch("tool", {"name": "save_storyboard", "call_id": str(i),
            "arguments": {"expected_script_hash": hash_value, "shots": [draft()]}})
    assert result["ok"]
    assert "write_prompt" in {t["function"]["name"] for t in turn.context()["tools"]}
    turn.terminal_failure = "Shot prompt failed: dialogue format invalid"
    result = turn.finish({"reply": "Everything completed.", "thinking": ""})
    assert "dialogue format invalid" in result.reply
    assert "Everything completed" not in result.reply
    assert "saved" in result.reply.lower()
