"""Temporary per-turn view. Not a new workflow, budget or project authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json

from ...config import settings
from ...core.projects.store import load_project
from .context_metrics import context_mode, metrics_scope, observe_request
from .task_context_models import TaskContextState, TaskRequest
from .task_context_builder import build_task_packet
from .task_context_snapshot import capture_task_snapshot

_task_state = ContextVar("director_task_context", default=None)


def task_context_enabled(project) -> bool:
    return context_mode(project) != "off"


@contextmanager
def task_context_scope(state):
    token = _task_state.set(state)
    try:
        yield state
    finally:
        _task_state.reset(token)


def current_task_context(project_id):
    state = _task_state.get()
    return state if state is not None and state.project_id == project_id else None


def scoped_task_turn(function):
    """Wrap existing entry points, including all exceptional/cancellation exits."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        project = load_project(kwargs["project_id"])
        from .intent import explicit_gpt_image_intent
        if not task_context_enabled(project) or explicit_gpt_image_intent(kwargs.get("message", "")):
            return await function(*args, **kwargs)
        state = current_task_context(project.id) or TaskContextState(project_id=project.id,
            request=TaskRequest(objective=kwargs.get("message", "")), retrieved_versions={})
        with task_context_scope(state), metrics_scope(project.id):
            return await function(*args, **kwargs)
    return wrapped


def present_task_tools(authorized_tools, state):
    from .tool_schema import TASK_CONTEXT_TOOLS
    selected = authorized_tools
    if state.request.kind == "shot_prompt":
        selected = [tool for tool in authorized_tools
                    if tool["function"]["name"] in {"write_prompt", "get_status", "inspect_asset"}]
    return [*selected, *TASK_CONTEXT_TOOLS]


def task_authority(project, tools):
    from ...core.managed_runs.context import managed_turn_scope
    managed = managed_turn_scope.get()
    return {"script_locked": project.script_locked,
        "managed_shot_id": managed.shot_id if managed and managed.project_id == project.id else None,
        "authorized_tools": [{"name": t["function"]["name"], "parameters": t["function"]["parameters"]}
                             for t in tools]}


def available_packet_chars(*, system, messages, tools, image_count=0, context_capacity=None):
    """Same labelled 4 chars/token heuristic and image allowance as Harness."""
    capacity = context_capacity or settings.director_num_ctx
    chars = (capacity - settings.director_num_predict - image_count * 2048) * 4
    used = len(system) + len(json.dumps(messages, ensure_ascii=False)) + len(json.dumps(tools, ensure_ascii=False))
    return max(0, chars - used)


def render_task_context(project, *, objective, authority, legacy_state, max_chars):
    mode = context_mode(project)
    if mode == "off":
        return legacy_state
    state = current_task_context(project.id)
    request = state.request if state else TaskRequest(objective=objective)
    try:
        snapshot = capture_task_snapshot(project.id)
        packet = build_task_packet(snapshot, request, authority=authority, max_chars=max_chars,
                                   extra_sources=tuple(state.retrieved_versions) if state else ())
        rendered = json.dumps(packet.model_dump(mode="json"), ensure_ascii=False)
        observe_request(f"task_context.{mode}", [{"role": "system", "content": rendered}],
                        source_keys=packet.source_versions)
    except Exception:
        if mode == "shadow":
            return legacy_state  # Shadow cannot change the inference/operation outcome.
        raise
    return legacy_state if mode == "shadow" else rendered
