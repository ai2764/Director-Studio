"""Observe real request boundaries without making model or project mutations."""
from contextlib import asynccontextmanager

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("counts, expected", [
    ({"prompt_eval_count": 28939, "eval_count": 4096}, {"input_tokens": 28939, "output_tokens": 4096}),
    ({"prompt_eval_count": 0, "eval_count": 0}, {"input_tokens": 0, "output_tokens": 0}),
    ({}, None),
])
async def test_ollama_preserves_reported_usage_including_truncated_output(monkeypatch, counts, expected):
    from app.core.vram import ollama_client

    class Client:
        def __init__(self, **kwargs):
            pass

        async def chat(self, **kwargs):
            return {"message": {"content": "", "thinking": "unfinished", "tool_calls": []}, "done_reason": "length", **counts}

    monkeypatch.setattr(ollama_client, "AsyncClient", Client)
    result = await ollama_client.OllamaClient().chat_response("test", messages=[{"role": "user", "content": "hello"}])
    assert result.get("usage") == expected
    assert result["done_reason"] == "length"


@pytest.mark.asyncio
async def test_openai_usage_preserves_reasoning_subset():
    from app.core.llm.openai_compatible import OpenAICompatibleClient

    def handler(request):
        return httpx.Response(200, json={
            "id": "usage-test", "object": "chat.completion", "created": 1, "model": "test",
            "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": ""}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180,
                      "completion_tokens_details": {"reasoning_tokens": 60}},
        })

    client = OpenAICompatibleClient(base_url="http://127.0.0.1:1234/v1", api_key=None, transport=httpx.MockTransport(handler))
    try:
        result = await client.chat_response("test", messages=[{"role": "user", "content": "hello"}])
        assert result["usage"] == {"input_tokens": 100, "output_tokens": 80, "reasoning_tokens": 60}
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["length", "stop", "overflow", "timeout"])
@pytest.mark.parametrize("prepared", [False, True])
async def test_chat_emits_full_envelope_estimate_then_actual_usage_or_failure(monkeypatch, outcome, prepared):
    from app.api import projects
    from app.core import vram

    events = []

    async def progress(event):
        events.append(event)

    class Client:
        async def chat_response(self, model, **kwargs):
            # Estimate must reach the user before the slow network call.
            assert events[-1]["type"] == "context_usage"
            assert events[-1]["data"]["status"] == "running"
            assert kwargs["messages"][0]["content"] == ("live skill instructions\nSYSTEM" if not prepared else "PREPARED SYSTEM INSTRUCTIONS")
            if outcome == "overflow":
                raise ValueError("maximum context length exceeded")
            if outcome == "timeout":
                raise httpx.ReadTimeout("read timed out")
            return {"content": "partial" if outcome == "length" else "done", "thinking": "reasoning",
                    "tool_calls": [], "done_reason": outcome,
                    "usage": {"input_tokens": 28939, "output_tokens": 4096}}

    class Orchestrator:
        provider_id = "local-test-provider"
        client = Client()
        class Lifecycle:
            uses_local_gpu = True

            async def context_capacity(self, model):
                return 32768 if model == "test-qwen" else None

        lifecycle = Lifecycle()

        def model_status(self):
            return {"model": "test-qwen"}

        @asynccontextmanager
        async def llm_session(self, **kwargs):
            yield

        async def ensure_llm_ready(self, **kwargs):
            pass

    orch = Orchestrator()
    orch.provider = orch
    monkeypatch.setattr(vram, "get_orchestrator", lambda: orch)
    monkeypatch.setattr(projects, "with_director_skill", lambda system, **kwargs: "live skill instructions\n" + system)
    monkeypatch.setattr(projects.settings, "director_num_predict", 4096)
    chat = await projects._make_chat_fn(on_progress=progress)
    args = dict(messages=[{"role": "user", "content": "hello", "images": ["SECRET_IMAGE_BYTES" * 5000]}],
                tools=[{"type": "function", "function": {"name": "status", "parameters": {"type": "object"}}}],
                inference_purpose="compaction", prepared_system=prepared)
    system = "PREPARED SYSTEM INSTRUCTIONS" if prepared else "SYSTEM"
    if outcome in {"overflow", "timeout"}:
        with pytest.raises((ValueError, httpx.ReadTimeout)):
            await chat(system, "hello", **args)
    else:
        result = await chat(system, "hello", **args)
        assert result["done_reason"] == outcome
    usage_events = [e["data"] for e in events if e["type"] == "context_usage"]
    assert len(usage_events) == 2
    start, end = usage_events
    assert start["call_id"] == end["call_id"]
    assert start["purpose"] == "compaction"
    assert start["context_window"] == 32768 and start["output_limit"] == 4096
    assert start["capacity_source"] == "provider_reported"
    assert start["input_budget"] == 28672
    assert start["image_count"] == 1
    assert start["estimated_input_tokens"] < 1000  # base64 is not text token usage
    assert start["estimated_parts"]["system"] > 5
    assert start["estimated_parts"]["tools"] > 0
    assert start["input_tokens"] is None and start["output_tokens"] is None
    assert end["status"] == {"length": "output_truncated", "stop": "completed", "overflow": "context_overflow", "timeout": "failed"}[outcome]
    if outcome in {"length", "stop"}:
        assert end["input_tokens"] == 28939 and end["output_tokens"] == 4096
        assert end["reasoning_tokens"] is None
        assert end["thinking_chars"] == 9
    assert "SECRET_IMAGE_BYTES" not in str(usage_events)
