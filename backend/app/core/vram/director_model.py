"""Runtime-selectable Director Ollama model (no service restart).

Priority:
1. In-process override (set via API / set_director_model)
2. Persisted choice under data/director_model.json
3. settings.director_plan_model (.env DS_DIRECTOR_PLAN_MODEL)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...config import settings

logger = logging.getLogger("director_studio.director_model")

_override: str | None = None


def _persist_path() -> Path:
    return settings.data_dir / "director_model.json"


def _read_persisted() -> str | None:
    path = _persist_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        name = (data.get("model") or "").strip()
        return name or None
    except Exception:
        logger.exception("failed to read %s", path)
        return None


def _write_persisted(model: str) -> None:
    path = _persist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"model": model}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_director_model() -> str:
    """Model name used for plan / chat / wake / unload."""
    if _override:
        return _override
    persisted = _read_persisted()
    if persisted:
        return persisted
    return (settings.director_plan_model or "").strip()


def set_director_model(model: str, *, persist: bool = True) -> str:
    """
    Switch Director LLM at runtime.

    Updates orchestrator unload/warm list and optionally persists across restarts.
    """
    global _override
    name = (model or "").strip()
    if not name:
        raise ValueError("model name must be non-empty")

    _override = name
    if persist:
        _write_persisted(name)

    # Keep VRAM orchestrator in sync (unload/warm the active model).
    try:
        from .orchestrator import get_orchestrator

        orch = get_orchestrator()
        orch.models = [name]
        orch._llm_ready = False  # force re-warm on next llm_session
    except Exception:
        logger.exception("failed to sync orchestrator models to %s", name)

    logger.info("director plan model set to %s (persist=%s)", name, persist)
    return name


def clear_director_model_override(*, remove_persisted: bool = False) -> str:
    """Fall back to .env default (and optional delete of data/director_model.json)."""
    global _override
    _override = None
    if remove_persisted:
        path = _persist_path()
        if path.is_file():
            path.unlink()
    return get_director_model()


def model_status() -> dict[str, Any]:
    return {
        "model": get_director_model(),
        "override": _override,
        "persisted": _read_persisted(),
        "env_default": settings.director_plan_model,
        "source": (
            "runtime"
            if _override
            else ("persisted" if _read_persisted() else "env")
        ),
    }
