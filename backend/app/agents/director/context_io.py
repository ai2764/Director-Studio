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
    """Write context.json before every release_llm / Comfy handoff."""
    d = agent_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    path = context_path(project_id)
    # Ensure project_id on the model matches the path
    payload = context.model_copy(update={"project_id": project_id})
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_agent_context(project_id: str) -> AgentContext | None:
    path = context_path(project_id)
    if not path.exists():
        return None
    return AgentContext.model_validate_json(path.read_text(encoding="utf-8"))
