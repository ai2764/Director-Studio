from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.core.llm.provider import UnsupportedLLMFeatureError


class RecordingClient:
    def __init__(self) -> None:
        self.generate_calls: list[tuple[str, str]] = []
        self.chat_calls: list[dict] = []

    async def generate(self, model: str, prompt: str, **kwargs) -> str:
        self.generate_calls.append((model, prompt))
        return "ok"

    async def chat(self, model: str, prompt: str, **kwargs) -> str:
        self.chat_calls.append(
            {"model": model, "prompt": prompt, **kwargs}
        )
        return "vision ok"


class RecordingProvider:
    provider_id = "openai-compatible"

    def __init__(self, client=None) -> None:
        self.client = client or RecordingClient()

    def model_status(self) -> dict:
        return {"model": "catalog-model"}


class RecordingLifecycle:
    uses_local_gpu = True

    async def status(self, model: str) -> dict:
        return {
            "provider": "lm-studio",
            "uses_local_gpu": True,
            "ready": True,
            "model": model,
            "loaded_instances": ["instance-1"],
        }


class FakeOrchestrator:
    @asynccontextmanager
    async def llm_session(self, **kwargs):
        yield

    async def ensure_llm_ready(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_director_plan_provider_uses_injected_active_provider(monkeypatch):
    from app.agents.director import llm_plan_provider as module

    composed: list[tuple[str, tuple[str, ...]]] = []

    def fake_skill(task: str, *, guides=()):
        composed.append((task, tuple(guides)))
        return "SKILLED"

    monkeypatch.setattr(module, "with_director_skill", fake_skill)
    active = RecordingProvider()
    provider = module.DirectorLLMPlanProvider(provider=active)

    result = await provider.complete(
        "SYSTEM",
        "USER",
        guides=("script-planning",),
    )

    assert result == "ok"
    assert active.client.generate_calls == [("catalog-model", "SKILLED")]
    assert composed == [("SYSTEM\n\nUSER", ("script-planning",))]


@pytest.mark.asyncio
async def test_make_chat_fn_routes_plain_chat_to_injected_provider(monkeypatch):
    from app.api import projects as projects_api

    active = RecordingProvider()
    monkeypatch.setattr(
        "app.core.vram.get_orchestrator", lambda: FakeOrchestrator()
    )

    chat_fn = await projects_api._make_chat_fn(provider=active)
    result = await chat_fn("SYSTEM", "USER")

    assert result == "ok"
    assert len(active.client.generate_calls) == 1
    assert active.client.generate_calls[0][0] == "catalog-model"
    assert active.client.generate_calls[0][1].endswith("\n\nUSER")


@pytest.mark.asyncio
async def test_make_chat_fn_retries_tools_only_for_unsupported_feature(monkeypatch):
    from app.api import projects as projects_api

    class UnsupportedToolsClient(RecordingClient):
        async def chat_response(self, *args, **kwargs):
            self.chat_calls.append(kwargs)
            raise UnsupportedLLMFeatureError(
                "tools", "endpoint does not support tool calls"
            )

    client = UnsupportedToolsClient()
    active = RecordingProvider(client)
    monkeypatch.setattr(
        "app.core.vram.get_orchestrator", lambda: FakeOrchestrator()
    )

    chat_fn = await projects_api._make_chat_fn(provider=active)
    result = await chat_fn(
        "SYSTEM",
        "Use the status tool",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_status",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert result == "ok"
    assert len(client.chat_calls) == 1
    assert len(client.generate_calls) == 1
    assert "AVAILABLE_TOOLS_JSON" in client.generate_calls[0][1]


@pytest.mark.asyncio
async def test_health_reports_active_provider_instead_of_ollama(monkeypatch):
    from app.api import health as health_api

    class HealthyClient:
        async def health(self) -> bool:
            return True

    active = RecordingProvider(HealthyClient())

    class FakeComfy:
        async def health(self):
            return {"system": {"device": "test"}}

    monkeypatch.setattr(health_api, "ComfyClient", FakeComfy)
    monkeypatch.setattr(health_api, "get_llm_provider", lambda: active)

    result = await health_api.health()

    assert result.details["llm"] == {
        "provider": "openai-compatible",
        "reachable": True,
    }
    assert "ollama_reachable" not in result.details


@pytest.mark.asyncio
async def test_vram_status_uses_active_provider_lifecycle(monkeypatch):
    from app.api import director as director_api

    active = RecordingProvider()
    active.provider_id = "lm-studio"
    active.lifecycle = RecordingLifecycle()

    class StatusOrchestrator(FakeOrchestrator):
        provider = active
        owner = "llm"
        comfy_pipeline = None
        policy = "exclusive"
        models = ["catalog-model"]
        acquire_timeout_sec = 30
        _llm_ready = True
        _waiters = 0
        last_comfy_free = None
        last_comfy_free_error = None

        async def generation_reservations(self):
            return []

    monkeypatch.setattr(director_api, "get_orchestrator", StatusOrchestrator)

    result = await director_api.vram_status()

    assert result["provider"] == "lm-studio"
    assert result["model"] == "catalog-model"
    assert result["llm_ready"] is True
    assert result["llm_runtime"]["loaded_instances"] == ["instance-1"]


@pytest.mark.asyncio
async def test_wake_context_ping_uses_active_provider_client(monkeypatch):
    from app.api import director as director_api

    active = RecordingProvider()

    class WakeOrchestrator(FakeOrchestrator):
        provider = active

    class Context:
        project_id = "prj_1"
        last_phase = "shot_planning"
        shot_summaries = [{"id": "shot_1"}]

    monkeypatch.setattr(director_api, "get_orchestrator", WakeOrchestrator)
    monkeypatch.setattr(director_api, "load_agent_context", lambda _id: Context())

    result = await director_api.wake_director(
        director_api.WakeBody(project_id="prj_1", keep=True)
    )

    assert result.model == "catalog-model"
    assert active.client.generate_calls[0][0] == "catalog-model"
