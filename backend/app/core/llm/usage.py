"""Debug telemetry for native chat calls; never controls inference or retries."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid


def _text_tokens(value) -> int:
    # A deliberately labelled heuristic, not the provider's tokenizer. Image
    # payloads are omitted rather than pricing base64 as natural language.
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return math.ceil(len(text) / 4) if text else 0


class ChatUsageReporter:
    def __init__(self, on_progress, *, provider: str, context_window=None, output_limit=None, capacity_source=None):
        self.on_progress = on_progress
        self.provider = provider
        self.context_window = context_window if context_window and context_window > 0 else None
        self.output_limit = output_limit if output_limit and output_limit > 0 else None
        self.capacity_source = capacity_source
        self.sequence = 0

    def set_context_capacity(self, context_window, source) -> None:
        self.context_window = context_window if context_window and context_window > 0 else None
        self.capacity_source = source if self.context_window else None

    async def call(self, client, model, *, purpose="turn", **request):
        if self.on_progress is None:
            return await client.chat_response(model, **request)
        self.sequence += 1
        output_limit = (request.get("options") or {}).get("num_predict", self.output_limit)
        messages = request["messages"]
        parts = {"system": 0, "conversation": 0, "tools": 0, "format": 0}
        for message in messages:
            text = message.get("content", "")
            if isinstance(text, list):
                text = [part.get("text", "") for part in text if part.get("type") == "text"]
            key = "system" if message.get("role") == "system" else "conversation"
            parts[key] += _text_tokens(text) + 8
            if message.get("tool_calls"):
                parts[key] += _text_tokens(message["tool_calls"])
        for key in ("tools", "format"):
            if request.get(key):
                parts[key] = _text_tokens(request[key]) + 4
        image_count = sum(len(m.get("images") or []) for m in messages)
        image_count += sum(
            sum(part.get("type") in {"image", "image_url"} for part in m["content"])
            for m in messages if isinstance(m.get("content"), list)
        )
        data = {
            "call_id": uuid.uuid4().hex, "sequence": self.sequence,
            "purpose": "compaction" if purpose == "compaction" else "turn",
            "provider": self.provider, "model": model, "status": "running",
            "context_window": self.context_window, "capacity_source": self.capacity_source,
            "output_limit": output_limit,
            "input_budget": max(0, self.context_window - output_limit)
                if self.context_window and output_limit else None,
            "estimated_input_tokens": sum(parts.values()), "estimated_parts": parts,
            "image_count": image_count, "input_tokens": None, "output_tokens": None,
            "reasoning_tokens": None, "thinking_chars": None, "content_chars": None,
            "tool_calls": None, "finish_reason": None, "elapsed_ms": 0,
        }
        started = time.monotonic()

        async def emit(**updates):
            await self.on_progress({"type": "context_usage", "data": {
                **data, **updates, "elapsed_ms": round((time.monotonic() - started) * 1000),
            }})

        await emit()
        try:
            result = await client.chat_response(model, **request)
        except asyncio.CancelledError:
            await emit(status="cancelled")
            raise
        except Exception as exc:
            overflow = re.search(
                r"context[_ -]+(?:length|window).*(?:exceed|overflow|limit)|(?:exceed|maximum).*context[_ -]+(?:length|window)",
                str(exc), re.I,
            )
            await emit(status="context_overflow" if overflow else "failed")
            raise
        reason = result.get("finish_reason") or result.get("done_reason") or None
        usage = result.get("usage") or {}
        await emit(
            status="output_truncated" if reason == "length" else "completed",
            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
            reasoning_tokens=usage.get("reasoning_tokens"), finish_reason=reason,
            thinking_chars=len(result.get("thinking") or ""),
            content_chars=len(result.get("content") or ""),
            tool_calls=len(result.get("tool_calls") or []),
        )
        return result
