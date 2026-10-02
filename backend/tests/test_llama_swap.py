import json

import httpx
import pytest

from app.config import settings
from app.core.llm.factory import get_llm_provider, reset_llm_provider
from app.core.llm.llama_swap import LlamaSwapLifecycle


@pytest.mark.asyncio
async def test_prepare_loads_once_and_status_tracks_ttl_unload():
    loaded = False
    warms = []

    def handler(request):
        nonlocal loaded
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen"}]})
        if request.url.path == "/v1/chat/completions":
            warms.append(json.loads(request.content))
            loaded = True
            return httpx.Response(200, json={})
        running = [{"model": "qwen", "state": "ready"}] if loaded else []
        return httpx.Response(200, json={"running": running})

    lifecycle = LlamaSwapLifecycle(
        "http://localhost:11435/v1", transport=httpx.MockTransport(handler)
    )
    try:
        await lifecycle.prepare("qwen")
        await lifecycle.prepare("qwen")
        assert len(warms) == 1 and warms[0]["max_tokens"] == 1
        loaded = False
        assert not (await lifecycle.status("qwen"))["ready"]
        await lifecycle.prepare("qwen")
        assert len(warms) == 2
        with pytest.raises(RuntimeError, match="not available"):
            await lifecycle.prepare("missing")
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_release_waits_for_all_models_to_exit():
    paths = []
    states = [[{"model": "qwen", "state": "stopping"}], []]

    def handler(request):
        paths.append(request.url.path)
        payload = {"msg": "ok"} if request.method == "POST" else {"running": states.pop(0)}
        return httpx.Response(200, json=payload)

    lifecycle = LlamaSwapLifecycle(
        "http://localhost:11435/v1", transport=httpx.MockTransport(handler)
    )
    try:
        await lifecycle.release(["qwen"])
        assert paths == ["/api/models/unload", "/running", "/running"]
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "stuck", "malformed"])
async def test_unconfirmed_unload_blocks_gpu_handoff(failure):
    from types import SimpleNamespace

    from app.core.vram.orchestrator import VramOrchestrator

    def handler(request):
        if failure == "http":
            return httpx.Response(503)
        if request.method == "POST":
            return httpx.Response(200, json={"msg": "ok"})
        payload = {} if failure == "malformed" else {"running": [{"model": "qwen"}]}
        return httpx.Response(200, json=payload)

    lifecycle = LlamaSwapLifecycle(
        "http://localhost:11435/v1",
        release_timeout=0.05,
        transport=httpx.MockTransport(handler),
    )
    provider = SimpleNamespace(
        provider_id="llama-swap", client=None, lifecycle=lifecycle
    )
    orch = VramOrchestrator(provider=provider, models=["qwen"])
    try:
        with pytest.raises(RuntimeError):
            await orch.before_comfy_job("test")
        assert orch.owner != "comfy"
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_factory_uses_local_gpu_lifecycle(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "llama-swap")
    monkeypatch.setattr(settings, "llm_base_url", "")
    reset_llm_provider()
    provider = get_llm_provider()
    try:
        assert provider.client.base_url == "http://127.0.0.1:11435/v1"
        assert provider.lifecycle.uses_local_gpu
        assert provider.lifecycle.release_failure_is_fatal
    finally:
        await provider.lifecycle.close()
        await provider.client.close()
        reset_llm_provider()


@pytest.mark.asyncio
async def test_context_capacity_comes_from_llama_cpp_props_for_selected_model():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "default_generation_settings": {"n_ctx": 131072},
        })

    lifecycle = LlamaSwapLifecycle(
        "http://localhost:11435/v1", transport=httpx.MockTransport(handler)
    )
    try:
        assert await lifecycle.context_capacity("qwen3_8") == 131072
    finally:
        await lifecycle.close()

    assert requests[0].url.path == "/props"
    assert requests[0].url.params["model"] == "qwen3_8"


@pytest.mark.asyncio
async def test_prompt_token_count_uses_upstream_chat_template_and_tokenizer():
    import json
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/apply-template"):
            assert body["messages"] == [{"role": "user", "content": "你好"}]
            assert body["add_generation_prompt"] is True
            return httpx.Response(200, json={"prompt": "<user>你好<assistant>"})
        assert body == {"content": "<user>你好<assistant>", "add_special": False, "parse_special": True}
        return httpx.Response(200, json={"tokens": [1, 2, 3, 4]})
    lifecycle = LlamaSwapLifecycle("http://localhost:11435/v1", transport=httpx.MockTransport(handler))
    try:
        assert await lifecycle.prompt_token_count("qwen3_8", "你好") == 4
    finally:
        await lifecycle.close()
    assert [r.url.path for r in requests] == ["/upstream/qwen3_8/apply-template", "/upstream/qwen3_8/tokenize"]
