from types import SimpleNamespace

import pytest

from app.core.jobs import runner
from app.config import settings


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["http://localhost:8190", "http://localhost:8188/"])
async def test_finish_unloads_separate_endpoint_before_releasing_gpu(monkeypatch, endpoint):
    events = []
    monkeypatch.setattr(settings, "comfy_base_url", "http://localhost:8188")

    class Client:
        async def free_memory(self, **kwargs):
            assert kwargs == {"unload_models": True, "free_memory": True}
            events.append("unload")

    class Orchestrator:
        async def after_comfy_job(self, pipeline_id, status):
            events.append("release")

    monkeypatch.setattr(runner, "_comfy_client_for_job", lambda job: Client())
    monkeypatch.setattr(runner, "get_orchestrator", Orchestrator)
    job = SimpleNamespace(id="test", pipeline_id="qwen21_layout",
                          params={"comfy_base_url": endpoint}, status=SimpleNamespace(value="completed"))
    await runner.finish_comfy(job)
    assert events == (["unload", "release"] if endpoint.endswith("8190") else ["release"])


@pytest.mark.asyncio
async def test_finish_releases_ownership_even_if_separate_endpoint_cleanup_fails(monkeypatch, caplog):
    events = []

    class Client:
        async def free_memory(self, **kwargs):
            raise RuntimeError("offline")

    class Orchestrator:
        async def after_comfy_job(self, pipeline_id, status):
            events.append("release")

    monkeypatch.setattr(runner, "_comfy_client_for_job", lambda job: Client())
    monkeypatch.setattr(runner, "get_orchestrator", Orchestrator)
    job = SimpleNamespace(id="test", pipeline_id="qwen21_layout",
                          params={"comfy_base_url": "http://localhost:8190"},
                          status=SimpleNamespace(value="failed"))
    await runner.finish_comfy(job)
    assert events == ["release"]
    assert "cleanup" in caplog.text
