"""Cheap capability probes for the selected director model: tool calling + vision.

Each probe is one tiny request (a forced-choice tool call, a 16x16 solid colour
image).  Harness mode needs tool calling; vision is used for reviewing images.
For Ollama the declared ``/api/show`` capabilities are read first so models that
do not declare ``vision`` are not sent an image at all.
"""

from __future__ import annotations

import base64
import json
import struct
import time
from typing import Any
import zlib

import httpx

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}
TOOL_PROMPT = "What is the weather in Paris right now? Call the get_weather tool."
VISION_PROMPT = "What is the single dominant colour of this image? Answer with one English word."
VISION_ANSWERS = ("red",)


def solid_png(rgb: tuple[int, int, int] = (220, 20, 20), size: int = 16) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * size
    raw = row * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _result(supported: bool | None, detail: str, started: float, **extra: Any) -> dict[str, Any]:
    return {"supported": supported, "detail": detail[:300], "latency_ms": int((time.monotonic() - started) * 1000), **extra}


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


# ---- Ollama native API -------------------------------------------------------------

async def ollama_declared(client: httpx.AsyncClient, base: str, model: str) -> list[str] | None:
    try:
        response = await client.post(f"{base}/api/show", json={"model": model})
        if response.status_code != 200:
            return None
        caps = response.json().get("capabilities")
        return [str(c) for c in caps] if isinstance(caps, list) else None
    except (httpx.HTTPError, ValueError):
        return None


async def probe_ollama(base: str, model: str, *, headers: dict[str, str] | None = None,
                       timeout: float = 120.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, headers=headers or {}, trust_env=False) as client:
        declared = await ollama_declared(client, base, model)
        result: dict[str, Any] = {"model": model, "declared": declared}

        started = time.monotonic()
        try:
            response = await client.post(f"{base}/api/chat", json={
                "model": model, "stream": False, "tools": [TOOL],
                "messages": [{"role": "user", "content": TOOL_PROMPT}],
                "options": {"temperature": 0, "num_predict": 128},
            })
            if response.status_code == 400 and "does not support tools" in response.text:
                result["tools"] = _result(False, "model does not support tools", started)
            elif response.status_code != 200:
                result["tools"] = _result(None, f"HTTP {response.status_code}: {response.text[:200]}", started)
            else:
                calls = (response.json().get("message") or {}).get("tool_calls") or []
                names = [str((c.get("function") or {}).get("name")) for c in calls]
                result["tools"] = _result(
                    "get_weather" in names,
                    f"tool_calls={names}" if names else "the model answered in text instead of calling the tool",
                    started)
        except httpx.HTTPError as exc:
            result["tools"] = _result(None, _error_text(exc), started)

        started = time.monotonic()
        if declared is not None and "vision" not in declared:
            result["vision"] = _result(False, "model does not declare vision", started)
        else:
            try:
                response = await client.post(f"{base}/api/chat", json={
                    "model": model, "stream": False,
                    "messages": [{"role": "user", "content": VISION_PROMPT,
                                  "images": [base64.b64encode(solid_png()).decode()]}],
                    "options": {"temperature": 0, "num_predict": 16},
                })
                if response.status_code != 200:
                    result["vision"] = _result(False, f"HTTP {response.status_code}: {response.text[:200]}", started)
                else:
                    text = str((response.json().get("message") or {}).get("content") or "").strip()
                    result["vision"] = _result(any(a in text.lower() for a in VISION_ANSWERS), f"answer: {text[:80]}", started)
            except httpx.HTTPError as exc:
                result["vision"] = _result(None, _error_text(exc), started)
    return result


# ---- OpenAI-compatible (LM Studio, llama-swap, cloud, tunnels) -----------------------

async def probe_openai(base: str, model: str, *, api_key: str | None = None, headers: dict[str, str] | None = None,
                       timeout: float = 120.0) -> dict[str, Any]:
    request_headers = dict(headers or {})
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"
    result: dict[str, Any] = {"model": model, "declared": None}
    async with httpx.AsyncClient(timeout=timeout, headers=request_headers, trust_env=False) as client:
        started = time.monotonic()
        try:
            response = await client.post(f"{base}/chat/completions", json={
                "model": model, "temperature": 0, "max_tokens": 128, "tools": [TOOL], "tool_choice": "auto",
                "messages": [{"role": "user", "content": TOOL_PROMPT}],
            })
            if response.status_code != 200:
                supported = False if response.status_code in (400, 422) else None
                result["tools"] = _result(supported, f"HTTP {response.status_code}: {response.text[:200]}", started)
            else:
                message = ((response.json().get("choices") or [{}])[0].get("message") or {})
                names = [str((c.get("function") or {}).get("name")) for c in message.get("tool_calls") or []]
                result["tools"] = _result(
                    "get_weather" in names,
                    f"tool_calls={names}" if names else "the model answered in text instead of calling the tool",
                    started)
        except (httpx.HTTPError, ValueError) as exc:
            result["tools"] = _result(None, _error_text(exc), started)

        started = time.monotonic()
        data_uri = "data:image/png;base64," + base64.b64encode(solid_png()).decode()
        try:
            response = await client.post(f"{base}/chat/completions", json={
                "model": model, "temperature": 0, "max_tokens": 16,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]}],
            })
            if response.status_code != 200:
                result["vision"] = _result(False if response.status_code in (400, 422) else None,
                                           f"HTTP {response.status_code}: {response.text[:200]}", started)
            else:
                message = ((response.json().get("choices") or [{}])[0].get("message") or {})
                text = str(message.get("content") or "").strip()
                result["vision"] = _result(any(a in text.lower() for a in VISION_ANSWERS), f"answer: {text[:80]}", started)
        except (httpx.HTTPError, ValueError) as exc:
            result["vision"] = _result(None, _error_text(exc), started)
    return result


def summarize(result: dict[str, Any], runtime: str) -> dict[str, Any]:
    tools = (result.get("tools") or {}).get("supported")
    warnings: list[str] = []
    if runtime == "harness" and tools is not True:
        warnings.append("harness_requires_tools")
    if (result.get("vision") or {}).get("supported") is not True:
        warnings.append("no_vision")
    result["warnings"] = warnings
    result["harness_ok"] = tools is True
    return result


def dumps(value: Any) -> str:  # pragma: no cover - debugging helper
    return json.dumps(value, ensure_ascii=False, indent=2)
