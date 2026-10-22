"""Metadata-only phase timings for local performance comparisons."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import monotonic

from ...config import settings

_phase = ContextVar("llm_performance_phase", default=None)
_progress = ContextVar("llm_performance_progress", default=None)
_write_lock = threading.Lock()
logger = logging.getLogger(__name__)


@contextmanager
def phase_scope(phase, on_progress):
    phase_token = _phase.set(phase)
    progress_token = _progress.set(on_progress)
    try:
        yield
    finally:
        _phase.reset(phase_token)
        _progress.reset(progress_token)


def record_performance(row):
    """Bound storage to two 5 MiB files; diagnostics cannot break inference."""
    try:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _write_lock:
            path = settings.data_dir / "logs" / "llm-performance.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size >= 5 * 1024 * 1024:
                path.replace(path.with_suffix(".previous.jsonl"))
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except Exception:
        logger.debug("Performance telemetry unavailable", exc_info=True)


@asynccontextmanager
async def measure_call(path, *, model, image_count, text_chars):
    started = monotonic()
    call = {}
    status = "completed"
    try:
        yield call
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        raise
    finally:
        result = call.get("result")
        usage = (result.get("usage") or {}) if isinstance(result, dict) else {}
        content = result.get("content", "") if isinstance(result, dict) else result
        row = {"kind": "llm_call", "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": path, "phase": _phase.get() or path, "model": model,
            "image_count": image_count, "text_chars": text_chars,
            "elapsed_ms": round((monotonic() - started) * 1000), "status": status,
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "output_chars": len(content or ""),
            "finish_reason": result.get("finish_reason") if isinstance(result, dict) else None}
        record_performance(row)
        callback = _progress.get()
        if callback:
            try:
                await callback({"type": "status", "phase": "llm_call", "state": status,
                    "text": f"{row['phase']}: {row['elapsed_ms'] / 1000:.1f}s, {image_count} image(s), {text_chars} text chars",
                    "metrics": row})
            except Exception:
                pass
