from __future__ import annotations

import json

import httpx
import pytest

from app.core.llm.lifecycle import LMStudioLifecycle, OllamaLifecycle, RemoteLifecycle


def _catalog() -> dict:
    return {
        "models": [
            {
                "key": "openai/gpt-oss-20b",
                "type": "llm",
                "loaded_instances": [
                    {"id": "instance-b"},
                    {"instance_id": "instance-a"},
                ],
            },
            {
                "key": "text-embedding-model",
                "type": "embedding",
                "loaded_instances": [{"id": "embedding-instance"}],
            },
            {
                "key": "qwen/vision-model",
                "type": "llm",
                "loaded_instances": [],
            },
        ]
    }


@pytest.mark.asyncio
async def test_lm_studio_catalog_filters_to_llm_model_keys() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_catalog())

    lifecycle = LMStudioLifecycle(
        "http://127.0.0.1:1234/v1/",
        api_key="private-token",
        timeout=10,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await lifecycle.list_models() == [
            "openai/gpt-oss-20b",
            "qwen/vision-model",
        ]
    finally:
        await lifecycle.close()

    assert requests[0].url == httpx.URL("http://127.0.0.1:1234/api/v1/models")
    assert requests[0].headers["authorization"] == "Bearer private-token"


@pytest.mark.asyncio
async def test_lm_studio_release_unloads_every_loaded_llm_instance() -> None:
    unload_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_catalog())
        unload_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=unload_bodies[-1])

    lifecycle = LMStudioLifecycle(
        "http://127.0.0.1:1234/v1",
        api_key=None,
        timeout=10,
        transport=httpx.MockTransport(handler),
    )
    try:
        await lifecycle.release(["openai/gpt-oss-20b"])
    finally:
        await lifecycle.close()

    assert unload_bodies == [
        {"instance_id": "instance-a"},
        {"instance_id": "instance-b"},
    ]


@pytest.mark.asyncio
async def test_lm_studio_unload_failure_is_fatal_without_leaking_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_catalog())
        return httpx.Response(
            500,
            json={"error": "cannot unload"},
        )

    lifecycle = LMStudioLifecycle(
        "http://127.0.0.1:1234/v1",
        api_key="private-token",
        timeout=10,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(RuntimeError, match="instance-a") as error:
            await lifecycle.release([])
    finally:
        await lifecycle.close()

    assert "private-token" not in str(error.value)


@pytest.mark.asyncio
async def test_remote_lifecycle_checks_health_without_local_gpu_actions() -> None:
    class Client:
        async def health(self) -> bool:
            return True

    lifecycle = RemoteLifecycle(Client(), provider_id="openai-compatible")

    await lifecycle.prepare("remote-model")
    await lifecycle.release(["remote-model"])

    assert lifecycle.uses_local_gpu is False
    assert lifecycle.release_failure_is_fatal is False
    assert (await lifecycle.status("remote-model"))["ready"] is True


@pytest.mark.asyncio
async def test_ollama_lifecycle_warms_and_releases_selected_model() -> None:
    class Client:
        base_url = "http://127.0.0.1:11434"

        def __init__(self) -> None:
            self.vram = 0
            self.generated: list[tuple[str, str]] = []
            self.unloaded: list[str] = []

        async def health(self) -> bool:
            return True

        async def model_vram_bytes(self, _model: str) -> int:
            return self.vram

        async def generate(self, model: str, prompt: str, **_kwargs) -> str:
            self.generated.append((model, prompt))
            self.vram = 1024
            return "ok"

        async def unload_models(self, models) -> None:
            self.unloaded.extend(models)
            self.vram = 0

        async def loaded_models(self) -> list[dict]:
            return []

    client = Client()
    lifecycle = OllamaLifecycle(client)

    await lifecycle.prepare("qwen-local")
    await lifecycle.release(["qwen-local"])

    assert client.generated == [("qwen-local", "ok")]
    assert client.unloaded == ["qwen-local"]
    assert lifecycle.uses_local_gpu is True
