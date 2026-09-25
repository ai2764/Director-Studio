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
from ...agents.director.tail_prompt_review import CreativeQuestion
from .models import ManagedRun, RunStep
from .context import ManagedTurnScope, managed_turn_scope
from .selection import latest_successful_video_job_id
from .store import _fingerprint, active_run_for_project, bind_job, current_step, finish_stop, list_runs, load_run, mark_tail_ready, pause_run, record_prompt_retry

logger = logging.getLogger("director_studio.managed_runs")
_continuation_tasks: dict[str, asyncio.Task[None]] = {}


def _clear_unplanned_managed_tails(run: ManagedRun, step: RunStep) -> None:
    """Remove stale managed continuity Pictures from an independent step."""
    target = load_shot(run.project_id, step.shot_id)
    if target is None:
        raise ValueError("Planned target Shot no longer exists")
    stale_ids = {
        layout.id
        for layout in target.layout_refs
        if layout.selected_for_h3
        and layout.feedback_source == "managed_run"
        and layout.origin is not None
        and layout.origin.kind == "clip_tail_frame"
    }
    if not stale_ids:
        return
    updated_layouts = [
        layout.model_copy(update={"selected_for_h3": False})
        if layout.id in stale_ids else layout
        for layout in target.layout_refs
    ]
    stale_assets = {
        layout.asset_id
        for layout in target.layout_refs
        if layout.id in stale_ids
    }
    updated = target.model_copy(update={
        "layout_refs": updated_layouts,
        "layout_asset_id": (
            None if target.layout_asset_id in stale_assets
            else target.layout_asset_id
        ),
    })
    updated = sync_selected_layout_refs(updated)
    meta = dict(updated.meta or {})
    meta["prompt_picture_signature"] = ""
    meta["prompt_layout_signature"] = ""
    meta["material_review_pending"] = True
    save_shot(updated.model_copy(update={"meta": meta}))


def _replace_managed_tail_selection(target: Any, layout_id: str) -> Any:
    """Activate one managed tail while retaining prior tails as inactive history."""
    updated_layouts = [
        layout.model_copy(update={"selected_for_h3": False})
        if (
            layout.id != layout_id
            and layout.selected_for_h3
            and layout.feedback_source == "managed_run"
            and layout.origin is not None
            and layout.origin.kind == "clip_tail_frame"
        )
        else layout
        for layout in target.layout_refs
    ]
    updated = target.model_copy(update={"layout_refs": updated_layouts})
    return sync_selected_layout_refs(
        select_layout_reference(updated, layout_id, True)
    )


def _tail_source_job_id(run: ManagedRun, step: RunStep) -> str | None:
    if not step.tail_from_shot_id:
        return None
    if step.tail_from_shot_id in run.selected_shot_ids:
        return (
            run.completed_job_ids.get(step.tail_from_shot_id)
            or latest_successful_video_job_id(
                run.project_id,
                step.tail_from_shot_id,
            )
        )
    return (
        run.tail_source_job_ids.get(step.shot_id)
        or latest_successful_video_job_id(
            run.project_id,
            step.tail_from_shot_id,
            run.completed_job_ids.get(step.tail_from_shot_id),
        )
    )


async def prepare_planned_tail(run: ManagedRun, svc: Any) -> None:
    """Use the exact successful source job authorized in the reviewed plan."""
    step = current_step(run)
    if step is None:
        return
    source_shot_id = step.tail_from_shot_id
    if not source_shot_id:
        _clear_unplanned_managed_tails(run, step)
        return
    source_job_id = _tail_source_job_id(run, step)
    if not source_job_id:
        warning = run.skipped_shots.get(step.shot_id)
        raise ValueError(
            warning
            or f"Planned tail source Shot {source_shot_id} has no successful video"
        )

    # A restarted process may have extracted the frame before marking the step.
    target = load_shot(run.project_id, step.shot_id)
    if target is None:
        raise ValueError("Planned target Shot no longer exists")
    prepared_layout_id = run.prepared_tail_layout_ids.get(step.shot_id)
    prepared = next(
        (layout for layout in target.layout_refs if layout.id == prepared_layout_id),
        None,
    )
    if (
        prepared is not None
        and prepared.origin is not None
        and prepared.origin.source_job_id == source_job_id
    ):
        return
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
    latest_step = current_step(latest) if latest is not None else None
    if (
        latest is None
        or latest.state != "active"
        or latest.pending_event_id != run.pending_event_id
        or latest_step is None
        or latest_step.shot_id != step.shot_id
    ):
        return
    reviewed = review_layout_reference(
        target, layout_id, LayoutReviewStatus.usable,
        f"Pre-authorized continuity handoff: {step.tail_reason}",
        feedback_source="managed_run",
    )
    selected = _replace_managed_tail_selection(reviewed, layout_id)
    save_shot(selected)
    selected_layout = next(layout for layout in selected.layout_refs if layout.id == layout_id)
    if selected_layout.asset_id:
        from ...api.projects import _update_layout_asset_review
        _update_layout_asset_review(selected_layout.asset_id, LayoutReviewStatus.usable.value)
    selected_fingerprint = _fingerprint(run.project_id)
    try:
        await svc.write_prompts_after_layout(step.shot_id)
    except CreativeQuestion:
        raise
    except Exception as exc:
        if _fingerprint(run.project_id) != selected_fingerprint:
            raise
        # The selected frame is durable. Give the Agent one informed chance to
        # author the prompt instead of repeating this same automatic draft.
        mark_tail_ready(
            run.project_id,
            run.run_id,
            step.shot_id,
            layout_id,
            expected_event_id=run.pending_event_id,
        )
        record_prompt_retry(
            run.project_id,
            run.run_id,
            str(exc),
            expected_event_id=run.pending_event_id,
        )
        return
    mark_tail_ready(
        run.project_id,
        run.run_id,
        step.shot_id,
        layout_id,
        expected_event_id=run.pending_event_id,
    )


async def _agent_turn(run: ManagedRun, svc: Any) -> Any:
    from ...api.projects import _make_chat_fn
    from ...agents.director.chat import handle_chat
    from ..projects.chat_history import append_chat_message
    from ..projects.chat_sessions import DirectorChatSessionConflict, director_chat_sessions

    step = current_step(run)
    if step is None:
        return None
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
    if run.prompt_retry_error:
        message += (
            f" Previous prompt attempt failed: {run.prompt_retry_error}. "
            "This is the final managed recovery attempt for this Shot. Use that concrete "
            "error to change the prompt-writing approach; do not repeat the same submission."
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
        return result
    finally:
        managed_turn_scope.reset(token)
        await director_chat_sessions.finish(run.project_id, session.session_id or "")


async def continue_run(project_id: str, run_id: str) -> None:
    """One bounded continuation for one durable pending event, never a polling loop."""
    run = load_run(project_id, run_id)
    if run is None or run.state != "active" or not run.pending_event_id:
        return
    if run.current_job_id or current_step(run) is None:
        return
    event_id = run.pending_event_id
    try:
        if run.current_fingerprint and _fingerprint(project_id) != run.current_fingerprint:
            pause_run(project_id, run_id, "Shot brief or references changed after plan review")
            return
        from ...agents.director import DirectorService
        from ...agents.director.llm_plan_provider import DirectorLLMPlanProvider

        svc = DirectorService(plan_provider=DirectorLLMPlanProvider())
        await prepare_planned_tail(run, svc)
        latest = load_run(project_id, run_id)
        if (
            latest is None
            or latest.state != "active"
            or latest.pending_event_id != event_id
        ):
            return
        while latest is not None and latest.state == "active" and not latest.current_job_id:
            turn_event_id = latest.pending_event_id
            result = await _agent_turn(latest, svc)
            latest = load_run(project_id, run_id)
            if latest is None or latest.state != "active" or latest.current_job_id:
                break
            if latest.pending_event_id != turn_event_id:
                return
            if (getattr(result, "failure_code", "") == "PROMPT_GENERATION_FAILED"
                    and latest.prompt_retry_count < 1):
                latest = record_prompt_retry(
                    project_id, run_id,
                    getattr(result, "failure_message", "") or result.reply,
                    expected_event_id=turn_event_id,
                )
                continue
            pause_run(
                project_id, run_id,
                (getattr(result, "failure_message", "") or "Agent turn ended without starting the planned H3 job"),
                expected_event_id=turn_event_id,
            )
            break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Managed H3 continuation failed for %s", run_id)
        latest = load_run(project_id, run_id)
        if latest is not None and latest.state == "active" and latest.current_job_id:
            logger.warning(
                "Managed run %s already bound Job %s; waiting for its terminal event",
                run_id, latest.current_job_id,
            )
            return
        if latest is None or latest.pending_event_id != event_id:
            return
        pause_run(
            project_id,
            run_id,
            f"Managed continuation failed: {exc}",
            expected_event_id=event_id,
        )


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
            step = current_step(run)
            if step is None:
                pause_run(project.id, run.run_id, "No pending Shot could be recovered")
                schedule_continuation(project.id)
                continue
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
                                   expected_fingerprint=run.current_fingerprint,
                                   expected_event_id=run.pending_event_id)
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
    """Cancel bound and late tagged Jobs, including Jobs created after Stop."""
    for project in list_projects():
        for run in list_runs(project.id):
            if run.state not in {"stopping", "stopped"}:
                continue
            pending_ids = [run.current_job_id] if run.current_job_id else []
            pending_ids.extend(
                job.id for job in list_jobs(
                    limit=None, pipeline_id="h3_ref2va", project_id=project.id,
                )
                if (job.params or {}).get("managed_run_id") == run.run_id
                and job.status in {JobStatus.queued, JobStatus.uploading, JobStatus.running}
            )
            cancelled_all = True
            for job_id in dict.fromkeys(pending_ids):
                job = load_job(job_id)
                if job is None or job.status not in {
                    JobStatus.queued, JobStatus.uploading, JobStatus.running,
                }:
                    continue
                try:
                    await cancel_job(job_id)
                except Exception:
                    logger.exception("Could not confirm cancellation for managed run %s", run.run_id)
                    cancelled_all = False
            if run.state == "stopping" and cancelled_all:
                finish_stop(project.id, run.run_id)
