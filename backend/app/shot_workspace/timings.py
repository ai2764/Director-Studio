"""Per-shot operation timings, separate from prompt input freshness."""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from ..core.projects.store import project_dir
from ..core.managed_runs.store import _project_lock


_active_operations = {}


def has_active_operation(project_id):
    return _active_operations.get(project_id, 0) > 0


def history(shot):
    path = project_dir(shot.project_id) / "shot_workspace" / "timings" / f"{shot.id}.json"
    with _project_lock(shot.project_id):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def record(shot, stage, started, status):
    entry = {"id": uuid.uuid4().hex, "stage": stage, "duration_s": round(max(0, time.monotonic()-started), 2),
        "status": status, "finished_at": datetime.now(timezone.utc).isoformat()}
    try:
        with _project_lock(shot.project_id):
            records = history(shot)
            records.append(entry)
            path = project_dir(shot.project_id) / "shot_workspace" / "timings" / f"{shot.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(records), encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    except Exception:
        logging.getLogger(__name__).exception("Could not persist operation timing")
    return entry


def timed(stage):
    def decorate(fn):
        @wraps(fn)
        async def run(shot_id, *args, **kwargs):
            from .router import find_shot
            shot = find_shot(shot_id)
            started = time.monotonic()
            status = "failed"
            operation = stage
            result = None
            _active_operations[shot.project_id] = _active_operations.get(shot.project_id, 0) + 1
            try:
                result = await fn(shot_id, *args, **kwargs)
                status = "completed"
                if stage == "assistant" and result.get("kind") in {"draft", "updated"}: operation = "refinement"
                elif stage == "assistant" and result.get("kind") == "prompt": operation = "prompt_planning"
                return result
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            finally:
                entry = record(shot, operation, started, status)
                if isinstance(result, dict): result["operation_duration_s"] = entry["duration_s"]
                _active_operations[shot.project_id] -= 1
                if not _active_operations[shot.project_id]:
                    _active_operations.pop(shot.project_id, None)
        return run
    return decorate
