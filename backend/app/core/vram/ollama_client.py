from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import httpx
from ollama import AsyncClient

from ...config import settings

logger = logging.getLogger("director_studio.ollama")

# Offload as many layers as possible to GPU (Ollama ignores excess).
def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _merge_thinking(data: dict[str, Any]) -> str:
    thinking = data.get("thinking") or data.get("reasoning") or ""
    msg = data.get("message") or {}
    text = str(data.get("response") or msg.get("content") or "")
    if thinking and "<think>" not in text.lower():
        return f"<think>{thinking}</think>\n{text}"
    if msg.get("thinking") and "<think>" not in text.lower():
        return f"<think>{msg.get('thinking')}</think>\n{text}"
    return text


def _err_text(r: httpx.Response) -> str:
    try:
        return (r.text or "")[:500]
    except Exception:
        return f"HTTP {r.status_code}"


def _is_multimodal_rejected(body: str) -> bool:
    b = (body or "").lower()
    return "multimodal" in b or "does not support multimodal" in b


class OllamaClient:
    """Minimal async client for Ollama health, unload, generate, and vision chat."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 600.0) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        if not _is_loopback(self.base_url):
            pass
        self._timeout = timeout

    def _opts(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        o = {
            "num_gpu": 999,
            "num_ctx": settings.director_num_ctx,
            "num_predict": settings.director_num_predict,
        }
        if extra:
            o.update(extra)
        return o

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code < 400
        except (httpx.HTTPError, OSError):
            return False

    async def list_models(self) -> list[str]:
        """Return installed Ollama model tags in server order."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            names: list[str] = []
            for item in (response.json() or {}).get("models") or []:
                name = item.get("name") or item.get("model")
                if name:
                    names.append(str(name))
            return names

    async def loaded_models(self) -> list[dict[str, Any]]:
        """Return /api/ps models list (includes size_vram)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/api/ps")
                if r.status_code >= 400:
                    return []
                return list((r.json() or {}).get("models") or [])
        except (httpx.HTTPError, OSError):
            return []

    async def model_vram_bytes(self, model: str) -> int:
        """VRAM bytes currently used by model (0 = CPU/RAM only)."""
        name = (model or "").split(":")[0]
        for m in await self.loaded_models():
            n = str(m.get("name") or m.get("model") or "")
            if n == model or n.startswith(name):
                return int(m.get("size_vram") or 0)
        return 0

    async def unload_models(self, names: Sequence[str] | Iterable[str]) -> None:
        """Best-effort unload: POST /api/generate with keep_alive=0."""
        timeout = httpx.Timeout(10.0, connect=2.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                for name in names:
                    if not name:
                        continue
                    try:
                        await client.post(
                            f"{self.base_url}/api/generate",
                            json={
                                "model": name,
                                "prompt": "",
                                "keep_alive": 0,
                            },
                        )
                    except (httpx.HTTPError, OSError):
                        continue
        except (httpx.HTTPError, OSError):
            return

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        images: Sequence[str] | None = None,
        keep_alive: str | int | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Non-streaming generate. Forces GPU offload via num_gpu when possible."""
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": self._opts(options),
        }
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        elif getattr(settings, "llm_keep_loaded", True):
            body["keep_alive"] = "60m"
        if images:
            body["images"] = list(images)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(f"{self.base_url}/api/generate", json=body)
            if r.status_code >= 400 and images:
                err = _err_text(r)
                if _is_multimodal_rejected(err):
                    logger.warning(
                        "model %s rejects multimodal generate; text-only fallback",
                        model,
                    )
                    body.pop("images", None)
                    r = await client.post(f"{self.base_url}/api/generate", json=body)
                else:
                    logger.warning(
                        "generate+images failed (%s): %s; trying /api/chat",
                        r.status_code,
                        err,
                    )
                    try:
                        return await self.chat(
                            model, prompt, images=images, keep_alive=keep_alive
                        )
                    except Exception:
                        body.pop("images", None)
                        r = await client.post(
                            f"{self.base_url}/api/generate", json=body
                        )
            r.raise_for_status()
            return _merge_thinking(r.json())

    async def chat_response(
        self,
        model: str,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        format: dict[str, Any] | str | None = None,
        keep_alive: str | int | None = None,
        options: dict[str, Any] | None = None,
        require_vision: bool = False,
    ) -> dict[str, Any]:
        """Return a provider-neutral chat result using Ollama's official SDK."""
        request: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "stream": False,
            "options": self._opts(options),
        }
        if tools:
            request["tools"] = list(tools)
        if format is not None:
            request["format"] = format
        if keep_alive is not None:
            request["keep_alive"] = keep_alive
        elif getattr(settings, "llm_keep_loaded", True):
            request["keep_alive"] = "60m"

        client = AsyncClient(host=self.base_url, timeout=self._timeout)
        try:
            response = await client.chat(**request)
        except Exception as exc:
            has_images = any(message.get("images") for message in request["messages"])
            if require_vision or not has_images or not _is_multimodal_rejected(str(exc)):
                raise
            logger.warning(
                "chat+images failed for %s: %s — falling back to text",
                model,
                str(exc)[:500],
            )
            for message in request["messages"]:
                message.pop("images", None)
            response = await client.chat(**request)

        close = getattr(client, "close", None)
        if close is not None:
            await close()

        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message") or {}

        def _value(obj: Any, key: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        normalized_calls: list[dict[str, Any]] = []
        for call in list(_value(message, "tool_calls", []) or []):
            function = _value(call, "function", {}) or {}
            name = str(_value(function, "name", "") or "").strip()
            arguments = _value(function, "arguments", {}) or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if name:
                normalized_calls.append(
                    {
                        "name": name,
                        "arguments": dict(arguments) if isinstance(arguments, dict) else {},
                    }
                )

        result = {
            "content": str(_value(message, "content", "") or ""),
            "thinking": str(_value(message, "thinking", "") or ""),
            "tool_calls": normalized_calls,
        }
        done_reason = str(_value(response, "done_reason", "") or "")
        if done_reason:
            result["done_reason"] = done_reason
        return result

    async def _chat_http_legacy(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        images: Sequence[str] | None = None,
        keep_alive: str | int | None = None,
        options: dict[str, Any] | None = None,
        require_vision: bool = False,
    ) -> str:
        """/api/chat — with vision fallback to text when model has no multimodal."""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user_msg["images"] = list(images)
        messages.append(user_msg)

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": self._opts(options),
        }
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        elif getattr(settings, "llm_keep_loaded", True):
            body["keep_alive"] = "60m"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=body)
            if r.status_code >= 400 and images:
                err = _err_text(r)
                if require_vision:
                    r.raise_for_status()
                logger.warning(
                    "chat+images failed for %s (%s): %s — falling back to text",
                    model,
                    r.status_code,
                    err,
                )
                # Drop images; keep text (VISION captions already in prompt if any)
                user_msg.pop("images", None)
                messages[-1] = user_msg
                body["messages"] = messages
                note = (
                    "\n\n(System note: current model does not accept images; "
                    "answered from text captions only.)"
                )
                if isinstance(user_msg.get("content"), str):
                    user_msg["content"] = str(user_msg["content"]) + note
                r = await client.post(f"{self.base_url}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
            msg = data.get("message") or {}
            thinking = data.get("thinking") or msg.get("thinking") or ""
            text = str(msg.get("content") or data.get("response") or "")
            if thinking and "<think>" not in text.lower():
                return f"<think>{thinking}</think>\n{text}"
            return text

    async def chat(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        images: Sequence[str] | None = None,
        keep_alive: str | int | None = None,
        options: dict[str, Any] | None = None,
        require_vision: bool = False,
        format: dict[str, Any] | str | None = None,
    ) -> str:
        """Text convenience wrapper over the official SDK chat response."""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        user_message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user_message["images"] = list(images)
        messages.append(user_message)
        result = await self.chat_response(
            model,
            messages=messages,
            keep_alive=keep_alive,
            options=options,
            require_vision=require_vision,
            format=format,
        )
        text = result["content"]
        thinking = result["thinking"]
        if thinking and "<think>" not in text.lower():
            return f"<think>{thinking}</think>\n{text}"
        return text

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        *,
        images: Sequence[str] | None = None,
        system: str | None = None,
        keep_alive: str | int | None = None,
        options: dict[str, Any] | None = None,
    ):
        """Stream tokens. Vision path falls back to text stream on multimodal 400."""
        if images:
            try:
                async for item in self.chat_stream(
                    model,
                    prompt,
                    system=system,
                    images=images,
                    keep_alive=keep_alive,
                    options=options,
                ):
                    yield item
                return
            except httpx.HTTPStatusError as e:
                err = _err_text(e.response) if e.response is not None else str(e)
                logger.warning(
                    "vision stream failed (%s); text stream fallback: %s",
                    getattr(e.response, "status_code", "?"),
                    err,
                )
                # fall through to text generate_stream
                images = None
                if system:
                    prompt = f"{system}\n\n{prompt}\n\n(System note: model cannot view images.)"

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": self._opts(options),
        }
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        elif getattr(settings, "llm_keep_loaded", True):
            body["keep_alive"] = "60m"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/generate", json=body
            ) as r:
                if r.status_code >= 400:
                    # read body for better error
                    await r.aread()
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    chunk = data.get("response")
                    think = data.get("thinking") or data.get("reasoning")
                    if think:
                        yield {"kind": "think", "text": str(think)}
                    if chunk:
                        yield {"kind": "token", "text": str(chunk)}
                    if data.get("done"):
                        break

    async def chat_stream(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        images: Sequence[str] | None = None,
        keep_alive: str | int | None = None,
        options: dict[str, Any] | None = None,
    ):
        """Stream /api/chat."""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user_msg["images"] = list(images)
        messages.append(user_msg)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": self._opts(options),
        }
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        elif getattr(settings, "llm_keep_loaded", True):
            body["keep_alive"] = "60m"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=body
            ) as r:
                # Need to raise with body available
                if r.status_code >= 400:
                    await r.aread()
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    msg = data.get("message") or {}
                    think = data.get("thinking") or msg.get("thinking")
                    chunk = msg.get("content") or data.get("response")
                    if think:
                        yield {"kind": "think", "text": str(think)}
                    if chunk:
                        yield {"kind": "token", "text": str(chunk)}
                    if data.get("done"):
                        break
