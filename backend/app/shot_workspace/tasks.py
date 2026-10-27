"""Small durable task receipts; refreshing the page never restarts a model call."""
import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
import json

from ..core.projects.store import project_dir
from ..core.projects.chat_sessions import director_chat_sessions

_tasks = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path(project_id):
    return project_dir(project_id) / "shot_workspace" / "task.json"


def _save(project_id, state):
    path = _path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def status(project_id):
    path = _path(project_id)
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if state and state["status"] == "running" and project_id not in _tasks:
        state.update(status="interrupted", updated_at=_now(), error="The service restarted. Retry prompt writing when ready.")
        _save(project_id, state)
    return state


def shot_has_active_prompt_task(shot):
    task = _tasks.get(shot.project_id)
    state = status(shot.project_id)
    return bool(task and not task.done() and state and state["shot_id"] == shot.id)


async def start(project_id, shot_id, request_id):
    previous = status(project_id)
    if previous and previous["request_id"] == request_id:
        if previous["shot_id"] != shot_id:
            raise ValueError("This request ID belongs to another shot.")
        return previous
    session = await director_chat_sessions.reserve(project_id)
    state = {"request_id": request_id, "shot_id": shot_id, "status": "running", "error": "",
             "started_at": _now(), "updated_at": _now(), "run_id": session.session_id}
    _save(project_id, state)
    async def run():
        try:
            from ..core.vram import get_orchestrator
            from ..config import settings
            from .writer import write_prompt
            orch = get_orchestrator()
            async with orch.llm_session(release_on_exit=not settings.llm_keep_loaded):
                await orch.ensure_llm_ready()
                await write_prompt(project_id, shot_id)
            state.update(status="completed")
        except asyncio.CancelledError:
            state.update(status="cancelled", error="Prompt writing cancelled. Saved shot content was kept.")
        except Exception as exc:
            state.update(status="failed", error=str(exc))
        finally:
            state["updated_at"] = _now()
            # A cancellation can release the project before this runner exits.
            # Never replace a newer action's receipt or task registration.
            from ..core.managed_runs.store import _project_lock
            with _project_lock(project_id):
                current = json.loads(_path(project_id).read_text(encoding="utf-8"))
                if current.get("run_id") == state["run_id"]:
                    _save(project_id, state)
            await director_chat_sessions.finish(project_id, session.session_id)
            if _tasks.get(project_id) is asyncio.current_task():
                _tasks.pop(project_id, None)
    task = asyncio.create_task(run())
    _tasks[project_id] = task
    await director_chat_sessions.attach(project_id, session.session_id, task)
    return state


async def cancel(project_id):
    await director_chat_sessions.cancel(project_id)
    return status(project_id)


async def close():
    for project_id in list(_tasks):
        await cancel(project_id)
