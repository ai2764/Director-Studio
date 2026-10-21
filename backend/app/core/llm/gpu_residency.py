"""GPU location and residency controls, independent of the inference protocol."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

import httpx

from .lifecycle import _emit_status


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _loopback(host: str) -> bool:
    if host in {"localhost", "0.0.0.0", "::"}:
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or bool(
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def resolve_shared_gpu(llm_url: str, comfy_url: str, setting: str = "auto") -> bool:
    """Same host implies shared GPU unless explicitly overridden; never resolve DNS.

    Loopback tunnels, host aliases and separate cards require the explicit override.
    """
    if setting in {"true", "false"}:
        return setting == "true"
    llm, comfy = _host(llm_url), _host(comfy_url)
    return bool(llm and comfy and (llm == comfy or (_loopback(llm) and _loopback(comfy))))


class LocatedLifecycle:
    """Keep the historical uses_local_gpu flag aligned with GPU contention.

    A runtime on another GPU still prepares models for inference, but it never
    blocks local generation or unloads models on that server on our GPU's behalf.
    """

    def __init__(self, runtime, *, shared: bool):
        self.runtime = runtime
        self.uses_local_gpu = shared
        self.release_failure_is_fatal = shared and runtime.release_failure_is_fatal

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    async def prepare(self, model, on_status=None):
        await self.runtime.prepare(model, on_status=on_status)

    async def release(self, models):
        if self.uses_local_gpu:
            await self.runtime.release(models)

    async def status(self, model):
        return {**await self.runtime.status(model), "uses_local_gpu": self.uses_local_gpu}


def _management_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("LLM base URL must include scheme and host")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/", "", ""))


class StrataLifecycle:
    uses_local_gpu = True
    release_failure_is_fatal = True

    def __init__(self, base_url, *, api_key=None, timeout=600.0, transport=None):
        self._client = httpx.AsyncClient(
            base_url=_management_url(base_url), timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            transport=transport, trust_env=False,
        )

    async def close(self):
        await self._client.aclose()

    async def _json(self, method, path, **kwargs):
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Strata management request failed; GPU handoff stopped") from exc

    async def _loaded(self):
        payload = await self._json("GET", "v1/status")
        if not isinstance(payload, dict) or not isinstance(payload.get("loaded"), bool):
            raise RuntimeError("Strata returned an invalid model status; GPU handoff stopped")
        return payload["loaded"]

    async def prepare(self, model, on_status=None):
        payload = await self._json("GET", "v1/models")
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list) or model not in {
            item.get("id") for item in models if isinstance(item, dict)
        }:
            raise RuntimeError("Selected model is not available from Strata")
        if not await self._loaded():
            await _emit_status(on_status, f"Loading {model} via Strata…")
            await self._json("POST", "v1/load", json={"model": model})
            if not await self._loaded():
                raise RuntimeError("Strata model load could not be confirmed")
        await _emit_status(on_status, f"{model} ready via Strata")

    async def release(self, models):
        if not await self._loaded():
            return
        await self._json("POST", "unload", params={"wait": "1"}, json={})
        if await self._loaded():
            raise RuntimeError("Strata unload could not be confirmed; GPU handoff stopped")

    async def status(self, model):
        loaded = await self._loaded()
        return {"provider": "strata", "uses_local_gpu": True, "ready": loaded,
                "model": model, "loaded_instances": [model] if loaded else []}

    async def context_capacity(self, model):
        payload = await self._json("GET", "health")
        value = payload.get("max_context") if isinstance(payload, dict) else None
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


class LocalOpenAILifecycle(StrataLifecycle):
    """Discover a supported runtime before sending any model management requests."""

    _identified = False

    async def _identify(self):
        if self._identified:
            return
        try:
            payload = await self._json("GET", "health")
        except RuntimeError as exc:
            raise RuntimeError("Cannot identify shared-GPU LLM runtime; set DS_LLM_RUNTIME "
                               "to strata, lm-studio or llama-swap") from exc
        if not isinstance(payload, dict) or payload.get("service") != "strata":
            raise RuntimeError("Unknown shared-GPU LLM runtime; set DS_LLM_RUNTIME "
                               "to strata, lm-studio or llama-swap. OpenAI inference alone "
                               "does not define model unloading.")
        self._identified = True

    async def prepare(self, model, on_status=None):
        await self._identify()
        await super().prepare(model, on_status)

    async def release(self, models):
        await self._identify()
        await super().release(models)

    async def status(self, model):
        await self._identify()
        return await super().status(model)

    async def context_capacity(self, model):
        await self._identify()
        return await super().context_capacity(model)
