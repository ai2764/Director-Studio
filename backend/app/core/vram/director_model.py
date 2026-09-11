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

_override: tuple[str, str] | None = None


def _persist_path() -> Path:
    return settings.data_dir / "director_model.json"


def _active_provider(provider_id: str | None = None) -> str:
    return (provider_id or settings.llm_provider or "ollama").strip()


def _read_persisted() -> tuple[str | None, str] | None:
    path = _persist_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("model") or "").strip()
        if not name:
            return None
        provider = str(data.get("provider") or "").strip() or None
        return provider, name
    except Exception:
        logger.exception("failed to read %s", path)
        return None


def _write_persisted(provider: str, model: str) -> None:
    path = _persist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"provider": provider, "model": model},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def get_director_model(provider_id: str | None = None) -> str:
    """Model name used for plan / chat / wake / unload."""
    provider = _active_provider(provider_id)
    if _override and _override[0] == provider:
        return _override[1]
    persisted = _read_persisted()
    if persisted:
        persisted_provider, persisted_model = persisted
        if persisted_provider == provider or (
            persisted_provider is None and provider == "ollama"
        ):
            return persisted_model
    if provider == "ollama":
        return (settings.director_plan_model or "").strip()
    return ""


def set_director_model(
    model: str,
    *,
    provider_id: str | None = None,
    persist: bool = True,
) -> str:
    """
    Switch Director LLM at runtime.

    Updates orchestrator unload/warm list and optionally persists across restarts.
    """
    global _override
    name = (model or "").strip()
    if not name:
        raise ValueError("model name must be non-empty")

    provider = _active_provider(provider_id)
    _override = (provider, name)
    if persist:
        _write_persisted(provider, name)

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


def clear_director_model_override(
    *,
    provider_id: str | None = None,
    remove_persisted: bool = False,
) -> str:
    """Fall back to .env default (and optional delete of data/director_model.json)."""
    global _override
    _override = None
    if remove_persisted:
        path = _persist_path()
        if path.is_file():
            path.unlink()
    return get_director_model(provider_id)


def model_status(provider_id: str | None = None) -> dict[str, Any]:
    provider = _active_provider(provider_id)
    persisted_state = _read_persisted()
    persisted = None
    if persisted_state:
        persisted_provider, persisted_model = persisted_state
        if persisted_provider == provider or (
            persisted_provider is None and provider == "ollama"
        ):
            persisted = persisted_model
    override = _override[1] if _override and _override[0] == provider else None
    return {
        "provider": provider,
        "model": get_director_model(provider),
        "override": override,
        "persisted": persisted,
        "env_default": settings.director_plan_model if provider == "ollama" else "",
        "source": (
            "runtime"
            if override
            else ("persisted" if persisted else "env")
        ),
    }
