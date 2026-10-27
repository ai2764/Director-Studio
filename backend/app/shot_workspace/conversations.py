"""Durable per-shot description conversations, separate from model context."""
import json
import uuid
from datetime import datetime, timezone
from ..core.projects.store import project_dir
from ..core.managed_runs.store import _project_lock


def _path(shot):
    return project_dir(shot.project_id) / "shot_workspace" / "conversations" / f"{shot.id}.json"


def history(shot):
    with _project_lock(shot.project_id):
        path = _path(shot)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def append(shot, role, text, *, proposal=None, scope=None):
    with _project_lock(shot.project_id):
        messages = history(shot)
        message = {"id": uuid.uuid4().hex, "role": role, "text": text,
            "created_at": datetime.now(timezone.utc).isoformat()}
        if proposal:
            message["proposal"] = proposal
        if scope:
            message["scope"] = scope
        messages.append(message)
        path = _path(shot)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return message
