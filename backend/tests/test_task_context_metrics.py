import json
from types import SimpleNamespace

import pytest

from task_context_fixtures import context_case
from app.config import settings
from app.core.projects.store import save_project
from app.core.projects.models import ProjectMode
from app.agents.director import context_metrics as metrics
from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider


def test_metrics_count_envelope_without_persisting_content():
    shape = metrics.request_shape(path="writer", messages=[
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "private-script-秘密"},
    ], tools=[], image_count=2, source_keys=["shot:sht_target"])
    assert shape["message_count"] == 2
    assert shape["text_chars"] == len("SYSTEMprivate-script-秘密")
    assert shape["image_count"] == 2
    assert shape["measurement"] == "characters_not_tokens"
    assert "private-script" not in str(shape)
    assert "秘密" not in str(shape)


@pytest.mark.parametrize("mode,allowed,project_mode,expected", [
    ("off", True, "director", 0), ("shadow", False, "director", 0),
    ("pilot", True, "mv", 0), ("shadow", True, "director", 3),
    ("pilot", True, "director", 3),
])
@pytest.mark.asyncio
async def test_final_writer_envelopes_are_gated_and_include_guides(
    context_case, monkeypatch, mode, allowed, project_mode, expected,
):
    project, _, _ = context_case
    save_project(project.model_copy(update={"mode": ProjectMode(project_mode)}))
    monkeypatch.setattr(settings, "director_task_context_mode", mode)
    monkeypatch.setattr(settings, "director_task_context_projects", [project.id] if allowed else [])
    shapes, calls = [], []
    monkeypatch.setattr(metrics, "record_request_shape", shapes.append)
    async def generate(model, prompt):
        calls.append(prompt)
        return "ok"
    async def chat(model, prompt, **kwargs):
        assert kwargs["images"] == ["PRIVATE_BASE64"]
        calls.append(prompt)
        return "ok"
    async def response(model, **kwargs):
        calls.append(kwargs["messages"][0]["content"])
        return {"content": "ok"}
    provider = DirectorLLMPlanProvider(SimpleNamespace(client=SimpleNamespace(
        generate=generate, chat=chat, chat_response=response)), model="fake")
    with metrics.metrics_scope(project.id):
        await provider.complete("SYSTEM", "USER", guides=("h3-prompt-writing",))
        await provider.complete_with_images("SYSTEM", "USER", images=["PRIVATE_BASE64"])
        await provider.complete_bounded("SYSTEM", "USER", max_tokens=100)
    assert len(calls) == 3
    assert len(shapes) == expected
    if expected:
        assert [s["text_chars"] for s in shapes] == list(map(len, calls))
        assert [s["image_count"] for s in shapes] == [0, 1, 0]
        assert "PRIVATE_BASE64" not in json.dumps(shapes)


def test_metrics_logger_failure_does_not_break_inference(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("log unavailable")
    monkeypatch.setattr(metrics.logger, "info", fail)
    metrics.record_request_shape(metrics.request_shape(
        path="writer", messages=[], tools=[], image_count=0, source_keys=[]))


@pytest.mark.asyncio
async def test_chat_metrics_measure_final_messages_and_tools(context_case, monkeypatch):
    from app.api import projects as projects_api
    from test_active_llm_routing import FakeOrchestrator, RecordingProvider
    project, _, _ = context_case
    monkeypatch.setattr(settings, "director_task_context_mode", "shadow")
    monkeypatch.setattr(settings, "director_task_context_projects", [project.id])
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    shapes, calls = [], []
    monkeypatch.setattr(metrics, "record_request_shape", shapes.append)
    async def response(model, **kwargs):
        calls.append(kwargs)
        return {"content": "ok"}
    provider = RecordingProvider(SimpleNamespace(chat_response=response))
    chat = await projects_api._make_chat_fn(provider=provider)
    tool = {"type": "function", "function": {"name": "get_status", "parameters": {"type": "object"}}}
    with metrics.metrics_scope(project.id):
        await chat("SYS", "ignored", messages=[{"role": "user", "content": "LATEST", "images": ["PRIVATE"]}], tools=[tool])
    assert len(calls) == len(shapes) == 1
    assert shapes[0]["text_chars"] == sum(len(m["content"]) for m in calls[0]["messages"])
    assert shapes[0]["tool_schema_chars"] == len(json.dumps([tool], ensure_ascii=False, sort_keys=True))
    assert shapes[0]["image_count"] == 1
    assert "PRIVATE" not in json.dumps(shapes)


def test_final_request_observation_includes_active_source_manifest(context_case, monkeypatch):
    from app.agents.director.task_context_runtime import task_context_scope
    from app.agents.director.task_context_models import TaskContextState, TaskRequest
    project, target, _ = context_case
    monkeypatch.setattr(settings, "director_task_context_mode", "pilot")
    monkeypatch.setattr(settings, "director_task_context_projects", [project.id])
    shapes = []
    monkeypatch.setattr(metrics, "record_request_shape", shapes.append)
    state = TaskContextState(project_id=project.id, request=TaskRequest(), retrieved_versions={},
                             source_keys=[f"shot:{target.id}"])
    with task_context_scope(state), metrics.metrics_scope(project.id):
        metrics.observe_request("writer.generate", [{"role": "user", "content": "private prose"}])
    assert shapes[0]["source_keys"] == [f"shot:{target.id}"]
