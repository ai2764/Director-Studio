import asyncio
import json
import pytest
import httpx

from app.config import settings
from app.core.projects.store import create_project
from app.core.projects.chat_history import append_chat_message, load_chat_history


@pytest.mark.asyncio
async def test_harness_envelope_includes_skills_once_and_summary_is_skill_free(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn
    project = create_project("envelope", "A cat waits.")
    captured = []
    async def chat_fn(system, user, **kwargs):
        captured.append((system, kwargs))
        return {"content": "done"}
    turn = BackendTurn(project.id, "hello", None, chat_fn)
    context = await turn.dispatch("context", {})
    assert context["system"].count("<DIRECTOR_SKILL>") == 1
    await turn.infer({"messages": [{"role": "user", "content": "hello"}]})
    system, kwargs = captured[-1]
    assert system == context["system"] + "\n\nPROJECT_STATE:\n" + context["state"]
    assert kwargs["prepared_system"] is True
    assert system.count("<DIRECTOR_SKILL>") == 1
    await turn.infer({"purpose": "compaction", "messages": [{"role": "user", "content": "summarize"}]})
    assert "<DIRECTOR_SKILL>" not in captured[-1][0]
    assert captured[-1][1]["prepared_system"] is True


@pytest.mark.asyncio
async def test_inference_uses_metered_envelope_and_does_not_authorize_concurrent_state(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.projects.store import save_project
    project = create_project("before", "A cat waits.")
    captured = []
    async def chat_fn(system, user, **kwargs):
        captured.append((system, kwargs))
        return {"content": "done"}
    turn = BackendTurn(project.id, "hello", None, chat_fn)
    context = await turn.dispatch("context", {})
    system = context["system"] + "\n\nPROJECT_STATE:\n" + context["state"]
    schemas = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                "parameters": {"type": "object", "properties": {}}} for tool in context["tools"]]
    save_project(project.model_copy(update={"name": "AFTER_CONCURRENT_CHANGE"}))
    await turn.infer({"system": system, "tools": schemas, "messages": [{"role": "user", "content": "hello"}]})
    assert captured[0][0] == system
    assert [t["function"] for t in captured[0][1]["tools"]] == schemas
    result = await turn.tool({"name": "set_script", "arguments": {"script": "bad"}, "call_id": "stale"})
    assert result["ok"] is False and "changed since inference" in result["error"]


@pytest.mark.asyncio
async def test_harness_reserves_space_for_locally_hydrated_images(tmp_projects_dir, monkeypatch):
    from app.agents.director import harness_runtime as runtime
    project = create_project("image budget", "")
    bodies = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def run(self, body, dispatch, on_progress):
            bodies.append(body)
            return {"reply": "done"}
    monkeypatch.setattr(runtime, "HarnessClient", Client)
    monkeypatch.setattr(settings, "director_num_ctx", 32768)
    monkeypatch.setattr(settings, "director_num_predict", 4096)
    await runtime.handle_harness_chat(project_id=project.id, message="Inspect", svc=None,
                                     user_images_b64=["IMAGE_A", "IMAGE_B"])
    assert bodies[0]["context_window"] == 24576


@pytest.mark.asyncio
async def test_focused_harness_context_reads_one_shot_on_demand(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.service import DirectorService
    from app.core.projects.models import Shot
    from app.core.projects.store import save_shot, load_shot
    project = create_project("focus", "A cat waits. A bird lands.")
    first = Shot(id="sht_cat123", scene_id="scene", duration_s=4, project_id=project.id, title="Cat", script_beat="CAT_DETAIL")
    second = Shot(id="sht_bird456", scene_id="scene", duration_s=4, project_id=project.id, title="Bird", script_beat="BIRD_DETAIL")
    save_shot(first); save_shot(second)
    turn = BackendTurn(project.id, "hello", DirectorService(plan_provider=None), None)
    overview = json.loads((await turn.dispatch("context", {}))["state"])
    assert all("script_beat" not in shot for shot in overview["shots"])
    focused = BackendTurn(project.id, first.id, DirectorService(plan_provider=None), None)
    state = json.loads(focused.context()["state"])
    assert next(s for s in state["shots"] if s["id"] == first.id)["script_beat"] == "CAT_DETAIL"
    assert "BIRD_DETAIL" not in json.dumps(state)
    result = await turn.tool({"name": "get_status", "arguments": {"shot_id": second.id}, "call_id": "read-one"})
    assert result["shot"]["script_beat"] == "BIRD_DETAIL"
    assert load_shot(project.id, first.id) == first
    assert load_shot(project.id, second.id) == second


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [[], ["native-sessions-v1"]])
async def test_old_sidecar_is_rejected_before_any_turn_is_sent(capabilities):
    from app.agents.director.harness_client import HarnessClient, HarnessError
    posted = []
    def transport(request):
        if request.method == "GET": return httpx.Response(200, json={"ok": True, "protocol": 1, "capabilities": capabilities})
        posted.append(request.method)
        return httpx.Response(200, text='{"type":"result","reply":"old","thinking":""}\n')
    async def dispatch(*args): raise AssertionError("must not invoke capabilities")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(HarnessError, match="restart"):
            await HarnessClient("http://127.0.0.1:8791", "test", http=http).run({"session_id": "stable"}, dispatch)
    assert posted == []


@pytest.mark.asyncio
async def test_stable_session_identity_and_manual_operation(tmp_projects_dir, monkeypatch):
    from app.agents.director import harness_runtime as runtime
    project = create_project("session", "A cat waits.")
    bodies = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def run(self, body, dispatch, on_progress):
            bodies.append(body)
            return {"reply": "done", "thinking": "", "compaction": {"compacted": True, "before_tokens": 9000, "after_tokens": 2000, "session_id": body.get("session_id", "")}}
    monkeypatch.setattr(runtime, "HarnessClient", Client)
    await runtime.handle_harness_chat(project_id=project.id, message="hello", svc=None)
    await runtime.handle_harness_chat(project_id=project.id, message="continue", svc=None)
    assert bodies[0].get("session_id") and bodies[0]["session_id"] == bodies[1]["session_id"]
    result = await runtime.compact_harness_chat(project_id=project.id, chat_fn=None, history=[])
    assert bodies[-1]["operation"] == "compact"
    assert bodies[-1]["session_id"] == bodies[0]["session_id"]
    assert result["after_tokens"] == 2000


@pytest.mark.asyncio
async def test_large_ui_history_is_not_resent_in_the_turn_envelope(tmp_projects_dir, monkeypatch):
    from app.agents.director import harness_runtime as runtime
    project = create_project("long history", "A cat waits.")
    history = [{"role": "user", "content": "old UI transcript " * 1000}] * 10001
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def run(self, body, dispatch, on_progress):
            assert body["history"] == []
            assert "history" not in await dispatch("context", {})
            seed = await dispatch("context", {"include_history": True})
            assert seed["history"] == history
            return {"reply": "done"}
    monkeypatch.setattr(runtime, "HarnessClient", Client)
    await runtime.handle_harness_chat(project_id=project.id, message="next", history=history, svc=None)


@pytest.mark.asyncio
async def test_summary_output_limit_reaches_provider_boundary(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn
    project = create_project("summary", "A cat waits.")
    async def chat_fn(system, user, **kwargs):
        assert kwargs["max_output_tokens"] == 1024
        assert kwargs["tools"] == []
        return {"content": "summary"}
    turn = BackendTurn(project.id, "hello", None, chat_fn)
    await turn.infer({"purpose": "compaction", "messages": [{"role": "user", "content": "old"}], "max_output_tokens": 1024})


@pytest.mark.asyncio
async def test_compact_endpoint_preserves_transcript_and_serial_admission(tmp_projects_dir, monkeypatch):
    from app.main import app
    from app.api import projects
    from app.agents.director import harness_runtime
    from app.core.projects.chat_sessions import director_chat_sessions
    project = create_project("manual", "A cat waits.")
    append_chat_message(project.id, role="user", content="Keep BLUE")
    before = load_chat_history(project.id)
    monkeypatch.setattr(settings, "director_agent_runtime", "harness")
    async def available(): pass
    async def chat_fn(**kwargs): return None
    async def compact(**kwargs):
        assert (await director_chat_sessions.snapshot(project.id)).active
        assert kwargs["history"] == [{"role": "user", "content": "Keep BLUE"}]
        return {"compacted": True, "before_tokens": 1000, "after_tokens": 250, "session_id": "native"}
    monkeypatch.setattr(projects, "_assert_chat_available", available)
    monkeypatch.setattr(projects, "_make_chat_fn", chat_fn)
    monkeypatch.setattr(harness_runtime, "compact_harness_chat", compact, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(f"/api/projects/{project.id}/chat/compact")
        assert response.status_code == 200
        assert response.json()["after_tokens"] == 250
        assert load_chat_history(project.id) == before
        assert not (await director_chat_sessions.snapshot(project.id)).active
        session = await director_chat_sessions.reserve(project.id)
        try:
            assert (await http.post(f"/api/projects/{project.id}/chat/compact")).status_code == 409
        finally:
            await director_chat_sessions.finish(project.id, session.session_id)
        monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
        assert (await http.post(f"/api/projects/{project.id}/chat/compact")).status_code == 409


@pytest.mark.asyncio
async def test_manual_compaction_rejects_business_capabilities(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn
    project = create_project("manual guard", "A cat waits.")
    turn = BackendTurn(project.id, "", None, None, compact_only=True)
    with pytest.raises(ValueError, match="compaction"):
        await turn.dispatch("tool", {"name": "append_shot", "arguments": {}, "call_id": "x"})
    with pytest.raises(ValueError, match="compaction"):
        await turn.dispatch("llm", {"purpose": "turn", "messages": [{"role": "user", "content": "run"}]})
