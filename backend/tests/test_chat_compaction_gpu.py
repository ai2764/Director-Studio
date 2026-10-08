"""Compaction after a generation tool must queue behind its GPU work."""

import asyncio
from types import SimpleNamespace

import pytest

from app.api.projects import _make_chat_fn
from app.core.vram.orchestrator import GenerationActiveError, VramOrchestrator


@pytest.mark.asyncio
async def test_compaction_waits_for_comfy_without_admitting_a_new_chat(monkeypatch):
    calls = []

    class Lifecycle:
        uses_local_gpu = True
        release_failure_is_fatal = True

        async def prepare(self, model, on_status=None):
            assert orch.owner == "llm"

        async def release(self, models):
            pass

        async def context_capacity(self, model):
            return 65536

    class Client:
        async def chat_response(self, model, **kwargs):
            assert orch.owner == "llm"
            calls.append(kwargs)
            return {"content": "Confirmed Layout job; review pending.", "tool_calls": []}

    class Comfy:
        async def free_memory(self, **kwargs):
            pass

    provider = SimpleNamespace(
        provider_id="llama-swap", client=Client(), lifecycle=Lifecycle(),
        model_status=lambda: {"model": "test-model"},
    )
    orch = VramOrchestrator(provider=provider, comfy=Comfy(), models=["test-model"])
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: orch)
    monkeypatch.setattr("app.core.vram.director_model.get_director_model", lambda *a: "test-model")
    await orch.reserve_generation(
        job_id="job_layout", pipeline_id="qwen21_layout", kind="image",
        status="running", phase="generating", queued_at="2026-10-04T00:00:00Z",
    )
    await orch.before_comfy_job("qwen21_layout")
    chat = await _make_chat_fn()
    messages = [{"role": "user", "content": "Generate and review the Layout."}]
    with pytest.raises(GenerationActiveError):
        await chat("Director", "", messages=messages, prepared_system=True)
    task = asyncio.create_task(chat(
        "Summarize history", "", messages=messages, prepared_system=True,
        inference_purpose="compaction", max_output_tokens=512,
    ))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.02)
        assert calls == [] and orch.owner == "comfy"
        await orch.after_comfy_job("qwen21_layout", "succeeded")
        await orch.release_generation("job_layout")
        result = await asyncio.wait_for(task, timeout=1)
        assert result["content"] == "Confirmed Layout job; review pending."
        assert len(calls) == 1 and not calls[0].get("tools")
        assert orch.owner is None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
