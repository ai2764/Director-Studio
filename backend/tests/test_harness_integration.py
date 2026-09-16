"""Real Node Harness + Python tool/provider boundary; no network model or GPU."""
from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import httpx
import pytest

from app.config import settings
from app.core.projects.models import Shot
from app.core.projects.store import create_project, load_shot, save_project, save_shot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def real_sidecar(tmp_path_factory):
    node = shutil.which("node")
    if not node or not (ROOT / "harness/node_modules/tsx/package.json").exists():
        pytest.skip("Run npm ci in harness/ to enable real sidecar integration tests")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = "integration-test-only-token"
    env = {**os.environ, "DS_HARNESS_PORT": str(port), "DS_HARNESS_INTERNAL_TOKEN": token,
           "DS_HARNESS_SESSION_ROOT": str(tmp_path_factory.mktemp("harness-sessions"))}
    process = subprocess.Popen(
        [node, "--import", "tsx", "src/server.ts"], cwd=ROOT / "harness", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(trust_env=False, timeout=0.5) as http:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail(process.stderr.read().decode(errors="replace"))
                try:
                    response = http.get(url + "/health", headers={"Authorization": f"Bearer {token}"})
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Real Harness did not start")
        yield url, token
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


class LeaseOrchestrator:
    """Exercise the real API chat_fn residency boundary with a fake provider."""
    def __init__(self, client):
        self.provider = self
        self.client = client
        self.active = 0
        self.released = 0

    def model_status(self):
        return {"model": "isolated-test-model"}

    @asynccontextmanager
    async def llm_session(self, **kwargs):
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self.released += 1

    async def ensure_llm_ready(self, **kwargs):
        pass


@pytest.mark.asyncio
async def test_native_manual_summary_survives_python_node_roundtrip(real_sidecar, tmp_projects_dir, monkeypatch):
    from app.api.projects import _make_chat_fn
    from app.agents.director.harness_runtime import compact_harness_chat, handle_harness_chat
    from app.core import vram
    project = create_project("persistent manual summary", "A cat waits.")
    calls = []
    class Provider:
        async def chat_response(self, model, *, messages, **kwargs):
            compact = "Summarize conversation history concisely." in messages[0]["content"]
            calls.append("compact" if compact else "turn")
            if compact:
                assert kwargs["options"]["num_predict"] == settings.director_num_predict
                return {"content": "The confirmed choice is BLUE.", "done_reason": "stop", "usage": {"input_tokens": 12000, "output_tokens": 20}}
            assert "The confirmed choice is BLUE." in json.dumps(messages)
            assert "STALE_REIMPORT" not in json.dumps(messages)
            return {"content": "Ready to continue.", "done_reason": "stop", "usage": {"input_tokens": 3000, "output_tokens": 10}}
    monkeypatch.setattr(vram, "get_orchestrator", lambda: LeaseOrchestrator(Provider()))
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Archive {i}: " + "resolved detail " * 60} for i in range(60)]
    result = await compact_harness_chat(project_id=project.id, history=history, chat_fn=await _make_chat_fn())
    assert result["compacted"] and result["after_tokens"] < result["before_tokens"]
    response = await handle_harness_chat(project_id=project.id, message="hello", svc=None,
        history=[{"role": "user", "content": "STALE_REIMPORT"}], chat_fn=await _make_chat_fn())
    assert response.reply == "Ready to continue."
    assert calls == ["compact", "turn"]


@pytest.mark.asyncio
async def test_real_harness_preserves_usage_from_compaction_and_truncated_turn(real_sidecar, tmp_projects_dir, monkeypatch):
    from app.api.projects import _make_chat_fn
    from app.agents.director.harness_runtime import handle_harness_chat
    from app.agents.director.harness_client import HarnessError
    from app.core import vram

    events = []

    async def progress(event):
        events.append(event)

    class Provider:
        async def chat_response(self, model, *, messages, **kwargs):
            if "Summarize conversation history concisely." in messages[0]["content"]:
                return {"content": "Previous discussions are resolved. Answer the current greeting.",
                        "tool_calls": [], "done_reason": "stop", "usage": {"input_tokens": 19000, "output_tokens": 100}}
            return {"content": "", "thinking": "Unfinished reasoning", "tool_calls": [],
                    "done_reason": "length", "usage": {"input_tokens": 24000, "output_tokens": 4096}}

    monkeypatch.setattr(vram, "get_orchestrator", lambda: LeaseOrchestrator(Provider()))
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    project = create_project("usage during incomplete turn", "A cat waits.")
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "resolved discussion " * 50} for i in range(162)]
    with pytest.raises(HarnessError, match="INCOMPLETE_TURN"):
        await handle_harness_chat(project_id=project.id, message="hello", history=history, svc=None,
                                  chat_fn=await _make_chat_fn(on_progress=progress), on_progress=progress,
                                  context_capacity=32768)
    calls = [event["data"] for event in events if event["type"] == "context_usage"]
    assert [(call["purpose"], call["status"]) for call in calls] == [
        ("compaction", "running"), ("compaction", "completed"),
        ("turn", "running"), ("turn", "output_truncated"),
    ]
    assert calls[-1]["input_tokens"] == 24000
    assert calls[-1]["output_tokens"] == 4096


@pytest.mark.asyncio
async def test_backend_runtime_checks_its_own_sidecar_token(real_sidecar, monkeypatch):
    from app.api.director import director_runtime

    monkeypatch.setattr(settings, "director_agent_runtime", "harness")
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", "wrong-token")
    assert (await director_runtime(check_sidecar=True))["sidecar_ready"] is False
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    assert (await director_runtime(check_sidecar=True))["sidecar_ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_limit, saved_count", [(None, 24), (1, 1)])
async def test_real_harness_batch_has_separate_step_and_tool_budgets(
    real_sidecar, tmp_projects_dir, monkeypatch, tool_limit, saved_count,
):
    from app.config import Settings
    from app.agents.director import harness_runtime
    from app.agents.director.service import DirectorService

    options = {} if tool_limit is None else {"harness_max_tool_calls": tool_limit}
    monkeypatch.setattr(harness_runtime, "settings", Settings(
        _env_file=None, harness_max_steps=2, harness_base_url=real_sidecar[0],
        harness_internal_token=real_sidecar[1], **options,
    ))
    project = create_project("24 shot batch", "A cat waits, then leaves.")
    shots = [Shot(id=f"sht_budget_{i:02}", project_id=project.id, scene_id="sc01", title=f"shot {i}", script_beat="cat waits", duration_s=5) for i in range(25)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [s.id for s in shots]}))
    before = [s.model_dump() for s in shots]
    model_calls = 0

    async def inference(system, user, **kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return {"content": "", "tool_calls": [
                {"name": "revise_shot", "arguments": {"shot_id": shot.id, "title": f"Changed {i}"}}
                for i, shot in enumerate(shots[:24])
            ]}
        assert model_calls == 2
        results = [json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool"]
        assert len(results) == 24
        assert sum(r["ok"] for r in results) == saved_count
        assert all("limit" in r["error"].lower() for r in results if not r["ok"])
        return {"content": f"Saved {saved_count} requested titles.", "tool_calls": []}

    result = await harness_runtime.handle_harness_chat(
        project_id=project.id, message="Change the first 24 titles; leave the last shot alone.",
        svc=DirectorService(plan_provider=None), chat_fn=inference,
    )
    assert model_calls == 2
    assert len(result.actions) == saved_count
    assert [s.id for s in result.shots] == [s.id for s in shots]
    for index, shot in enumerate(result.shots):
        if index < saved_count:
            assert shot.title == f"Changed {index}"
            assert shot.script_beat == "cat waits" and shot.duration_s == 5
        else:
            assert shot.model_dump() == before[index]


@pytest.mark.asyncio
async def test_real_harness_repairs_tool_then_edits_one_shot(real_sidecar, tmp_projects_dir, monkeypatch):
    from app.api.projects import _make_chat_fn
    from app.agents.director.harness_runtime import handle_harness_chat
    from app.agents.director.service import DirectorService
    from app.core import vram

    project = create_project("Harness roundtrip", "A cat waits, then leaves.")
    shots = [Shot(id=f"sht_roundtrip_{i}", project_id=project.id, scene_id="sc01", title=f"shot {i}", script_beat="cat waits", duration_s=5) for i in range(2)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [s.id for s in shots]}))
    neighbor = load_shot(project.id, shots[1].id).model_dump()
    calls = []

    class Provider:
        async def chat_response(self, model, *, messages, **kwargs):
            calls.append(messages)
            assert model == "isolated-test-model"
            assert messages[0]["role"] == "system" and "PROJECT_STATE" in messages[0]["content"]
            assert any(m.get("content") == "Keep the second shot unchanged." for m in messages)
            if len(calls) == 1:
                return {"content": "", "tool_calls": [{"name": "revise_shot", "arguments": {"title": "missing shot id"}}]}
            if len(calls) == 2:
                assert any(m["role"] == "tool" for m in messages)
                return {"content": "", "tool_calls": [{"name": "revise_shot", "arguments": {"shot_id": shots[0].id, "title": "The waiting cat", "duration_s": "7"}}]}
            assert len(calls) == 3
            assistant_calls = [c for m in messages for c in m.get("tool_calls", [])]
            assert isinstance(assistant_calls[-1]["function"]["arguments"], dict)
            assert any(m.get("tool_name") == "revise_shot" for m in messages)
            return {"content": "Updated the first title.", "thinking": "", "tool_calls": []}

    orch = LeaseOrchestrator(Provider())
    monkeypatch.setattr(vram, "get_orchestrator", lambda: orch)
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    result = await handle_harness_chat(
        project_id=project.id, message="Rename the first shot to The waiting cat and set its duration to 7 seconds",
        history=[{"role": "user", "content": "Keep the second shot unchanged."}, {"role": "assistant", "content": "Understood."}],
        svc=DirectorService(plan_provider=None), chat_fn=await _make_chat_fn(),
    )
    assert result.reply == "Updated the first title."
    assert load_shot(project.id, shots[0].id).title == "The waiting cat"
    assert load_shot(project.id, shots[0].id).duration_s == 7.0
    assert load_shot(project.id, shots[1].id).model_dump() == neighbor
    assert len(result.actions) == 1 and len(calls) == 3
    assert orch.active == 0 and orch.released == 3


@pytest.mark.asyncio
async def test_real_harness_cancel_releases_provider_lease(real_sidecar, tmp_projects_dir, monkeypatch):
    from app.api.projects import _make_chat_fn
    from app.agents.director.harness_runtime import handle_harness_chat
    from app.core import vram

    project = create_project("cancel roundtrip", "")
    started = asyncio.Event()

    class Provider:
        async def chat_response(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

    orch = LeaseOrchestrator(Provider())
    monkeypatch.setattr(vram, "get_orchestrator", lambda: orch)
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    task = asyncio.create_task(handle_harness_chat(project_id=project.id, message="hello", svc=None, chat_fn=await _make_chat_fn()))
    try:
        await asyncio.wait_for(started.wait(), 10)
        assert orch.active == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert orch.active == 0 and orch.released == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
