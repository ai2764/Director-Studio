import asyncio
import copy
import json

import pytest
from task_context_fixtures import context_case
from app.config import settings
from app.core.projects.store import load_shot, save_shot
from app.agents.director.task_context_models import TaskRequest, TaskContextState
from app.agents.director.task_context_runtime import (
    present_task_tools, render_task_context, task_context_scope, current_task_context,
)


def enable(monkeypatch, project, mode="pilot"):
    monkeypatch.setattr(settings, "director_task_context_mode", mode)
    monkeypatch.setattr(settings, "director_task_context_projects", [project.id])


def test_switch_does_not_grant_new_mutations(context_case):
    project, target, _ = context_case
    allowed = [{"type": "function", "function": {"name": "write_prompt",
        "parameters": {"type": "object", "properties": {"shot_id": {"const": target.id}},
                       "required": ["shot_id"]}}}]
    state = TaskContextState(project_id=project.id, request=TaskRequest(kind="shot_prompt",
        target_shot_id=target.id), retrieved_versions={})
    for kind in ("shot_prompt", "overview"):
        state.request.kind = kind
        offered = present_task_tools(allowed, state)
        names = {item["function"]["name"] for item in offered}
        assert names == {"write_prompt", "read_task_context", "set_task_context"}
        assert next(t for t in offered if t["function"]["name"] == "write_prompt") == allowed[0]


@pytest.mark.parametrize("mode,allow", [("off", True), ("shadow", True), ("pilot", False)])
def test_nonpilot_renderer_keeps_legacy_input(context_case, monkeypatch, mode, allow):
    project, _, _ = context_case
    enable(monkeypatch, project, mode)
    if not allow:
        monkeypatch.setattr(settings, "director_task_context_projects", [])
    assert render_task_context(project, objective="Request", authority={}, legacy_state="EXACT_OLD_INPUT", max_chars=50000) == "EXACT_OLD_INPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["legacy", "harness"])
async def test_runtime_switch_read_status_final_preserves_scope_and_evidence(context_case, monkeypatch, runtime):
    from app.agents.director.chat import handle_chat
    from app.agents.director import harness_runtime
    project, target, _ = context_case
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "director_agent_runtime", runtime)
    calls = []
    sequence = [("set_task_context", {"kind": "shot_prompt", "target_shot_id": target.id}),
        ("read_task_context", {"source_key": f"script:{project.id}", "limit": 8}),
        ("get_status", {"shot_id": target.id})]
    async def inference(system, user, **kwargs):
        assert current_task_context(project.id) is not None
        calls.append((system, user, copy.deepcopy(kwargs)))
        if len(calls) <= len(sequence):
            name, args = sequence[len(calls) - 1]
            return {"content": "", "tool_calls": [{"id": str(len(calls)), "name": name, "arguments": args}]}
        return {"content": "Checked the current shot.", "tool_calls": []}
    class HarnessTransport:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, body, dispatch, on_progress):
            messages = [{"role": "user", "content": body["message"]}]
            for _ in range(4):
                await dispatch("context", {})
                result = await dispatch("llm", {"messages": messages})
                if not result.get("tool_calls"):
                    return {"reply": result["content"], "thinking": ""}
                call = result["tool_calls"][0]
                outcome = await dispatch("tool", {"name": call["name"],
                    "arguments": call["arguments"], "call_id": call["id"]})
                assert outcome["ok"], outcome
                messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": call["id"], "type": "function",
                              "function": {"name": call["name"], "arguments": call["arguments"]}}]},
                             {"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(outcome)}]
            raise AssertionError("Expected final response")
    monkeypatch.setattr(harness_runtime, "HarnessClient", HarnessTransport)
    result = await handle_chat(project_id=project.id, message="Please consider the next step.", svc=None, chat_fn=inference)
    assert len(calls) == 4
    assert "Checked" in result.reply
    assert load_shot(project.id, target.id) == target
    assert current_task_context(project.id) is None
    first = calls[0][0] + calls[0][1]
    second = calls[1][0] + calls[1][1] + json.dumps(calls[1][2].get("messages", []))
    assert '"kind": "overview"' in first
    assert '"kind": "shot_prompt"' in second
    assert target.id in second and '"source_versions"' in second
    assert "start_h3_video" not in {t["function"]["name"] for t in calls[1][2]["tools"]}


@pytest.mark.asyncio
async def test_harness_task_switch_does_not_reset_tool_budget(context_case, monkeypatch):
    from app.agents.director.harness_runtime import BackendTurn
    project, target, _ = context_case
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "harness_max_tool_calls", 2)
    state = TaskContextState(project_id=project.id, request=TaskRequest(), retrieved_versions={})
    with task_context_scope(state):
        turn = BackendTurn(project.id, "Consider next step", None, None)
        await turn.dispatch("context", {})
        for index in range(2):
            result = await turn.dispatch("tool", {"name": "set_task_context", "arguments": {"kind": "overview"}, "call_id": str(index)})
            assert result["ok"]
        denied = await turn.dispatch("tool", {"name": "read_task_context", "arguments": {"source_key": f"shot:{target.id}"}, "call_id": "third"})
        assert denied["ok"] is False and "limit" in denied["error"]
        assert len(turn.call_ids) == 2


@pytest.mark.asyncio
async def test_context_refresh_does_not_hide_ui_edit_from_harness(context_case, monkeypatch):
    from app.agents.director.harness_runtime import BackendTurn
    project, target, _ = context_case
    enable(monkeypatch, project)
    with task_context_scope(TaskContextState(project_id=project.id, request=TaskRequest(), retrieved_versions={})):
        turn = BackendTurn(project.id, "Consider next step", None, None)
        await turn.dispatch("context", {})
        save_shot(target.model_copy(update={"title": "UI changed title"}))
        await turn.dispatch("context", {})
        result = await turn.dispatch("tool", {"name": "revise_shot", "arguments": {"shot_id": target.id, "title": "Overwrite"}, "call_id": "stale"})
        assert result["ok"] is False and "changed since inference" in result["error"]
    assert load_shot(project.id, target.id).title == "UI changed title"


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["legacy", "harness"])
async def test_cancelling_runtime_cleans_scope(context_case, monkeypatch, runtime):
    from app.agents.director.chat import handle_chat
    from app.agents.director import harness_runtime
    project, _, _ = context_case
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "director_agent_runtime", runtime)
    async def cancelled(*args, **kwargs):
        assert current_task_context(project.id)
        raise asyncio.CancelledError()
    class Transport:
        def __init__(self, *args, **kwargs):
            pass
        run = cancelled
    monkeypatch.setattr(harness_runtime, "HarnessClient", Transport)
    with pytest.raises(asyncio.CancelledError):
        await handle_chat(project_id=project.id, message="Please consider the next step.", svc=None, chat_fn=cancelled)
    assert current_task_context(project.id) is None


def test_context_queries_do_not_inject_storyboard_work(context_case):
    from app.agents.director.chat_orchestrator import sanitize_tools_for_pipeline
    project, _, _ = context_case
    requested = [{"name": "read_task_context", "args": {"source_key": "catalog:shots"}}]
    safe, notes = sanitize_tools_for_pipeline(requested, project=project, shots=[])
    assert safe == requested and notes == []


@pytest.mark.asyncio
async def test_shadow_keeps_same_legacy_request_and_call_count(context_case, monkeypatch):
    from app.agents.director.chat import handle_chat
    project, target, _ = context_case
    monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
    requests = []
    async def inference(system, user, **kwargs):
        requests.append((system, user, copy.deepcopy(kwargs)))
        return {"content": "Consider the shot's camera movement.", "tool_calls": []}
    for mode in ("off", "shadow"):
        enable(monkeypatch, project, mode)
        await handle_chat(project_id=project.id, message="Please consider the next step.", svc=None, chat_fn=inference)
    assert len(requests) == 2 and requests[0] == requests[1]
    assert load_shot(project.id, target.id) == target


def test_pilot_does_not_unseal_upload_or_terminal_tools(context_case, monkeypatch):
    from app.agents.director.harness_runtime import BackendTurn
    project, _, _ = context_case
    enable(monkeypatch, project)
    with task_context_scope(TaskContextState(project_id=project.id, request=TaskRequest(), retrieved_versions={})):
        turn = BackendTurn(project.id, "Consider next step", None, None, images=["image"])
        names = {t["function"]["name"] for t in turn.context()["tools"]}
        assert names == {"classify_chat_image"}
        turn.terminal_failure = "Existing bounded prompt failure"
        assert turn.context()["tools"] == []


def test_packet_budget_reserves_system_history_tools_output_and_images(monkeypatch):
    from app.agents.director.task_context_runtime import available_packet_chars
    monkeypatch.setattr(settings, "director_num_predict", 1000)
    available = available_packet_chars(system="S" * 1000,
        messages=[{"role": "user", "content": "H" * 1000}], tools=[], image_count=1, context_capacity=4000)
    assert 0 < available < (4000 - 1000 - 2048) * 4 - 2000
    assert available_packet_chars(system="S" * 100000, messages=[], tools=[], context_capacity=4000) == 0


def test_packet_budget_does_not_count_base64_as_text():
    from app.agents.director.task_context_runtime import available_packet_chars
    plain = [{"role": "user", "content": "Inspect this"}]
    media = [{**plain[0], "images": ["BASE64" * 100000]}]
    assert available_packet_chars(system="S", messages=plain, tools=[], image_count=1) == available_packet_chars(
        system="S", messages=media, tools=[], image_count=1)
