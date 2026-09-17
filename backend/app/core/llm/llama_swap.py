from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .lifecycle import _emit_status, _server_origin


class LlamaSwapLifecycle:
    """Dedicated local llama-swap instance sharing the GPU with Comfy."""

    uses_local_gpu = True
    release_failure_is_fatal = True

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 600.0,
        release_timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.release_timeout = release_timeout
        self._client = httpx.AsyncClient(
            base_url=_server_origin(base_url),
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            transport=transport,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[str]:
        response = await self._client.get("/v1/models")
        response.raise_for_status()
        return [item["id"] for item in response.json()["data"]]

    async def _running(self) -> list[dict[str, Any]]:
        response = await self._client.get("/running")
        response.raise_for_status()
        running = response.json().get("running")
        if not isinstance(running, list) or any(
            not isinstance(item, dict) for item in running
        ):
            raise RuntimeError("Invalid llama-swap running-model response")
        return running

    async def prepare(
        self,
        model: str,
        on_status: Callable[[str], Any] | None = None,
    ) -> None:
        if model not in await self.list_models():
            raise RuntimeError(f"llama-swap model is not available: {model}")
        if not (await self.status(model))["ready"]:
            await _emit_status(on_status, f"Loading {model} via llama.cpp…")
            response = await self._client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "OK"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                },
            )
            response.raise_for_status()
            if not (await self.status(model))["ready"]:
                raise RuntimeError(
                    "llama-swap responded but the selected model is not ready"
                )
        await _emit_status(on_status, f"{model} ready via llama.cpp")

    async def release(self, models: Sequence[str]) -> None:
        # This proxy is dedicated to the local GPU; release all its models.
        del models
        try:
            async with asyncio.timeout(self.release_timeout):
                response = await self._client.post("/api/models/unload")
                response.raise_for_status()
                while await self._running():
                    await asyncio.sleep(0.2)
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise RuntimeError(
                "llama-swap unload could not be confirmed; GPU handoff stopped"
            ) from exc

    async def status(self, model: str) -> dict[str, Any]:
        running = await self._running()
        return {
            "provider": "llama-swap",
            "uses_local_gpu": True,
            "model": model,
            "ready": any(
                item.get("model") == model and item.get("state") == "ready"
                for item in running
            ),
            "loaded_instances": [item.get("model") for item in running],
        }

    async def context_capacity(self, model: str) -> int | None:
        response = await self._client.get("/props", params={"model": model})
        response.raise_for_status()
        payload = response.json()
        value = (payload.get("default_generation_settings") or {}).get("n_ctx")
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
