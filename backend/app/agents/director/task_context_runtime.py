"""Temporary per-turn view. Not a new workflow, budget or project authority."""
from contextlib import contextmanager
from contextvars import ContextVar

from .context_metrics import context_mode

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
