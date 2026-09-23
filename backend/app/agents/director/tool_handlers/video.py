"""Managed local H3 kickoff; reuse the canonical Production submit preflight."""

from __future__ import annotations

import asyncio
from typing import Any

from ....core.jobs import cancel_job, list_jobs, load_job
from ....core.managed_runs.store import _fingerprint, active_run_for_project, bind_job, pause_run
from ....core.managed_runs.context import managed_turn_scope
from ....core.schemas import JobStatus
from ..tool_schema import explicit_one_off_h3_intent
from ....pipelines.h3_ref2va.resolutions import resolve_local_resolution

_submission_locks: dict[str, asyncio.Lock] = {}


async def start_h3_video(project_id: str, shot_id: str, *, svc: Any,
                         one_off_authorized: bool = False,
                         resolution_preset: str | None = None) -> dict[str, Any]:
    async with _submission_locks.setdefault(project_id, asyncio.Lock()):
        scope = managed_turn_scope.get()
        run = active_run_for_project(project_id)
        if run is None:
            if scope is not None:
                raise ValueError("No active managed H3 run; managed turn stopped before video submission")
            if not one_off_authorized:
                raise ValueError("No active managed H3 run or explicit one-off video request")
            if not resolution_preset:
                raise ValueError("One-off H3 resolution is required; inspect prior runs or ask the user")
            width, height = resolve_local_resolution(resolution_preset)
            from ....api.projects import H3SubmitOptions, submit_shot_endpoint
            shot = await submit_shot_endpoint(
                shot_id, svc, H3SubmitOptions(h3_provider="local", width=width, height=height)
            )
            if not shot.h3_job_id:
                raise ValueError("H3 submit returned no Job ID")
            finished = load_job(shot.h3_job_id)
            if finished is not None and finished.status in {
                JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled,
            }:
                from ....core.jobs.shot_sync import on_pipeline_job_terminal
                on_pipeline_job_terminal(finished)
            return {"ok": True, "shot_id": shot_id, "job_id": shot.h3_job_id}
        if run.current_index >= len(run.steps) or run.steps[run.current_index].shot_id != shot_id:
            raise ValueError("Only the next planned Shot can start")
        if (scope is None or scope.project_id != project_id or scope.run_id != run.run_id
                or scope.shot_id != shot_id):
            raise ValueError("Only the coordinator-issued managed turn may start this Shot")
        if run.current_job_id:
            return {"ok": True, "shot_id": shot_id, "job_id": run.current_job_id,
                    "already_started": True}
        if scope.event_id != run.pending_event_id:
            raise ValueError("Managed continuation event changed before submission")
        step = run.steps[run.current_index]
        if step.tail_from_shot_id and shot_id not in run.prepared_tail_layout_ids:
            raise ValueError("Planned tail frame has not been attached to this Shot")
        if run.current_fingerprint and _fingerprint(project_id) != run.current_fingerprint:
            raise ValueError("Shot brief or references changed after managed plan review")
        if not run.resolution_preset:
            raise ValueError("Managed H3 resolution was not selected")
        if resolution_preset and resolution_preset != run.resolution_preset:
            raise ValueError("Managed H3 resolution must match the user-selected run preset")
        width, height = resolve_local_resolution(run.resolution_preset)

        # Call the same API implementation used by manual Production. This keeps
        # prompt freshness, reference staging, job creation and status transitions
        # in one place instead of maintaining a second submission path.
        from ....api.projects import H3SubmitOptions, submit_shot_endpoint

        starting_fingerprint = run.current_fingerprint
        try:
            shot = await submit_shot_endpoint(
                shot_id, svc, H3SubmitOptions(h3_provider="local", width=width, height=height)
            )
        except Exception as exc:
            pause_run(project_id, run.run_id, f"H3 submission failed: {exc}")
            # The canonical submit may fail after creating its durable Job.
            # A tagged orphan must not continue outside the stopped step.
            for started in list_jobs(limit=None, pipeline_id="h3_ref2va", project_id=project_id):
                params = started.params or {}
                if params.get("managed_run_id") == run.run_id and params.get("managed_event_id") == scope.event_id:
                    await cancel_job(started.id)
            raise
        job_id = shot.h3_job_id
        if not job_id:
            raise ValueError("H3 submit returned no Job ID")
        try:
            bind_job(project_id, run.run_id, shot_id, job_id,
                     expected_fingerprint=starting_fingerprint)
        except ValueError as exc:
            # Stop may race the endpoint's asynchronous reference/prompt preflight.
            await cancel_job(job_id)
            pause_run(project_id, run.run_id, f"H3 submission could not bind its Job: {exc}")
            raise
        finished = load_job(job_id)
        if finished is not None and finished.status in {
            JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled,
        }:
            # A tiny/failed job can complete before the run binds its ID.
            from ....core.jobs.shot_sync import on_pipeline_job_terminal
            on_pipeline_job_terminal(finished)
            from ....core.managed_runs.continuation import schedule_continuation
            schedule_continuation(project_id)
        return {"ok": True, "shot_id": shot_id, "job_id": job_id}


async def handle_video_tool(
    *, name: str, args: dict[str, Any], project_id: str, svc: Any,
    actions: list[str], notes: list[str], result_payloads: list[dict[str, Any]] | None,
    user_feedback: str, previous_assistant: str = "",
) -> bool:
    if name != "start_h3_video":
        return False
    shot_id = str(args["shot_id"])
    result = await start_h3_video(
        project_id, shot_id, svc=svc,
        one_off_authorized=explicit_one_off_h3_intent(
            user_feedback, project_id, shot_id=shot_id, previous_assistant=previous_assistant,
        ),
        resolution_preset=args.get("resolution_preset"),
    )
    actions.append(f"start_h3_video:{result['shot_id']}:{result['job_id']}")
    reply = f"Started local H3 video for Shot {result['shot_id']}; Job {result['job_id']}."
    notes.append(reply)
    if result_payloads is not None:
        result_payloads.append({**result, "concludes_turn": True, "reply": reply})
    return True
