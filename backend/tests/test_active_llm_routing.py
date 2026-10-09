from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_bounded_prompt_call_cancels_silent_transport_at_deadline(monkeypatch):
    import asyncio

    from app.agents.director import llm_plan_provider as module

    cancelled = asyncio.Event()

    class SilentClient:
        async def chat_response(self, model, **kwargs):
            try:
                await asyncio.sleep(0.1)
                return {"content": "late reply", "finish_reason": "stop"}
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(module, "PROMPT_CALL_TIMEOUT_SEC", 0.01, raising=False)
    adapter = module.DirectorLLMPlanProvider(provider=RecordingProvider(SilentClient()))
    with pytest.raises(TimeoutError, match="timed out"):
        await adapter.complete_bounded("Review", "Candidate", max_tokens=1024)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_bounded_prompt_review_uses_provider_schema_and_rejects_truncation(monkeypatch):
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.config import settings
    monkeypatch.setattr(settings, "director_num_predict", 1024)
    requests = []

    class Client:
        finish_reason = "stop"

        async def chat_response(self, model, **kwargs):
            requests.append({"model": model, **kwargs})
            return {"content": '{"valid":true}', "finish_reason": self.finish_reason}

    client = Client()
    adapter = DirectorLLMPlanProvider(provider=RecordingProvider(client))
    schema = {"type": "object", "properties": {"valid": {"type": "boolean"}}}
    result = await adapter.complete_bounded("Review", "Candidate", max_tokens=1024, schema=schema)
    assert result == '{"valid":true}'
    assert requests[0]["format"] == schema
    assert requests[0]["options"]["num_predict"] == 1024
    assert requests[0]["model"] == "catalog-model"
    client.finish_reason = "length"
    with pytest.raises(ValueError, match="truncated"):
        await adapter.complete_bounded("Review", "Candidate", max_tokens=1024, schema=schema)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_budget", [1024, 4096, 6144])
async def test_configured_output_budget_reaches_bounded_writer_and_review(monkeypatch, stage_budget):
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.config import settings
    monkeypatch.setattr(settings, "director_num_predict", 65536)
    requests = []

    class Client:
        async def chat_response(self, model, **kwargs):
            requests.append(kwargs)
            return {"content": '{"valid":true}', "finish_reason": "stop"}

    adapter = DirectorLLMPlanProvider(provider=RecordingProvider(Client()))
    assert await adapter.complete_bounded("Review", "Candidate", max_tokens=stage_budget) == '{"valid":true}'
    assert len(requests) == 1
    assert requests[0]["options"]["num_predict"] == 65536


@pytest.mark.asyncio
async def test_bounded_writer_keeps_larger_stage_budget_with_default_config(monkeypatch):
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.config import settings
    from unittest.mock import AsyncMock
    monkeypatch.setattr(settings, "director_num_predict", 4096)
    client = SimpleNamespace(chat_response=AsyncMock(return_value={"content": "{}", "finish_reason": "stop"}))
    adapter = DirectorLLMPlanProvider(provider=RecordingProvider(client))
    assert await adapter.complete_bounded("Write", "Candidate", max_tokens=6144) == "{}"
    assert client.chat_response.call_args.kwargs["options"]["num_predict"] == 6144


@pytest.mark.asyncio
@pytest.mark.parametrize("reason_field", ["finish_reason", "done_reason"])
async def test_truncation_reports_budget_and_usage_without_response_text(monkeypatch, reason_field, caplog):
    import logging
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.config import settings
    from app.core.prompt_errors import PromptOutputTruncated
    from unittest.mock import AsyncMock
    monkeypatch.setattr(settings, "director_num_predict", 8192)
    private_text = "private response marker"
    client = SimpleNamespace(chat_response=AsyncMock(return_value={
        "content": private_text, "thinking": private_text, reason_field: "length",
        "usage": {"output_tokens": 8192, "reasoning_tokens": 7000}}))
    adapter = DirectorLLMPlanProvider(provider=RecordingProvider(client))
    with caplog.at_level(logging.INFO), pytest.raises(PromptOutputTruncated) as failure:
        await adapter.complete_bounded("Review", "Candidate", max_tokens=1024)
    assert "8192" in str(failure.value)
    assert "7000" in str(failure.value)
    assert "DS_DIRECTOR_NUM_PREDICT" in str(failure.value)
    assert "finish_reason=length" in caplog.text
    assert "output_tokens=8192" in caplog.text
    assert private_text not in caplog.text + str(failure.value)
    assert client.chat_response.await_count == 1


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

    async def context_capacity(self, model: str) -> int | None:
        return 65536 if model == "catalog-model" else None


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

    def fake_skill(task: str, *, guides=(), writer_only=False):
        assert writer_only is True
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
async def test_compaction_keeps_summary_internal_without_hiding_next_reply(monkeypatch):
    from app.api import projects as projects_api

    summary = '## Primary Request and Intent\n- Multi-shot H3 video arc "first"'
    response = {"content": summary, "thinking": "internal planning", "tool_calls": [],
                "usage": {"input_tokens": 200, "output_tokens": 30}}

    class SummaryClient(RecordingClient):
        async def chat_response(self, model, **kwargs):
            self.chat_calls.append(kwargs)
            return response

    events = []

    async def progress(event):
        events.append(event)

    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    client = SummaryClient()
    chat_fn = await projects_api._make_chat_fn(progress, provider=RecordingProvider(client))
    messages = [{"role": "user", "content": "Summarize the history"}]

    result = await chat_fn("SYSTEM", "USER", messages=messages,
                           prepared_system=True, inference_purpose="compaction")

    # Harness still needs the full summary to replace history internally.
    assert result == response
    assert not any(event["type"] in {"token", "think"} for event in events)
    assert any(event["type"] == "runtime" and event["text"] == "整理对话上下文…"
               for event in events)
    usage = [event["data"] for event in events if event["type"] == "context_usage"]
    assert [event["status"] for event in usage] == ["running", "completed"]
    assert all(event["purpose"] == "compaction" for event in usage)
    assert usage[-1]["output_tokens"] == 30

    # A normal reply can legitimately use the same heading. Filter by purpose,
    # and do not leave suppression enabled on this shared chat_fn.
    events.clear()
    assert await chat_fn("SYSTEM", "USER", messages=messages, prepared_system=True) == response
    assert {"type": "token", "text": summary} in events
    assert {"type": "think", "text": response["thinking"]} in events
    assert all(event["data"]["purpose"] == "turn"
               for event in events if event["type"] == "context_usage")


@pytest.mark.asyncio
async def test_chat_preflight_reads_local_provider_context_capacity(monkeypatch):
    from app.api import projects as projects_api

    active = RecordingProvider()
    active.provider_id = "lm-studio"
    active.lifecycle = RecordingLifecycle()
    monkeypatch.setattr(
        "app.core.vram.get_orchestrator", lambda: FakeOrchestrator()
    )

    chat_fn = await projects_api._make_chat_fn(provider=active)

    assert await chat_fn.resolve_context_capacity() == 65536


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
