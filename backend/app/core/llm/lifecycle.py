from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ...config import settings
from ..vram.ollama_client import OllamaClient
from .provider import LLMClient


async def _emit_status(callback: Callable[[str], Any] | None, text: str) -> None:
    if callback is None:
        return
    result = callback(text)
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await result


class RemoteLifecycle:
    uses_local_gpu = False
    release_failure_is_fatal = False

    def __init__(self, client: LLMClient, *, provider_id: str) -> None:
        self.client = client
        self.provider_id = provider_id

    async def prepare(
        self,
        model: str,
        on_status: Callable[[str], Any] | None = None,
    ) -> None:
        if not await self.client.health():
            raise RuntimeError(f"{self.provider_id} is not reachable")
        await _emit_status(on_status, f"{model} ready via {self.provider_id}")

    async def release(self, models: Sequence[str]) -> None:
        del models

    async def status(self, model: str) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "uses_local_gpu": False,
            "ready": await self.client.health(),
            "model": model,
            "loaded_instances": [],
        }


class OllamaLifecycle:
    uses_local_gpu = True
    release_failure_is_fatal = False

    def __init__(self, client: OllamaClient) -> None:
        self.client = client

    async def prepare(
        self,
        model: str,
        on_status: Callable[[str], Any] | None = None,
    ) -> None:
        if not await self.client.health():
            raise RuntimeError("Ollama is not reachable at " + self.client.base_url)
        vram = await self.client.model_vram_bytes(model)
        if vram > 0:
            await _emit_status(
                on_status,
                f"{model} ready on GPU ({vram / (1024**3):.1f} GB)",
            )
            return
        await _emit_status(on_status, f"Starting {model}…")
        keep = "60m" if settings.llm_keep_loaded else "0"
        await self.client.generate(
            model,
            "ok",
            keep_alive=keep,
            options={"num_gpu": 999, "num_predict": 1},
        )
        vram = await self.client.model_vram_bytes(model)
        if vram > 0:
            await _emit_status(
                on_status,
                f"{model} ready on GPU ({vram / (1024**3):.1f} GB)",
            )
        else:
            await _emit_status(
                on_status,
                f"Warning: {model} responded but size_vram=0; it may be running on CPU",
            )

    async def release(self, models: Sequence[str]) -> None:
        await self.client.unload_models(models)

    async def status(self, model: str) -> dict[str, Any]:
        loaded = await self.client.loaded_models()
        vram = await self.client.model_vram_bytes(model)
        return {
            "provider": "ollama",
            "uses_local_gpu": True,
            "ready": vram > 0,
            "model": model,
            "size_vram": vram,
            "loaded_instances": loaded,
        }


def _server_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("LM Studio base URL must include scheme and host")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


class LMStudioLifecycle:
    uses_local_gpu = True
    release_failure_is_fatal = True

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None,
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _server_origin(base_url)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _catalog(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.get("/api/v1/models")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise RuntimeError(f"LM Studio model catalog request failed{suffix}") from exc
        payload = response.json()
        return list(payload.get("models", []) if isinstance(payload, dict) else [])

    async def list_models(self) -> list[str]:
        return [
            str(item["key"])
            for item in await self._catalog()
            if item.get("type") == "llm" and item.get("key")
        ]

    async def prepare(
        self,
        model: str,
        on_status: Callable[[str], Any] | None = None,
    ) -> None:
        available = await self.list_models()
        if model not in available:
            raise RuntimeError(f"LM Studio model is not available: {model}")
        await _emit_status(on_status, f"{model} ready for LM Studio JIT loading")

    async def release(self, models: Sequence[str]) -> None:
        del models
        instance_ids = {
            str(instance.get("instance_id") or instance.get("id"))
            for model in await self._catalog()
            if model.get("type") == "llm"
            for instance in list(model.get("loaded_instances") or [])
            if instance.get("instance_id") or instance.get("id")
        }
        for instance_id in sorted(instance_ids):
            try:
                response = await self._client.post(
                    "/api/v1/models/unload",
                    json={"instance_id": instance_id},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                suffix = f" (HTTP {status})" if status else ""
                raise RuntimeError(
                    f"LM Studio failed to unload model instance {instance_id}{suffix}"
                ) from exc

    async def status(self, model: str) -> dict[str, Any]:
        catalog = await self._catalog()
        loaded_ids = [
            str(instance.get("instance_id") or instance.get("id"))
            for item in catalog
            if item.get("type") == "llm"
            for instance in list(item.get("loaded_instances") or [])
            if instance.get("instance_id") or instance.get("id")
        ]
        selected_loaded = any(
            item.get("key") == model and item.get("loaded_instances")
            for item in catalog
        )
        return {
            "provider": "lm-studio",
            "uses_local_gpu": True,
            "ready": bool(selected_loaded),
            "model": model,
            "loaded_instances": loaded_ids,
        }
