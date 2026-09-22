"""Plan and control opt-in local H3 runs."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ..core.managed_runs.models import ManagedRun, RunPlan
from ..core.managed_runs.store import (
    activate_run, create_draft, finish_stop, list_runs, request_stop,
)
from ..core.jobs import cancel_job
from ..core.projects.store import list_shots, load_project

router = APIRouter(tags=["managed_runs"])


class StartManagedRunBody(BaseModel):
    resolution_preset: str


@router.post("/projects/{project_id}/managed-run/plan", response_model=ManagedRun)
async def plan_managed_run(project_id: str) -> ManagedRun:
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
        "matching the schema. Preserve every Shot ID and its order. "
        "Only propose a tail-frame handoff when the Shot brief needs visual "
        "continuity; give its concrete reason based on action, camera, and composition. "
        "Do not change Shot content."
    )
    response = await chat_fn(
        system, f"Current Shot briefs:\n{brief}",
        format=RunPlan.model_json_schema(),
    )
    try:
        content = response.get("content") if isinstance(response, dict) else response
        plan = RunPlan.model_validate(json.loads(content) if isinstance(content, str) else content)
        return create_draft(project_id, plan.steps)
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(422, f"Managed run plan was invalid: {exc}") from exc


@router.get("/projects/{project_id}/managed-run", response_model=ManagedRun | None)
async def get_managed_run(project_id: str) -> ManagedRun | None:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    runs = list_runs(project_id)
    return next((run for run in runs if run.state in {"active", "stopping"}), runs[0] if runs else None)


@router.post("/projects/{project_id}/managed-run/{run_id}/start", response_model=ManagedRun)
async def start_managed_run(project_id: str, run_id: str, body: StartManagedRunBody) -> ManagedRun:
    try:
        run = activate_run(project_id, run_id, body.resolution_preset)
        from ..core.managed_runs.continuation import schedule_continuation
        schedule_continuation(project_id)
        return run
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/projects/{project_id}/managed-run/{run_id}/stop", response_model=ManagedRun)
async def stop_managed_run(project_id: str, run_id: str) -> ManagedRun:
    try:
        run = request_stop(project_id, run_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if run.current_job_id:
        try:
            await cancel_job(run.current_job_id)
        except Exception as exc:
            # Keep stopping so no late Agent turn can submit. The user may
            # retry Stop, and startup reconciliation retries cancellation.
            raise HTTPException(503, f"Could not confirm H3 cancellation: {exc}") from exc
    return finish_stop(project_id, run_id)
