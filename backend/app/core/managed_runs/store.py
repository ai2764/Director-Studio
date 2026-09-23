"""Project-local storage and validation for managed H3 runs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from ..projects.store import list_shots, load_project, project_dir
from ..schemas import JobStatus
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


def active_run_for_project(project_id: str) -> ManagedRun | None:
    return next((run for run in list_runs(project_id) if run.state == "active"), None)


def bind_job(project_id: str, run_id: str, shot_id: str, job_id: str,
             *, expected_fingerprint: str | None = None) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None or run.state != "active":
            raise ValueError("Managed run was stopped before the H3 job could be bound")
        if run.current_index >= len(run.steps) or run.steps[run.current_index].shot_id != shot_id:
            raise ValueError("H3 job is not for the next planned Shot")
        if run.current_job_id is not None:
            raise ValueError("The next planned Shot already has an H3 job")
        current_fingerprint = _fingerprint(project_id)
        if expected_fingerprint and (
            run.current_fingerprint != expected_fingerprint
            or current_fingerprint != expected_fingerprint
        ):
            raise ValueError("Shot brief or references changed during H3 preflight")
        return _save_run(run.model_copy(update={
            "current_job_id": job_id, "pending_event_id": None,
            "current_fingerprint": current_fingerprint,
        }))


def record_terminal(project_id: str, job_id: str, status: JobStatus, error: str = "") -> ManagedRun | None:
    """Advance only the active run that bound this exact job, once."""
    if status not in {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}:
        return None
    with _project_lock(project_id):
        run = active_run_for_project(project_id)
        if run is None:
            return None
        if run.current_job_id != job_id:
            return run if job_id in run.completed_job_ids.values() else None
        if status != JobStatus.succeeded:
            return _save_run(run.model_copy(update={
                "state": "paused", "current_job_id": None, "pending_event_id": None,
                "paused_reason": error or f"H3 job {job_id} {status.value}",
            }))
        step = run.steps[run.current_index]
        completed = dict(run.completed_job_ids)
        completed[step.shot_id] = job_id
        next_index = run.current_index + 1
        return _save_run(run.model_copy(update={
            "current_index": next_index,
            "current_job_id": None,
            "completed_job_ids": completed,
            "prompt_retry_count": 0,
            "prompt_retry_error": "",
            "pending_event_id": f"{job_id}:{status.value}" if next_index < len(run.steps) else None,
            "state": "active" if next_index < len(run.steps) else "completed",
        }))


def mark_tail_ready(project_id: str, run_id: str, shot_id: str, layout_id: str) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None or run.state != "active" or run.steps[run.current_index].shot_id != shot_id:
            raise ValueError("Managed run changed while preparing tail frame")
        prepared = dict(run.prepared_tail_layout_ids)
        prepared[shot_id] = layout_id
        return _save_run(run.model_copy(update={
            "prepared_tail_layout_ids": prepared,
            "current_fingerprint": _fingerprint(project_id),
        }))


def pause_run(project_id: str, run_id: str, reason: str) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if run.state != "active":
            return run
        return _save_run(run.model_copy(update={
            "state": "paused", "pending_event_id": None, "paused_reason": reason,
        }))


def record_prompt_retry(project_id: str, run_id: str, error: str) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None or run.state != "active" or run.current_job_id:
            raise ValueError("Managed run is no longer awaiting a prompt")
        if run.prompt_retry_count >= 1:
            raise ValueError("Managed prompt retry budget reached")
        return _save_run(run.model_copy(update={
            "prompt_retry_count": run.prompt_retry_count + 1,
            "prompt_retry_error": error[:1000],
        }))


def create_draft(project_id: str, steps: list[RunStep]) -> ManagedRun:
    with _project_lock(project_id):
        _validate_steps(project_id, steps)
        run = ManagedRun(
            run_id=f"mrun_{uuid.uuid4().hex}", project_id=project_id,
            steps=steps, plan_fingerprint=_fingerprint(project_id),
            current_fingerprint=_fingerprint(project_id),
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
        return _save_run(run.model_copy(update={"state": "active", "resolution_preset": preset, "pending_event_id": "start"}))


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
