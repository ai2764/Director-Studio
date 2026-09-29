"""Persist / load Director agent context under data/projects/<id>/agent/."""

from __future__ import annotations

from pathlib import Path

from ...config import settings
from ...core.projects.models import AgentContext


def agent_dir(project_id: str) -> Path:
    from ...core.paths import ensure_project_tree

    ensure_project_tree(project_id)
    return settings.projects_dir / project_id / "agent"


def context_path(project_id: str) -> Path:
    return agent_dir(project_id) / "context.json"


def save_agent_context(project_id: str, context: AgentContext) -> Path:
    """Publish durable context under the same lock as project/shot decisions."""
    from ...core.managed_runs.store import _project_lock
    from ...core.projects.store import _atomic_model_write
    with _project_lock(project_id):
        path = context_path(project_id)
        payload = context.model_copy(update={"project_id": project_id})
        _atomic_model_write(path, payload)
        return path


def load_agent_context(project_id: str) -> AgentContext | None:
    path = context_path(project_id)
    if not path.exists():
        return None
    return AgentContext.model_validate_json(path.read_text(encoding="utf-8"))
