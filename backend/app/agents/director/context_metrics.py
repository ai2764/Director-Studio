"""Opt-in request shape diagnostics. Never persist prompt or media bodies."""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar

from ...config import settings

logger = logging.getLogger("director_studio.context_metrics")
_project = ContextVar("director_metrics_project", default=None)


def context_mode(project) -> str:
    if (project is not None and project.mode == "director"
            and project.id in settings.director_task_context_projects):
        return settings.director_task_context_mode
    return "off"


@contextmanager
def metrics_scope(project_id: str):
    token = _project.set(project_id)
    try:
        yield
    finally:
        _project.reset(token)


def request_shape(*, path: str, messages: list[dict], tools: list[dict],
                  image_count: int, source_keys: list[str]) -> dict:
    texts = [m.get("content", "") for m in messages if isinstance(m.get("content", ""), str)]
    tool_text = json.dumps(tools, ensure_ascii=False, sort_keys=True)
    return {
        "path": path, "message_count": len(messages), "text_chars": sum(map(len, texts)),
        "tool_schema_chars": len(tool_text), "image_count": image_count,
        "measurement": "characters_not_tokens",
        "input_digest": hashlib.sha256(json.dumps([texts, tools], ensure_ascii=False,
                                                   sort_keys=True).encode()).hexdigest(),
        "source_keys": sorted(set(source_keys)),
    }


def record_request_shape(shape: dict) -> None:
    try:
        logger.info("director_request_shape %s", json.dumps(shape, sort_keys=True))
    except Exception:
        # Telemetry must never turn a successful operation into a failed one.
        pass


def observe_request(path: str, messages: list[dict], *, tools=None,
                    image_count: int = 0, source_keys=()) -> None:
    if settings.director_task_context_mode == "off" or _project.get() is None:
        return
    try:
        from ...core.projects.store import load_project
        if context_mode(load_project(_project.get())) == "off":
            return
        record_request_shape(request_shape(path=path, messages=messages, tools=tools or [],
            image_count=image_count, source_keys=list(source_keys)))
    except Exception:
        pass
