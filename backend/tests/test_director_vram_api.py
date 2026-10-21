from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.api import director as director_api
from app.core.vram import GenerationReservation


class FakeOllama:
    async def loaded_models(self):
        return []

    async def model_vram_bytes(self, model: str) -> int:
        return 0


class FakeOrchestrator:
    owner = None
    comfy_pipeline = None
    policy = "exclusive"
    models = ["qwen-test"]
    acquire_timeout_sec = 3600
    _llm_ready = False
    _waiters = 0
    last_comfy_free = None
    last_comfy_free_error = None
    ollama = FakeOllama()

    async def generation_reservations(self):
        return [
            GenerationReservation(
                job_id="job_video",
                pipeline_id="h3_ref2va",
                kind="video",
                status="running",
                phase="generating",
                queued_at="2026-08-31T10:00:00+00:00",
            )
        ]


@pytest.mark.asyncio
async def test_vram_status_exposes_generation_chat_lock(monkeypatch):
    monkeypatch.setattr(director_api, "get_orchestrator", FakeOrchestrator)
    monkeypatch.setattr(director_api, "get_director_model", lambda: "qwen-test")

    result = await director_api.vram_status()

    assert result["chat_locked"] is True
    assert result["generation_count"] == 1
    assert result["generation_jobs"] == [
        {
            "job_id": "job_video",
            "pipeline_id": "h3_ref2va",
            "kind": "video",
            "status": "running",
            "phase": "generating",
            "queued_at": "2026-08-31T10:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("local_gpu", [False, True])
async def test_generation_chat_lock_follows_provider_gpu_usage(monkeypatch, local_gpu):
    from app.api.projects import _assert_chat_available
    from app.core.vram import GenerationActiveError

    class Lifecycle:
        uses_local_gpu = local_gpu

        async def status(self, model):
            return {"ready": True, "uses_local_gpu": local_gpu}

    orch = FakeOrchestrator()
    orch.owner = "comfy"
    orch.provider = SimpleNamespace(
        provider_id="lm-studio" if local_gpu else "openai-compatible",
        lifecycle=Lifecycle(), model_status=lambda: {"model": "test-model"},
    )
    monkeypatch.setattr(director_api, "get_orchestrator", lambda: orch)
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: orch)

    result = await director_api.vram_status()
    assert result["chat_locked"] is local_gpu
    assert result["generation_count"] == 1
    assert result["generation_jobs"][0]["job_id"] == "job_video"
    if local_gpu:
        with pytest.raises(GenerationActiveError):
            await _assert_chat_available()
    else:
        await _assert_chat_available()
