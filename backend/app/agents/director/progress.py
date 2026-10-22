"""Observable inference phases using the existing chat progress channel."""
import logging
from contextlib import asynccontextmanager
from time import monotonic
from ...core.llm.performance import phase_scope, record_performance

logger = logging.getLogger(__name__)


async def emit_progress(on_progress, event):
    """Progress delivery must never fail the underlying work."""
    if on_progress:
        try:
            await on_progress(event)
        except Exception:
            logger.exception("Prompt progress callback failed")


@asynccontextmanager
async def report_phase(on_progress, phase, label):
    started = monotonic()

    async def emit(state):
        elapsed = monotonic() - started
        text = f"{label}…" if state == "started" else f"{label}: {state} ({elapsed:.1f}s)"
        if state != "started":
            logger.info("%s %s in %.1fs", phase, state, elapsed)
            record_performance({"kind": "phase", "phase": phase, "path": "phase_total",
                "status": state, "elapsed_ms": round(elapsed * 1000)})
        await emit_progress(on_progress, {"type": "status", "text": text, "phase": phase,
                                         "state": state, "elapsed_s": elapsed})

    await emit("started")
    try:
        with phase_scope(phase, on_progress):
            yield
    except BaseException:
        await emit("failed")
        raise
    else:
        await emit("completed")
