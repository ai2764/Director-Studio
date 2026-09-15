"""Python drives an ephemeral Harness turn; all capabilities execute locally here."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


class HarnessError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


class HarnessClient:
    def __init__(self, base_url: str, token: str, *, http=None, timeout=1800):
        from ...config import Settings

        self.url = Settings._loopback_sidecar(base_url)
        if not token:
            raise HarnessError("Harness internal token is missing; launch with start.ps1")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.http = http
        self.timeout = timeout

    async def run(
        self, body: dict, dispatch: Callable[[str, dict], Awaitable[Any]],
        on_progress=None,
    ) -> dict:
        if self.http is not None:
            return await self._run(self.http, body, dispatch, on_progress)
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False, follow_redirects=False) as http:
            return await self._run(http, body, dispatch, on_progress)

    async def _run(self, http, body, dispatch, on_progress):
        if body.get("session_id"):
            health = await http.get(f"{self.url}/health", headers=self.headers, timeout=5)
            health.raise_for_status()
            if not {"native-sessions-v1", "context-envelope-v2"}.issubset(health.json().get("capabilities", [])):
                raise HarnessError("Harness session/context support is outdated; update and restart the sidecar before continuing")
        url = f"{self.url}/turns/{uuid.uuid4()}"
        seen: set[str] = set()
        pending_read = None
        try:
            async with asyncio.timeout(self.timeout):
                async with http.stream("POST", url, headers=self.headers, json=body) as response:
                    response.raise_for_status()
                    lines = response.aiter_lines().__aiter__()
                    pending_read = asyncio.create_task(anext(lines, None))
                    while (line := await pending_read) is not None:
                        # One-line lookahead detects a dead sidecar while Python is
                        # awaiting a model/tool, so cancellation releases its lease.
                        pending_read = asyncio.create_task(anext(lines, None))
                        if not line.strip():
                            continue
                        if len(line) > 4_000_000:
                            raise HarnessError("Harness response exceeded transport limit")
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise HarnessError("Invalid Harness event")
                        kind = event.get("type")
                        if kind == "request":
                            request_id = event.get("id")
                            if (not isinstance(request_id, str) or not request_id
                                    or len(request_id) > 100
                                    or not all(c.isalnum() or c in "-_" for c in request_id)
                                    or request_id in seen or len(seen) >= 256):
                                raise HarnessError("Invalid or duplicate Harness request")
                            seen.add(request_id)
                            if event.get("method") not in {"context", "llm", "tool"} or not isinstance(event.get("params"), dict):
                                raise HarnessError("Unsupported Harness capability request")
                            try:
                                data = await self._dispatch_connected(dispatch, event, pending_read)
                                reply = {"ok": True, "data": data}
                            except HarnessError:
                                raise
                            except Exception as exc:
                                status = getattr(exc, "status_code", None)
                                if isinstance(exc, httpx.HTTPStatusError):
                                    status = exc.response.status_code
                                retryable = event["method"] == "llm" and (
                                    isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout))
                                    or status in {429, 502, 503, 504}
                                )
                                overflow = event["method"] == "llm" and re.search(
                                    r"context[\s_-]+(?:length|window).*(?:exceed|overflow|limit)|(?:exceed|maximum).*context[\s_-]+(?:length|window)",
                                    str(exc), re.I,
                                )
                                # Capability errors are model-visible; cancellation is never caught.
                                reply = {"ok": False, "error": {
                                    "code": "CONTEXT_WINDOW_EXCEEDED" if overflow else "TRANSIENT_LLM" if retryable else "CAPABILITY_ERROR",
                                    "message": str(exc)[:1000], "retryable": retryable,
                                }}
                            acknowledgement = await http.post(
                                f"{url}/responses/{request_id}", headers=self.headers, json=reply,
                            )
                            acknowledgement.raise_for_status()
                        elif kind == "result":
                            if not isinstance(event.get("reply"), str):
                                raise HarnessError("Invalid Harness final response")
                            # Domain objects and predicted actions never cross this boundary.
                            result = {"reply": event["reply"], "thinking": str(event.get("thinking") or "")}
                            if body.get("operation") == "compact" and isinstance(event.get("compaction"), dict):
                                result["compaction"] = event["compaction"]
                            return result
                        elif kind == "error":
                            code = str(event.get("code") or "HARNESS_ERROR")
                            raise HarnessError(
                                f"Harness {code}: {event.get('message', '')}",
                                code=code,
                            )
                        elif kind in {"status", "runtime"} and on_progress:
                            await on_progress({"type": kind, "text": str(event.get("text") or "")[:2000]})
                    raise HarnessError("Harness turn interrupted; no final result received. Completed tools were not replayed.")
        except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            raise HarnessError("Harness unavailable or turn interrupted; no automatic replay or legacy fallback.") from exc
        finally:
            if pending_read is not None:
                pending_read.cancel()
                await asyncio.gather(pending_read, return_exceptions=True)
            # Explicit cancel also covers lost POST acknowledgements and malformed streams.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(3):
                    await http.delete(url, headers=self.headers, timeout=2)

    @staticmethod
    async def _dispatch_connected(dispatch, event, pending_read):
        operation = asyncio.create_task(dispatch(event["method"], event["params"]))
        try:
            await asyncio.wait({operation, pending_read}, return_when=asyncio.FIRST_COMPLETED)
            if not operation.done():
                raise HarnessError("Harness turn interrupted during capability execution; no automatic replay.")
            return await operation
        finally:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
