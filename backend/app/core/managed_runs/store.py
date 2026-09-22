"""Project-local storage and validation for managed H3 runs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from ..projects.store import list_shots, load_project, project_dir
from ...pipelines.h3_ref2va.resolutions import resolve_local_resolution
from .models import ManagedRun, RunStep

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(project_id, threading.RLock())


def _run_dir(project_id: str) -> Path:
    return project_dir(project_id) / "managed_runs"


def _fingerprint(project_id: str) -> str:
    project = load_project(project_id)
    if project is None:
        raise ValueError("Project not found")
    shots = list_shots(project_id)
    if [shot.id for shot in shots] != project.shot_ids:
        raise ValueError("Project Shot list is incomplete")
    authored = [
        {
            "shot_id": shot.id,
            "title": shot.title,
            "script_beat": shot.script_beat,
            "shot_type": shot.shot_type,
            "camera_angle": shot.camera_angle,
            "camera_motion": shot.camera_motion,
            "composition": shot.composition,
            "duration_s": shot.duration_s,
            "dialogue": shot.dialogue,
            "refs": [ref.model_dump(mode="json") for ref in shot.refs],
            "voice_refs": [ref.model_dump(mode="json") for ref in shot.voice_refs],
        }
        for shot in shots
    ]
    payload = json.dumps([project.script_text, project.shot_ids, authored], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_steps(project_id: str, steps: list[RunStep]) -> None:
    shots = list_shots(project_id)
    ordered_ids = [shot.id for shot in shots]
    if not ordered_ids or [step.shot_id for step in steps] != ordered_ids:
        raise ValueError("Run plan must list every current Shot in project order")
    preceding: set[str] = set()
    for step in steps:
        if step.tail_from_shot_id:
            if step.tail_from_shot_id not in preceding:
                raise ValueError("Tail source must be an earlier Shot in this plan")
            if not step.tail_reason.strip():
                raise ValueError("Every tail handoff needs a reason")
        preceding.add(step.shot_id)


def _save_run(run: ManagedRun) -> ManagedRun:
    directory = _run_dir(run.project_id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{run.run_id}.json"
    temporary = directory / f".{run.run_id}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return run


def load_run(project_id: str, run_id: str) -> ManagedRun | None:
    if not run_id.startswith("mrun_") or not run_id[5:].isalnum():
        return None
    path = _run_dir(project_id) / f"{run_id}.json"
    if not path.is_file():
        return None
    return ManagedRun.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs(project_id: str) -> list[ManagedRun]:
    directory = _run_dir(project_id)
    if not directory.is_dir():
        return []
    runs = [ManagedRun.model_validate_json(path.read_text(encoding="utf-8"))
            for path in directory.glob("mrun_*.json")]
    return sorted(runs, key=lambda run: (run.created_at, run.run_id), reverse=True)


def create_draft(project_id: str, steps: list[RunStep]) -> ManagedRun:
    with _project_lock(project_id):
        _validate_steps(project_id, steps)
        run = ManagedRun(
            run_id=f"mrun_{uuid.uuid4().hex}", project_id=project_id,
            steps=steps, plan_fingerprint=_fingerprint(project_id),
        )
        return _save_run(run)


def activate_run(project_id: str, run_id: str, preset: str) -> ManagedRun:
    resolve_local_resolution(preset)
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if run.state != "draft":
            raise ValueError("Only a draft plan can start")
        if run.plan_fingerprint != _fingerprint(project_id):
            raise ValueError("Shot brief or project order changed since this plan")
        if any(item.state in {"active", "stopping"} for item in list_runs(project_id)):
            raise ValueError("Another managed run is active for this project")
        return _save_run(run.model_copy(update={"state": "active", "resolution_preset": preset}))


def request_stop(project_id: str, run_id: str) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if run.state in {"stopped", "completed"}:
            return run
        return _save_run(run.model_copy(update={"state": "stopping"}))


def finish_stop(project_id: str, run_id: str) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if run.state == "completed":
            return run
        return _save_run(run.model_copy(update={"state": "stopped"}))
