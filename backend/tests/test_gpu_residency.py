from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.core.llm.factory import get_llm_provider, reset_llm_provider
from app.core.llm.gpu_residency import (
    resolve_shared_gpu, StrataLifecycle, LocalOpenAILifecycle, LocatedLifecycle,
)
from app.core.vram.orchestrator import VramOrchestrator


@pytest.mark.parametrize("llm,comfy,override,expected", [
    ("http://localhost:8080/v1", "http://127.0.0.1:8188", "auto", True),
    ("http://[::1]:8080/v1", "http://127.2.3.4:8188", "auto", True),
    ("http://[::ffff:127.0.0.1]:8080/v1", "http://localhost:8188", "auto", True),
    ("https://llm.example/v1", "http://127.0.0.1:8188", "auto", False),
    ("http://127.0.0.1:8080/v1", "https://comfy.example", "auto", False),
    ("http://GPU.example:8080/v1", "http://gpu.example:8188", "auto", True),
    ("http://gpu-a.example:8080/v1", "http://gpu-b.example:8188", "auto", False),
    ("http://localhost:8080/v1", "http://localhost:8188", "false", False),
    ("https://llm.example/v1", "http://localhost:8188", "true", True),
    ("", "http://localhost:8188", "auto", False),
])
def test_gpu_topology(llm, comfy, override, expected):
    assert resolve_shared_gpu(llm, comfy, override) is expected


@pytest.mark.parametrize("provider,url,shared", [
    ("openai-compatible", "http://localhost:8080/v1", True),
    ("openai-compatible", "https://llm.example/v1", False),
    ("lm-studio", "https://llm.example/v1", False),
    ("llama-swap", "https://llm.example/v1", False),
    ("ollama", "https://llm.example", False),
])
def test_factory_uses_location_independently_of_protocol(monkeypatch, provider, url, shared):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "llm_base_url", url)
    monkeypatch.setattr(settings, "ollama_base_url", url)
    monkeypatch.setattr(settings, "comfy_base_url", "http://localhost:8188")
    reset_llm_provider()
    try:
        assert get_llm_provider().lifecycle.uses_local_gpu is shared
    finally:
        reset_llm_provider()


@pytest.mark.asyncio
async def test_strata_shared_handoff_roundtrip(monkeypatch):
    loaded = True
    events = []

    def handle(request):
        nonlocal loaded
        assert request.headers["authorization"] == "Bearer fixture-key"
        path = request.url.path
        if path == "/proxy/v1/status":
            return httpx.Response(200, json={"loaded": loaded})
        if path == "/proxy/v1/models":
            return httpx.Response(200, json={"data": [{"id": "flash-next"}]})
        if path == "/proxy/unload":
            assert request.method == "POST" and request.url.params["wait"] == "1"
            events.append("unload")
            loaded = False
            return httpx.Response(200, json={})
        if path == "/proxy/v1/load":
            events.append("load")
            loaded = True
            return httpx.Response(200, json={})
        raise AssertionError(str(request.url))

    lifecycle = StrataLifecycle("http://localhost:8080/proxy/v1", api_key="fixture-key",
                               transport=httpx.MockTransport(handle))
    provider = SimpleNamespace(provider_id="openai-compatible", lifecycle=lifecycle, client=object())

    class Comfy:
        async def free_memory(self, **kwargs):
            events.append("free-comfy")

    monkeypatch.setattr("app.core.vram.director_model.get_director_model", lambda *a: "flash-next")
    orch = VramOrchestrator(provider=provider, comfy=Comfy(), models=["flash-next"])
    try:
        await orch.before_comfy_job("video")
        assert loaded is False and orch.owner == "comfy"
        await orch.after_comfy_job("video", "completed")
        async with orch.llm_session(release_on_exit=False):
            await orch.ensure_llm_ready()
        assert loaded is True
        assert events == ["unload", "free-comfy", "free-comfy", "load"]
    finally:
        await lifecycle.close()


@pytest.mark.parametrize("status", [{}, {"loaded": "false"}, {"loaded": True}])
@pytest.mark.asyncio
async def test_unconfirmed_strata_unload_stops_comfy(status):
    def handle(request):
        return httpx.Response(200, json=status if request.method == "GET" else {})

    lifecycle = StrataLifecycle("http://localhost:8080/v1", transport=httpx.MockTransport(handle))
    provider = SimpleNamespace(provider_id="openai-compatible", lifecycle=lifecycle, client=object())
    orch = VramOrchestrator(provider=provider, models=["flash-next"])
    try:
        with pytest.raises(RuntimeError):
            await orch.before_comfy_job("video")
        assert orch.owner is None
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_auto_identifies_strata_without_generating_tokens():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"service": "strata", "loaded": False})
        if request.url.path == "/v1/status":
            return httpx.Response(200, json={"loaded": False})
        raise AssertionError(str(request.url))

    lifecycle = LocalOpenAILifecycle("http://localhost:8080/v1", transport=httpx.MockTransport(handle))
    try:
        await lifecycle.release(["flash-next"])
        assert paths == ["/health", "/v1/status"]
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_unknown_local_server_does_not_get_unload_requests():
    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, json={"status": "ok"})

    lifecycle = LocalOpenAILifecycle("http://localhost:8080/v1", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RuntimeError, match="DS_LLM_RUNTIME"):
            await lifecycle.release(["model"])
        assert methods == ["GET"]
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_failed_session_release_drops_ownership():
    class Failing:
        uses_local_gpu = True
        release_failure_is_fatal = True

        async def release(self, models):
            raise RuntimeError("unload failed")

    class Comfy:
        async def free_memory(self, **kwargs):
            pass

    provider = SimpleNamespace(provider_id="openai-compatible", lifecycle=Failing(), client=object())
    orch = VramOrchestrator(provider=provider, comfy=Comfy(), models=["model"])
    with pytest.raises(RuntimeError, match="unload failed"):
        async with orch.llm_session():
            pass
    assert orch.owner is None


@pytest.mark.asyncio
async def test_separate_gpu_never_unloads_remote_runtime():
    class Runtime:
        release_failure_is_fatal = True

        async def release(self, models):
            pytest.fail("an independent runtime must not be unloaded")

        async def status(self, model):
            return {"uses_local_gpu": True, "ready": True}

    lifecycle = LocatedLifecycle(Runtime(), shared=False)
    assert lifecycle.release_failure_is_fatal is False
    assert (await lifecycle.status("m"))["uses_local_gpu"] is False
    await lifecycle.release(["m"])


@pytest.mark.parametrize("runtime,override,shared,adapter", [
    ("auto", "auto", True, LocalOpenAILifecycle),
    ("strata", "auto", True, StrataLifecycle),
    ("strata", "false", False, StrataLifecycle),
])
def test_explicit_runtime_is_independent_of_gpu_override(monkeypatch, runtime, override, shared, adapter):
    monkeypatch.setattr(settings, "llm_provider", "openai-compatible")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:8080/v1")
    monkeypatch.setattr(settings, "comfy_base_url", "http://localhost:8188")
    monkeypatch.setattr(settings, "llm_runtime", runtime)
    monkeypatch.setattr(settings, "vram_shared_gpu", override)
    reset_llm_provider()
    try:
        lifecycle = get_llm_provider().lifecycle
        assert lifecycle.uses_local_gpu is shared
        assert isinstance(lifecycle.runtime, adapter)
    finally:
        reset_llm_provider()


@pytest.mark.parametrize("error", [401, 404, 500])
@pytest.mark.asyncio
async def test_strata_auth_or_control_failure_blocks_handoff(error):
    def handle(request):
        return httpx.Response(error, json={})

    lifecycle = StrataLifecycle("http://localhost:8080/v1", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RuntimeError, match="handoff stopped"):
            await lifecycle.release(["model"])
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_strata_refuses_missing_model_before_loading():
    requests = []

    def handle(request):
        requests.append(request.method)
        return httpx.Response(200, json={"data": [{"id": "different-model"}]})

    lifecycle = StrataLifecycle("http://localhost:8080/v1", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RuntimeError, match="not available"):
            await lifecycle.prepare("selected-model")
        assert requests == ["GET"]
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_strata_rejects_unconfirmed_load():
    def handle(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "model"}]})
        return httpx.Response(200, json={"loaded": False})

    lifecycle = StrataLifecycle("http://localhost:8080/v1", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RuntimeError, match="load could not be confirmed"):
            await lifecycle.prepare("model")
    finally:
        await lifecycle.close()


@pytest.mark.parametrize("shared", [True, False])
@pytest.mark.asyncio
async def test_generation_blocks_only_llm_on_shared_gpu(monkeypatch, shared):
    from app.core.vram.orchestrator import GenerationActiveError

    monkeypatch.setattr(settings, "llm_provider", "openai-compatible")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:8080/v1" if shared else "https://llm.example/v1")
    monkeypatch.setattr(settings, "comfy_base_url", "http://localhost:8188")
    reset_llm_provider()
    try:
        orch = VramOrchestrator(provider=get_llm_provider(), models=["model"])
        await orch.reserve_generation(job_id="job", pipeline_id="video", kind="video",
                                      status="running", phase="generating", queued_at="2026-01-01")
        if shared:
            with pytest.raises(GenerationActiveError):
                async with orch.llm_session(fail_if_generation_pending=True):
                    pytest.fail("shared GPU was admitted")
        else:
            async with orch.llm_session(fail_if_generation_pending=True):
                assert orch.owner is None
    finally:
        reset_llm_provider()
