"""Pre-authorized tail-frame handoffs and event-driven managed turns."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from ..media.tail_frame import extract_clip_tail_frame
from ..jobs import cancel_job
from ..jobs.store import list_jobs, load_job
from ..schemas import JobStatus
from ..projects.layouts import LayoutReviewStatus, sync_selected_layout_refs
from ..projects.store import list_projects, load_shot, save_shot
from ..projects.transitions import review_layout_reference, select_layout_reference
from .models import ManagedRun
from .context import ManagedTurnScope, managed_turn_scope
from .store import _fingerprint, active_run_for_project, bind_job, finish_stop, list_runs, load_run, mark_tail_ready, pause_run

logger = logging.getLogger("director_studio.managed_runs")
_continuation_tasks: dict[str, asyncio.Task[None]] = {}


async def prepare_planned_tail(run: ManagedRun, svc: Any) -> None:
    """Use the exact successful source job authorized in the reviewed plan."""
    if run.current_index >= len(run.steps):
        return
    step = run.steps[run.current_index]
    source_shot_id = step.tail_from_shot_id
    if not source_shot_id:
        return
    source_job_id = run.completed_job_ids.get(source_shot_id)
    if not source_job_id:
        raise ValueError(f"Planned tail source Shot {source_shot_id} has no completed managed Job")
    if step.shot_id in run.prepared_tail_layout_ids:
        return

    # A restarted process may have extracted the frame before marking the step.
    target = load_shot(run.project_id, step.shot_id)
    if target is None:
        raise ValueError("Planned target Shot no longer exists")
    prior = next((layout for layout in target.layout_refs if layout.origin
                  and layout.origin.source_job_id == source_job_id), None)
    if prior is None:
        extracted = await asyncio.to_thread(
            extract_clip_tail_frame,
            project_id=run.project_id,
            source_shot_id=source_shot_id,
            target_shot_id=step.shot_id,
            source_job_id=source_job_id,
        )
        layout_id = str(extracted["layout_ref_id"])
        target = load_shot(run.project_id, step.shot_id)
    else:
        layout_id = prior.id

    latest = load_run(run.project_id, run.run_id)
    if latest is None or latest.state != "active" or latest.current_index != run.current_index:
        return
    reviewed = review_layout_reference(
        target, layout_id, LayoutReviewStatus.usable,
        f"Pre-authorized continuity handoff: {step.tail_reason}",
        feedback_source="managed_run",
    )
    selected = sync_selected_layout_refs(select_layout_reference(reviewed, layout_id, True))
    save_shot(selected)
    selected_layout = next(layout for layout in selected.layout_refs if layout.id == layout_id)
    if selected_layout.asset_id:
        from ...api.projects import _update_layout_asset_review
        _update_layout_asset_review(selected_layout.asset_id, LayoutReviewStatus.usable.value)
    await svc.write_prompts_after_layout(step.shot_id)
    mark_tail_ready(run.project_id, run.run_id, step.shot_id, layout_id)


async def _agent_turn(run: ManagedRun, svc: Any) -> None:
    from ...api.projects import _make_chat_fn
    from ...agents.director.chat import handle_chat
    from ..projects.chat_history import append_chat_message
    from ..projects.chat_sessions import DirectorChatSessionConflict, director_chat_sessions

    step = run.steps[run.current_index]
    shot = load_shot(run.project_id, step.shot_id)
    if shot is None:
        raise ValueError("Planned Shot no longer exists")
    previous_job_id = run.completed_job_ids.get(run.steps[run.current_index - 1].shot_id) if run.current_index else None
    previous_job = load_job(previous_job_id) if previous_job_id else None
    feedback = (
        f"Previous Job {previous_job.id}: {previous_job.status.value}; "
        f"outputs={list(previous_job.outputs)}. "
        if previous_job is not None else "First planned Shot. "
    )
    message = (
        f"Managed local H3 run {run.run_id}, planned Shot {run.current_index + 1}/{len(run.steps)}: "
        f"{step.shot_id}. {feedback}"
        f"Brief: {shot.script_beat}. Camera: {shot.camera_motion}; composition: {shot.composition}. "
        f"Plan: tail_from={step.tail_from_shot_id or 'none'}; "
        f"tail_source_job={run.completed_job_ids.get(step.tail_from_shot_id or '', 'none')}; "
        f"reason={step.tail_reason or 'none'}. "
        f"The user reviewed this run plan and selected {run.resolution_preset}. "
        "Use the saved Shot brief and current assets. Write or refresh its H3 prompt if needed, "
        "then call start_h3_video for this exact Shot. A planned tail frame, if any, is already "
        "selected. Do not queue a new Layout or invent assets. If a required input is missing, "
        "explain the blocker and stop; do not retry blindly."
    )
    while True:
        latest = load_run(run.project_id, run.run_id)
        if latest is None or latest.state != "active" or latest.pending_event_id != run.pending_event_id:
            return
        await director_chat_sessions.wait_available(run.project_id)
        latest = load_run(run.project_id, run.run_id)
        if latest is None or latest.state != "active" or latest.pending_event_id != run.pending_event_id:
            return
        try:
            session = await director_chat_sessions.reserve(run.project_id)
            break
        except DirectorChatSessionConflict:
            continue
    token = managed_turn_scope.set(ManagedTurnScope(
        project_id=run.project_id, run_id=run.run_id,
        event_id=run.pending_event_id or "", shot_id=step.shot_id,
    ))
    try:
        await director_chat_sessions.attach(run.project_id, session.session_id or "", asyncio.current_task())
        chat_fn = await _make_chat_fn(on_progress=None)
        isolated_session = f"managed_{uuid.uuid4().hex}"
        append_chat_message(run.project_id, role="user", content=f"[Managed run event] {message}")
        result = await handle_chat(
            project_id=run.project_id, message=message, svc=svc, chat_fn=chat_fn,
            history=[], managed_session_id=isolated_session,
        )
        append_chat_message(run.project_id, role="assistant", content=result.reply)
    finally:
        managed_turn_scope.reset(token)
        await director_chat_sessions.finish(run.project_id, session.session_id or "")


async def continue_run(project_id: str, run_id: str) -> None:
    """One bounded continuation for one durable pending event, never a polling loop."""
    run = load_run(project_id, run_id)
    if run is None or run.state != "active" or not run.pending_event_id:
        return
    if run.current_job_id or run.current_index >= len(run.steps):
        return
    try:
        if run.current_fingerprint and _fingerprint(project_id) != run.current_fingerprint:
            pause_run(project_id, run_id, "Shot brief or references changed after plan review")
            return
        from ...agents.director import DirectorService
        from ...agents.director.llm_plan_provider import DirectorLLMPlanProvider

        svc = DirectorService(plan_provider=DirectorLLMPlanProvider())
        await prepare_planned_tail(run, svc)
        latest = load_run(project_id, run_id)
        if latest is None or latest.state != "active" or not latest.pending_event_id:
            return
        await _agent_turn(latest, svc)
        latest = load_run(project_id, run_id)
        if (latest is not None and latest.state == "active"
                and latest.current_index == run.current_index and not latest.current_job_id):
            pause_run(project_id, run_id, "Agent turn ended without starting the planned H3 job")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Managed H3 continuation failed for %s", run_id)
        pause_run(project_id, run_id, f"Managed continuation failed: {exc}")


def schedule_continuation(project_id: str) -> None:
    run = active_run_for_project(project_id)
    if run is None or not run.pending_event_id or run.current_job_id:
        return
    current = _continuation_tasks.get(project_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(continue_run(project_id, run.run_id))
    _continuation_tasks[project_id] = task

    def clear(done: asyncio.Task[None]) -> None:
        if _continuation_tasks.get(project_id) is done:
            _continuation_tasks.pop(project_id, None)
            # A job can finish during the Agent's own kickoff turn. Defer its
            # next step until that turn has released the project chat session.
            schedule_continuation(project_id)

    task.add_done_callback(clear)


def schedule_pending_runs() -> None:
    for project in list_projects():
        run = active_run_for_project(project.id)
        if run is not None and not run.current_job_id and run.pending_event_id:
            step = run.steps[run.current_index]
            candidates = [job for job in list_jobs(
                limit=None, pipeline_id="h3_ref2va", project_id=project.id,
            ) if (job.params or {}).get("managed_run_id") == run.run_id
                and (job.params or {}).get("managed_step_shot_id") == step.shot_id
                and (job.params or {}).get("managed_event_id") == run.pending_event_id]
            if len(candidates) > 1:
                pause_run(project.id, run.run_id, "Multiple H3 Jobs claim the same managed step")
            elif len(candidates) == 1:
                try:
                    run = bind_job(project.id, run.run_id, step.shot_id, candidates[0].id,
                                   expected_fingerprint=run.current_fingerprint)
                except ValueError as exc:
                    pause_run(project.id, run.run_id, f"Could not reconcile managed H3 Job: {exc}")
            run = active_run_for_project(project.id)
        if run is not None and run.current_job_id:
            job = load_job(run.current_job_id)
            if job is None:
                pause_run(project.id, run.run_id, f"Bound H3 Job {run.current_job_id} is missing")
            elif job.status in {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}:
                from ..jobs.shot_sync import on_pipeline_job_terminal
                on_pipeline_job_terminal(job)
        elif run is not None and not run.pending_event_id:
            pause_run(project.id, run.run_id, "No pending event or bound H3 Job could be recovered")
        schedule_continuation(project.id)


async def reconcile_stopping_runs() -> None:
    """Finish interrupted Stop requests before queued Jobs can be recovered."""
    for project in list_projects():
        for run in list_runs(project.id):
            if run.state != "stopping":
                continue
            job = load_job(run.current_job_id) if run.current_job_id else None
            if job is not None and job.status in {
                JobStatus.queued, JobStatus.uploading, JobStatus.running,
            }:
                try:
                    await cancel_job(job.id)
                except Exception:
                    logger.exception("Could not confirm cancellation for managed run %s", run.run_id)
                    continue
            finish_stop(project.id, run.run_id)
