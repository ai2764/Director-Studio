"""Authored directing history cannot change inside a planning commit."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.agents.director.context_io import load_agent_context, save_agent_context
from app.core.managed_runs.store import _project_lock
from app.core.projects.models import AgentContext
from app.core.projects.store import create_project


def test_directing_context_writer_waits_for_project_publication_lock():
    project = create_project("Concurrency", "Reaction")
    initial = AgentContext(project_id=project.id, script_hash="", extra={"directing_brief": {"messages": ["Old"]}})
    save_agent_context(project.id, initial)
    started = Event()
    revised = initial.model_copy(update={"extra": {"directing_brief": {"messages": ["New"]}}})
    def write():
        started.set()
        save_agent_context(project.id, revised)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _project_lock(project.id):
            future = pool.submit(write)
            assert started.wait(2)
            # A bounded wait proves the write cannot complete while the lock is held.
            from concurrent.futures import TimeoutError
            try:
                future.result(timeout=0.1)
            except TimeoutError:
                pass
            assert load_agent_context(project.id) == initial
        future.result(timeout=2)
    assert load_agent_context(project.id) == revised
