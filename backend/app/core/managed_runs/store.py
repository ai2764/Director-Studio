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
from .selection import build_execution_batch

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(project_id, threading.RLock())


def _run_dir(project_id: str) -> Path:
    return project_dir(project_id) / "managed_runs"


def _authored_shot_payload(shots, *, legacy: bool) -> list[dict]:
    authored = []
    for shot in shots:
        managed_layout_assets = {
            str(layout.asset_id)
            for layout in shot.layout_refs
            if layout.asset_id and layout.feedback_source == "managed_run"
        }
        refs = [
            ref.model_dump(mode="json")
            for ref in shot.refs
            if legacy or not (
                ref.role.value == "layout_ref_frame"
                and ref.asset_id in managed_layout_assets
            )
        ]
        item = {
            "shot_id": shot.id,
            "title": shot.title,
            "script_beat": shot.script_beat,
            "shot_type": shot.shot_type,
            "camera_angle": shot.camera_angle,
            "camera_motion": shot.camera_motion,
            "composition": shot.composition,
            "duration_s": shot.duration_s,
            "dialogue": shot.dialogue,
            "refs": refs,
            "voice_refs": [ref.model_dump(mode="json") for ref in shot.voice_refs],
        }
        if not legacy:
            if shot.dialogue_lines is not None:
                item["dialogue_lines"] = [line.model_dump(mode="json") for line in shot.dialogue_lines]
            item.update({
                "scene_id": shot.scene_id,
                "music_segment": (
                    shot.music_segment.model_dump(mode="json")
                    if shot.music_segment else None
                ),
                "source_audio_path": shot.source_audio_path,
            })
        authored.append(item)
    return authored


def _project_fingerprint(project_id: str, *, legacy: bool, version: int = 2) -> str:
    project = load_project(project_id)
    if project is None:
        raise ValueError("Project not found")
    shots = list_shots(project_id)
    if [shot.id for shot in shots] != project.shot_ids:
        raise ValueError("Project Shot list is incomplete")
    authored = _authored_shot_payload(shots, legacy=legacy)
    if version == 1:
        for item in authored:
            item.pop("scene_id", None)
            item.pop("dialogue_lines", None)
    fingerprint_items = [project.script_text, project.shot_ids, authored]
    if not legacy and version >= 2:
        from ...agents.director.brief import directing_requests
        requests = directing_requests(project)
        if requests:
            fingerprint_items.append(requests)
    if not legacy:
        fingerprint_items.insert(
            1,
            project.music_master.content_sha256 if project.music_master else None,
        )
    payload = json.dumps(
        fingerprint_items,
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fingerprint(project_id: str) -> str:
    return _project_fingerprint(project_id, legacy=False)


def _legacy_fingerprint(project_id: str) -> str:
    return _project_fingerprint(project_id, legacy=True)


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


def _decode_run(project_id: str, content: str) -> ManagedRun:
    payload = json.loads(content)
    run = ManagedRun.model_validate(payload)
    if "pending_shot_ids" in payload:
        if payload.get("fingerprint_version", 1) < 2:
            with _project_lock(project_id):
                updates = {"fingerprint_version": 2}
                try:
                    previous = _project_fingerprint(project_id, legacy=False, version=1)
                    # Fields absent from the old contract cannot authorize new attribution.
                    from ...agents.director.brief import directing_requests
                    project = load_project(project_id)
                    compatible = (not directing_requests(project)
                        and all(shot.dialogue_lines is None for shot in list_shots(project_id)))
                    if compatible:
                        current = _fingerprint(project_id)
                        if run.plan_fingerprint == previous:
                            updates["plan_fingerprint"] = current
                        if run.current_fingerprint == previous:
                            updates["current_fingerprint"] = current
                except ValueError:
                    pass
                return _save_run(run.model_copy(update=updates))
        return run

    plan_ids = [step.shot_id for step in run.steps]
    pending = (
        plan_ids[run.current_index:]
        if run.state in {"active", "stopping", "paused", "stopped"}
        else []
    )
    updates: dict[str, object] = {
        "selected_shot_ids": plan_ids,
        "pending_shot_ids": pending,
        "skipped_shots": {},
        "tail_source_job_ids": {},
    }
    try:
        legacy_fingerprint = _legacy_fingerprint(project_id)
        if run.plan_fingerprint == legacy_fingerprint:
            current_fingerprint = _fingerprint(project_id)
            updates["plan_fingerprint"] = current_fingerprint
            if run.current_fingerprint in {"", legacy_fingerprint}:
                updates["current_fingerprint"] = current_fingerprint
    except ValueError:
        pass
    with _project_lock(project_id):
        return _save_run(run.model_copy(update=updates))


def load_run(project_id: str, run_id: str) -> ManagedRun | None:
    if not run_id.startswith("mrun_") or not run_id[5:].isalnum():
        return None
    path = _run_dir(project_id) / f"{run_id}.json"
    if not path.is_file():
        return None
    return _decode_run(project_id, path.read_text(encoding="utf-8"))


def list_runs(project_id: str) -> list[ManagedRun]:
    directory = _run_dir(project_id)
    if not directory.is_dir():
        return []
    runs = [_decode_run(project_id, path.read_text(encoding="utf-8"))
            for path in directory.glob("mrun_*.json")]
    return sorted(runs, key=lambda run: (run.created_at, run.run_id), reverse=True)


def active_run_for_project(project_id: str) -> ManagedRun | None:
    return next((run for run in list_runs(project_id) if run.state == "active"), None)


def current_step(run: ManagedRun) -> RunStep | None:
    if not run.pending_shot_ids:
        return None
    shot_id = run.pending_shot_ids[0]
    return next((step for step in run.steps if step.shot_id == shot_id), None)


def _plan_index(run: ManagedRun, shot_id: str | None) -> int:
    if shot_id is None:
        return len(run.steps)
    return next(
        index for index, step in enumerate(run.steps) if step.shot_id == shot_id
    )


def bind_job(project_id: str, run_id: str, shot_id: str, job_id: str,
             *, expected_fingerprint: str | None = None,
             expected_event_id: str | None = None,
             allow_prompt_refinement: bool = False) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None or run.state != "active":
            raise ValueError("Managed run was stopped before the H3 job could be bound")
        if expected_event_id and run.pending_event_id != expected_event_id:
            raise ValueError("Managed run event changed before the H3 job could be bound")
        step = current_step(run)
        if step is None or step.shot_id != shot_id:
            raise ValueError("H3 job is not for the next planned Shot")
        if run.current_job_id is not None:
            raise ValueError("The next planned Shot already has an H3 job")
        if allow_prompt_refinement and expected_event_id and run.recovery_history:
            # Canonical submit may refresh a stale prompt. Adopt only the exact
            # reviewed refinement published for this submission event, not any
            # freshly observed project fingerprint or another turn's edit.
            receipt = run.recovery_history[-1]
            if (receipt.get("kind") == "prompt_refinement"
                    and receipt.get("shot_id") == shot_id
                    and receipt.get("event_id") == expected_event_id
                    and receipt.get("before_fingerprint") == expected_fingerprint
                    and receipt.get("after_fingerprint") == run.current_fingerprint):
                expected_fingerprint = run.current_fingerprint
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
        step = current_step(run)
        if step is None:
            return None
        completed = dict(run.completed_job_ids)
        completed[step.shot_id] = job_id
        remaining = run.pending_shot_ids[1:]
        next_index = _plan_index(run, remaining[0] if remaining else None)
        return _save_run(run.model_copy(update={
            "current_index": next_index,
            "current_job_id": None,
            "completed_job_ids": completed,
            "pending_shot_ids": remaining,
            "prompt_retry_count": 0,
            "prompt_retry_error": "",
            "paused_reason": "",
            "pending_event_id": f"{job_id}:{status.value}" if remaining else None,
            "state": "active" if remaining else "completed",
        }))


def mark_tail_ready(
    project_id: str,
    run_id: str,
    shot_id: str,
    layout_id: str,
    *,
    expected_event_id: str | None = None,
) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        step = current_step(run) if run is not None else None
        if (
            run is None
            or run.state != "active"
            or step is None
            or step.shot_id != shot_id
            or (
                expected_event_id is not None
                and run.pending_event_id != expected_event_id
            )
        ):
            raise ValueError("Managed run changed while preparing tail frame")
        prepared = dict(run.prepared_tail_layout_ids)
        prepared[shot_id] = layout_id
        return _save_run(run.model_copy(update={
            "prepared_tail_layout_ids": prepared,
            "current_fingerprint": _fingerprint(project_id),
        }))


def pause_run(
    project_id: str,
    run_id: str,
    reason: str,
    *,
    expected_event_id: str | None = None,
) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if expected_event_id and run.pending_event_id != expected_event_id:
            return run
        if run.state != "active":
            return run
        return _save_run(run.model_copy(update={
            "state": "paused", "pending_event_id": None, "paused_reason": reason,
        }))


def record_prompt_retry(
    project_id: str,
    run_id: str,
    error: str,
    *,
    expected_event_id: str | None = None,
) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None or run.state != "active" or run.current_job_id:
            raise ValueError("Managed run is no longer awaiting a prompt")
        if expected_event_id and run.pending_event_id != expected_event_id:
            raise ValueError("Managed run event changed before prompt retry")
        if run.prompt_retry_count >= 1:
            raise ValueError("Managed prompt retry budget reached")
        return _save_run(run.model_copy(update={
            "prompt_retry_count": run.prompt_retry_count + 1,
            "prompt_retry_error": error[:1000],
        }))


def abandon_tail_handoff(
    project_id: str,
    run_id: str,
    shot_id: str,
    reason: str,
    *,
    expected_event_id: str | None = None,
    expected_fingerprint: str | None = None,
) -> ManagedRun:
    """Drop an incompatible planned tail while keeping the current Shot runnable."""
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        step = current_step(run) if run is not None else None
        if (
            run is None
            or run.state != "active"
            or run.current_job_id
            or step is None
            or step.shot_id != shot_id
            or (
                expected_event_id is not None
                and run.pending_event_id != expected_event_id
            )
        ):
            raise ValueError("Managed run changed while abandoning tail handoff")
        if expected_fingerprint and _fingerprint(project_id) != expected_fingerprint:
            raise ValueError("Shot brief or references changed before tail recovery")
        from ..projects.store import load_shot, save_shot
        from ..projects.layouts import sync_selected_layout_refs
        target = load_shot(project_id, shot_id)
        layout_id = run.prepared_tail_layout_ids.get(shot_id)
        selected = [layout for layout in target.layout_refs
                    if layout.selected_for_h3 and layout.origin
                    and layout.origin.kind == "clip_tail_frame"] if target else []
        if (not selected or any(layout.id != layout_id or layout.feedback_source != "managed_run"
                                for layout in selected)):
            raise ValueError("Tail selection changed or is not an automatic managed handoff")
        target = sync_selected_layout_refs(target.model_copy(update={
            "layout_refs": [layout.model_copy(update={"selected_for_h3": False})
                            if layout.id == layout_id else layout for layout in target.layout_refs],
            "layout_asset_id": None if target.layout_asset_id == selected[0].asset_id else target.layout_asset_id,
        }))
        save_shot(target.model_copy(update={"meta": {**target.meta,
            "prompt_picture_signature": "", "prompt_layout_signature": "", "material_review_pending": True}}))
        steps = [
            item.model_copy(update={"tail_from_shot_id": None, "tail_reason": ""})
            if item.shot_id == shot_id else item
            for item in run.steps
        ]
        prepared = dict(run.prepared_tail_layout_ids)
        prepared.pop(shot_id, None)
        sources = dict(run.tail_source_job_ids)
        sources.pop(shot_id, None)
        return _save_run(run.model_copy(update={
            "steps": steps,
            "prepared_tail_layout_ids": prepared,
            "tail_source_job_ids": sources,
            "recovery_history": [*run.recovery_history, {
                "action": "drop_optional_tail", "shot_id": shot_id,
                "source_shot_id": step.tail_from_shot_id, "layout_id": layout_id,
                "event_id": expected_event_id, "reason": reason,
            }],
            "prompt_retry_count": 1,
            "prompt_retry_error": "The incompatible automatic tail was removed. Write this Shot independently. " + reason[:1000],
            "current_fingerprint": _fingerprint(project_id),
        }))


def create_draft(project_id: str, steps: list[RunStep], *, recovery_history: list[dict] | None = None) -> ManagedRun:
    with _project_lock(project_id):
        _validate_steps(project_id, steps)
        run = ManagedRun(
            run_id=f"mrun_{uuid.uuid4().hex}", project_id=project_id,
            steps=steps, plan_fingerprint=_fingerprint(project_id),
            current_fingerprint=_fingerprint(project_id),
            selected_shot_ids=[step.shot_id for step in steps],
            recovery_history=recovery_history or [],
        )
        return _save_run(run)


def activate_run(project_id: str, run_id: str, preset: str) -> ManagedRun:
    run = load_run(project_id, run_id)
    if run is None:
        raise ValueError("Managed run not found")
    return run_selected(
        project_id,
        run_id,
        [step.shot_id for step in run.steps],
        preset,
    )


def run_selected(
    project_id: str,
    run_id: str,
    shot_ids: list[str],
    resolution_preset: str | None,
) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if run.state not in {"draft", "paused", "stopped", "completed"}:
            raise ValueError("Managed run must be inactive before starting a selection")
        if run.plan_fingerprint != _fingerprint(project_id):
            raise ValueError("Shot brief or project order changed since this plan")
        if not shot_ids:
            raise ValueError("Select at least one Shot to run")
        preset = resolution_preset or run.resolution_preset
        if preset is None:
            raise ValueError("A resolution preset is required")
        resolve_local_resolution(preset)
        if run.resolution_preset and preset != run.resolution_preset:
            raise ValueError("Managed run resolution cannot change after its first run")
        if any(
            item.run_id != run_id and item.state in {"active", "stopping"}
            for item in list_runs(project_id)
        ):
            raise ValueError("Another managed run is active for this project")
        batch = build_execution_batch(project_id, run, shot_ids)
        has_pending = bool(batch.pending_shot_ids)
        next_shot_id = batch.pending_shot_ids[0] if has_pending else None
        return _save_run(run.model_copy(update={
            "state": "active" if has_pending else "paused",
            "resolution_preset": preset,
            "current_index": _plan_index(run, next_shot_id),
            "current_job_id": None,
            "selected_shot_ids": batch.selected_shot_ids,
            "pending_shot_ids": batch.pending_shot_ids,
            "skipped_shots": batch.skipped_shots,
            "tail_source_job_ids": batch.tail_source_job_ids,
            "pending_event_id": f"selection:{uuid.uuid4().hex}" if has_pending else None,
            "paused_reason": (
                "" if has_pending
                else "No selected Shots have satisfiable dependencies"
            ),
            "prompt_retry_count": 0,
            "prompt_retry_error": "",
            "current_fingerprint": _fingerprint(project_id),
        }))


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
        return _save_run(run.model_copy(update={
            "state": "stopped",
            "current_job_id": None,
            "pending_event_id": None,
        }))
