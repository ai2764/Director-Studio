"""Plan and control opt-in local H3 runs."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ..core.managed_runs.models import ManagedRun, ManagedRunView, RunPlan, RunStep
from ..core.managed_runs.store import (
    _fingerprint,
    create_draft,
    finish_stop,
    list_runs,
    load_run,
    request_stop,
    run_selected,
)
from ..core.jobs import cancel_job, list_jobs
from ..core.schemas import JobStatus
from ..core.projects.store import list_shots, load_project

router = APIRouter(tags=["managed_runs"])


class StartManagedRunBody(BaseModel):
    resolution_preset: str


class RunManagedRunBody(BaseModel):
    shot_ids: list[str]
    resolution_preset: str | None = None


def _run_view(run: ManagedRun) -> ManagedRunView:
    try:
        stale = run.plan_fingerprint != _fingerprint(run.project_id)
        reason = (
            "Shot brief, order, dialogue, references, or audio changed"
            if stale else ""
        )
    except ValueError as exc:
        stale, reason = True, str(exc)
    return ManagedRunView(
        **run.model_dump(),
        is_stale=stale,
        stale_reason=reason,
    )


def _build_run_steps(shots, plan: RunPlan) -> list[RunStep]:
    ordered_ids = [shot.id for shot in shots]
    known_ids = set(ordered_ids)
    target_ids: set[str] = set()
    for handoff in plan.tail_handoffs:
        if handoff.target_shot_id not in known_ids or handoff.source_shot_id not in known_ids:
            raise ValueError("Tail handoff references an unknown Shot")
        if handoff.target_shot_id in target_ids:
            raise ValueError("Tail handoff target was listed more than once")
        target_ids.add(handoff.target_shot_id)
    by_target = {handoff.target_shot_id: handoff for handoff in plan.tail_handoffs}
    return [
        RunStep(
            shot_id=shot_id,
            tail_from_shot_id=(by_target[shot_id].source_shot_id if shot_id in by_target else None),
            tail_reason=(by_target[shot_id].reason if shot_id in by_target else ""),
        )
        for shot_id in ordered_ids
    ]


@router.post("/projects/{project_id}/managed-run/plan", response_model=ManagedRunView)
async def plan_managed_run(project_id: str) -> ManagedRunView:
    project = load_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    shots = list_shots(project_id)
    if not shots:
        raise HTTPException(409, "Plan Shots before starting managed video")

    from .projects import _make_chat_fn

    chat_fn = await _make_chat_fn(on_progress=None)
    brief = "\n".join(
        f"{index}. {shot.id} — {shot.title}: {shot.script_beat}; "
        f"duration={shot.duration_s}s; framing={shot.shot_type}; "
        f"angle={shot.camera_angle}; camera_motion={shot.camera_motion}; "
        f"composition={shot.composition}; dialogue={shot.dialogue}; "
        f"Pictures={[ref.asset_id for ref in shot.refs]}"
        for index, shot in enumerate(shots, start=1)
    )
    system = (
        "Plan local H3 video execution for the existing Shots. Return only JSON "
        "matching the schema. Return only visually necessary tail-frame handoffs; "
        "the application owns the complete Shot list and execution order. For each "
        "handoff, target_shot_id is the later Shot that inherits the final frame from "
        "the earlier source_shot_id. Give a concrete reason based on action, camera, "
        "and composition. "
        "Do not change Shot content."
    )
    response = await chat_fn(
        system, f"Current Shot briefs:\n{brief}",
        format=RunPlan.model_json_schema(),
    )
    try:
        content = response.get("content") if isinstance(response, dict) else response
        plan = RunPlan.model_validate(json.loads(content) if isinstance(content, str) else content)
        return _run_view(create_draft(project_id, _build_run_steps(shots, plan)))
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(422, f"Managed run plan was invalid: {exc}") from exc


@router.get("/projects/{project_id}/managed-run", response_model=ManagedRunView | None)
async def get_managed_run(project_id: str) -> ManagedRunView | None:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    runs = list_runs(project_id)
    run = next(
        (run for run in runs if run.state in {"active", "stopping"}),
        runs[0] if runs else None,
    )
    return _run_view(run) if run is not None else None


@router.post("/projects/{project_id}/managed-run/{run_id}/run", response_model=ManagedRunView)
async def run_managed_selection(
    project_id: str,
    run_id: str,
    body: RunManagedRunBody,
) -> ManagedRunView:
    try:
        run = run_selected(
            project_id,
            run_id,
            body.shot_ids,
            body.resolution_preset,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if run.state == "active":
        from ..core.managed_runs.continuation import schedule_continuation
        schedule_continuation(project_id)
    return _run_view(run)


@router.post("/projects/{project_id}/managed-run/{run_id}/start", response_model=ManagedRunView)
async def start_managed_run(
    project_id: str,
    run_id: str,
    body: StartManagedRunBody,
) -> ManagedRunView:
    try:
        saved = load_run(project_id, run_id)
        if saved is None:
            raise ValueError("Managed run not found")
        run = run_selected(
            project_id,
            run_id,
            [step.shot_id for step in saved.steps],
            body.resolution_preset,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if run.state == "active":
        from ..core.managed_runs.continuation import schedule_continuation
        schedule_continuation(project_id)
    return _run_view(run)


@router.post("/projects/{project_id}/managed-run/{run_id}/stop", response_model=ManagedRunView)
async def stop_managed_run(project_id: str, run_id: str) -> ManagedRunView:
    try:
        run = request_stop(project_id, run_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    pending_ids = [run.current_job_id] if run.current_job_id else []
    pending_ids.extend(
        job.id for job in list_jobs(limit=None, pipeline_id="h3_ref2va", project_id=project_id)
        if (job.params or {}).get("managed_run_id") == run_id
        and job.status in {JobStatus.queued, JobStatus.uploading, JobStatus.running}
    )
    for job_id in dict.fromkeys(pending_ids):
        try:
            await cancel_job(job_id)
        except Exception as exc:
            # Keep stopping so no late Agent turn can submit. The user may
            # retry Stop, and startup reconciliation retries cancellation.
            raise HTTPException(503, f"Could not confirm H3 cancellation: {exc}") from exc
    return _run_view(finish_stop(project_id, run_id))
