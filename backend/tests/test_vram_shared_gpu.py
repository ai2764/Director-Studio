"""DS_VRAM_SHARED_GPU: GPU handoff between the Director LLM and a (remote) ComfyUI."""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.vram.orchestrator import VramOrchestrator
from app.core.vram.shared_gpu import resolve_shared_gpu


def _cfg(**kw):
    base = dict(vram_shared_gpu="auto", comfy_mode="remote", comfy_remote_url="", llm_provider="llama-swap",
                llm_base_url="", ollama_mode="system", ollama_base_url="http://127.0.0.1:11434")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("kw", "shared"),
    [
        ({"comfy_mode": "managed"}, True),
        ({"comfy_mode": "external-local"}, True),
        # forwarders/tunnels to the same PC: indistinguishable -> safe default
        ({"comfy_remote_url": "http://127.0.0.1:18000", "llm_base_url": "http://127.0.0.1:18001/v1"}, True),
        ({"comfy_remote_url": "http://100.1.2.3:8188", "llm_base_url": "http://100.1.2.3:11435/v1"}, True),
        ({"comfy_remote_url": "http://PC.lan.example:8188", "llm_base_url": "http://pc.lan.example:11435/v1"}, True),
        ({"comfy_remote_url": "http://100.1.2.3:8188", "llm_base_url": "http://100.9.9.9:11435/v1"}, False),
        # local LLM (default llama-swap URL) next to a ComfyUI on another machine
        ({"comfy_remote_url": "https://comfy.example.com"}, False),
        ({"comfy_remote_url": "http://100.1.2.3:8188", "llm_provider": "ollama", "ollama_mode": "bundled"}, False),
        ({"comfy_remote_url": ""}, False),
        # explicit setting wins
        ({"comfy_remote_url": "https://comfy.example.com", "vram_shared_gpu": "true"}, True),
        ({"comfy_mode": "managed", "vram_shared_gpu": "false"}, False),
    ],
)
def test_resolve_shared_gpu(kw, shared):
    assert resolve_shared_gpu(_cfg(**kw)).shared is shared


class LocalLifecycle:
    uses_local_gpu = True
    release_failure_is_fatal = True

    def __init__(self):
        self.released: list[list[str]] = []

    async def prepare(self, model, on_status=None):
        del model, on_status

    async def release(self, models):
        self.released.append(list(models))

    async def status(self, model):
        return {"model": model, "ready": True}


class FakeComfy:
    def __init__(self):
        self.free_calls = 0

    async def free_memory(self, *, unload_models=True, free_memory=True):
        self.free_calls += 1


def _orch(lifecycle, comfy):
    provider = SimpleNamespace(provider_id="llama-swap", lifecycle=lifecycle, client=object())
    return VramOrchestrator(provider=provider, comfy=comfy, models=["m"], acquire_timeout_sec=5.0)


@pytest.fixture
def remote(monkeypatch):
    from app.core.vram import director_model

    monkeypatch.setattr(director_model, "get_director_model", lambda provider_id=None: "m")
    monkeypatch.setattr(settings, "comfy_mode", "remote")
    monkeypatch.setattr(settings, "comfy_remote_url", "http://127.0.0.1:18000")
    monkeypatch.setattr(settings, "llm_provider", "llama-swap")
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:18001/v1")
    return monkeypatch


@pytest.mark.asyncio
async def test_remote_shared_gpu_hands_over_both_ways(remote):
    remote.setattr(settings, "vram_shared_gpu", "true")
    lifecycle, comfy = LocalLifecycle(), FakeComfy()
    orch = _orch(lifecycle, comfy)
    async with orch.llm_session(release_on_exit=False):
        await orch.ensure_llm_ready()
    assert comfy.free_calls == 1  # /free on the remote ComfyUI before the LLM turn
    await orch.before_comfy_job("actor")
    assert lifecycle.released == [["m"]]  # llama-swap unloaded before the Comfy job
    assert orch.owner == "comfy"


@pytest.mark.asyncio
async def test_remote_separate_gpu_skips_handoff(remote):
    remote.setattr(settings, "vram_shared_gpu", "false")
    lifecycle, comfy = LocalLifecycle(), FakeComfy()
    orch = _orch(lifecycle, comfy)
    async with orch.llm_session(release_on_exit=False):
        await orch.ensure_llm_ready()
    await orch.before_comfy_job("actor")
    assert comfy.free_calls == 0
    assert lifecycle.released == []
