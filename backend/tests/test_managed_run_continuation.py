"""Durable terminal-event behavior for managed runs."""

from __future__ import annotations

import asyncio

from app.core.managed_runs.models import RunStep
from app.core.managed_runs.store import (
    activate_run,
    bind_job,
    create_draft,
    current_step,
    finish_stop,
    load_run,
    record_prompt_retry,
    record_terminal,
    request_stop,
    run_selected,
)
from app.core.projects.models import Shot
from app.core.projects.store import create_project, save_project, save_shot
from app.core.schemas import JobStatus
from app.core.jobs.store import create_job, save_job
import pytest


def _run():
    project = create_project("Two Shots", "A passage")
    shots = [Shot(id=f"sht_{i}", project_id=project.id, scene_id="scene_1",
                  title=f"Shot {i}", script_beat="A passage", duration_s=5)
             for i in (1, 2)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id for shot in shots]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id) for shot in shots])
    return activate_run(project.id, draft.run_id, "landscape-480")


def test_terminal_event_advances_once_and_rejects_old_job() -> None:
    run = _run()
    assert run.pending_event_id.startswith("selection:")
    assert run.pending_shot_ids == ["sht_1", "sht_2"]
    bind_job(run.project_id, run.run_id, "sht_1", "job_1")
    first = record_terminal(run.project_id, "job_1", JobStatus.succeeded)
    replay = record_terminal(run.project_id, "job_1", JobStatus.succeeded)
    assert first.current_index == replay.current_index == 1
    assert first.completed_job_ids == {"sht_1": "job_1"}
    assert first.pending_shot_ids == ["sht_2"]
    assert first.pending_event_id == "job_1:succeeded"
    assert load_run(run.project_id, run.run_id).current_job_id is None


def test_failure_pauses_without_retry() -> None:
    run = _run()
    bind_job(run.project_id, run.run_id, "sht_1", "job_failed")
    paused = record_terminal(run.project_id, "job_failed", JobStatus.failed, "Provider error")
    assert paused.state == "paused"
    assert paused.current_index == 0
    assert paused.current_job_id is None
    assert paused.pending_shot_ids == ["sht_1", "sht_2"]
    assert "Provider error" in paused.paused_reason
    assert paused.pending_event_id is None


def test_sparse_selection_completes_without_replaying_earlier_shot() -> None:
    run = _run()
    request_stop(run.project_id, run.run_id)
    finish_stop(run.project_id, run.run_id)
    resumed = run_selected(run.project_id, run.run_id, ["sht_2"], None)

    bind_job(run.project_id, run.run_id, "sht_2", "job_second")
    completed = record_terminal(
        run.project_id, "job_second", JobStatus.succeeded
    )

    assert completed.state == "completed"
    assert completed.pending_shot_ids == []
    assert completed.current_index == len(completed.steps)
    assert completed.completed_job_ids == {"sht_2": "job_second"}


@pytest.mark.asyncio
async def test_continuation_runs_only_first_selected_pending_shot(monkeypatch) -> None:
    from app.core.managed_runs import continuation

    run = _run()
    request_stop(run.project_id, run.run_id)
    finish_stop(run.project_id, run.run_id)
    resumed = run_selected(run.project_id, run.run_id, ["sht_2"], None)
    started = []

    async def fake_agent_turn(current, _svc):
        step = current_step(current)
        assert step is not None
        started.append(step.shot_id)
        bind_job(
            current.project_id,
            current.run_id,
            step.shot_id,
            "job_selected",
        )

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent_turn)
    await continuation.continue_run(resumed.project_id, resumed.run_id)

    assert started == ["sht_2"]


@pytest.mark.asyncio
async def test_resume_skips_missing_dependent_but_starts_later_independent(
    monkeypatch,
) -> None:
    from app.core.managed_runs import continuation

    project = create_project("Selective", "Three shots")
    source = Shot(
        id="sht_source", project_id=project.id, scene_id="sc",
        title="Source", script_beat="Source", duration_s=5,
    )
    dependent = Shot(
        id="sht_dependent", project_id=project.id, scene_id="sc",
        title="Dependent", script_beat="Dependent", duration_s=5,
    )
    independent = Shot(
        id="sht_independent", project_id=project.id, scene_id="sc",
        title="Independent", script_beat="Independent", duration_s=5,
    )
    for shot in (source, dependent, independent):
        save_shot(shot)
    save_project(project.model_copy(update={
        "shot_ids": [source.id, dependent.id, independent.id],
    }))
    draft = create_draft(project.id, [
        RunStep(shot_id=source.id),
        RunStep(
            shot_id=dependent.id,
            tail_from_shot_id=source.id,
            tail_reason="Continue the source pose",
        ),
        RunStep(shot_id=independent.id),
    ])
    resumed = run_selected(
        project.id,
        draft.run_id,
        [dependent.id, independent.id],
        "landscape-480",
    )
    assert dependent.id in resumed.skipped_shots

    started = []

    async def fake_agent_turn(current, _svc):
        step = current_step(current)
        assert step is not None
        started.append(step.shot_id)
        bind_job(
            current.project_id,
            current.run_id,
            step.shot_id,
            "job_independent",
        )

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent_turn)
    await continuation.continue_run(project.id, resumed.run_id)

    assert started == [independent.id]


@pytest.mark.asyncio
async def test_tail_for_unselected_source_uses_persisted_successful_job(
    monkeypatch,
) -> None:
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import _save_run
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.projects.store import load_shot

    run = _run()
    run = _save_run(run.model_copy(update={
        "steps": [
            RunStep(shot_id="sht_1"),
            RunStep(
                shot_id="sht_2",
                tail_from_shot_id="sht_1",
                tail_reason="Continue the door",
            ),
        ],
        "current_index": 1,
        "pending_shot_ids": ["sht_2"],
        "tail_source_job_ids": {"sht_2": "job_existing"},
    }))
    captured = []

    def fake_extract(**kwargs):
        captured.append(kwargs)
        shot = load_shot(run.project_id, "sht_2")
        layout = LayoutReference(
            id="lref_existing_tail",
            purpose="continuity",
            review_status=LayoutReviewStatus.pending_review,
        )
        save_shot(shot.model_copy(update={"layout_refs": [layout]}))
        return {"layout_ref_id": layout.id, "layout_asset_id": None}

    class FakeService:
        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            return None

    monkeypatch.setattr(continuation, "extract_clip_tail_frame", fake_extract)
    await continuation.prepare_planned_tail(run, FakeService())

    assert captured[0]["source_job_id"] == "job_existing"


@pytest.mark.asyncio
async def test_rerun_source_replaces_prepared_tail_with_new_job(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import _save_run
    from app.core.projects.layouts import (
        ClipTailFrameOrigin,
        LayoutReference,
        LayoutReviewStatus,
    )
    from app.core.projects.store import load_shot

    run = _run()
    run = _save_run(run.model_copy(update={"steps": [
        RunStep(shot_id="sht_1"),
        RunStep(
            shot_id="sht_2",
            tail_from_shot_id="sht_1",
            tail_reason="Continue the door",
        ),
    ]}))
    bind_job(run.project_id, run.run_id, "sht_1", "job_new")
    run = record_terminal(run.project_id, "job_new", JobStatus.succeeded)
    target = load_shot(run.project_id, "sht_2")
    old_layout = LayoutReference(
        id="lref_old_tail",
        purpose="old continuity",
        review_status=LayoutReviewStatus.usable,
        origin=ClipTailFrameOrigin(
            source_shot_id="sht_1",
            source_job_id="job_old",
            source_generation=1,
            output_kind="enhanced",
            output_key="video",
            source_filename="old.mp4",
        ),
    )
    save_shot(target.model_copy(update={"layout_refs": [old_layout]}))
    run = _save_run(run.model_copy(update={
        "prepared_tail_layout_ids": {"sht_2": old_layout.id},
    }))
    captured = []

    def fake_extract(**kwargs):
        captured.append(kwargs)
        shot = load_shot(run.project_id, "sht_2")
        new_layout = LayoutReference(
            id="lref_new_tail",
            purpose="new continuity",
            review_status=LayoutReviewStatus.pending_review,
            origin=ClipTailFrameOrigin(
                source_shot_id="sht_1",
                source_job_id="job_new",
                source_generation=2,
                output_kind="enhanced",
                output_key="video",
                source_filename="new.mp4",
            ),
        )
        save_shot(shot.model_copy(update={
            "layout_refs": [*shot.layout_refs, new_layout],
        }))
        return {"layout_ref_id": new_layout.id, "layout_asset_id": None}

    class FakeService:
        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            return None

    monkeypatch.setattr(continuation, "extract_clip_tail_frame", fake_extract)
    await continuation.prepare_planned_tail(run, FakeService())

    assert captured[0]["source_job_id"] == "job_new"
    saved = load_run(run.project_id, run.run_id)
    assert saved.prepared_tail_layout_ids["sht_2"] == "lref_new_tail"


@pytest.mark.asyncio
async def test_planned_tail_uses_exact_completed_job_and_selects_for_h3(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.core.projects.store import load_shot
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus

    run = _run()
    from app.core.managed_runs.store import load_run as read_run
    from app.core.managed_runs.store import _save_run
    run = _save_run(run.model_copy(update={"steps": [
        RunStep(shot_id="sht_1"),
        RunStep(shot_id="sht_2", tail_from_shot_id="sht_1", tail_reason="Match the door"),
    ]}))
    bind_job(run.project_id, run.run_id, "sht_1", "job_first")
    run = record_terminal(run.project_id, "job_first", JobStatus.succeeded)
    captured = []

    def fake_extract(**kwargs):
        captured.append(kwargs)
        shot = load_shot(run.project_id, "sht_2")
        layout = LayoutReference(id="lref_tail", asset_id="lay_tail", purpose="continuity",
                                 review_status=LayoutReviewStatus.pending_review)
        save_shot(shot.model_copy(update={"layout_refs": [layout]}))
        return {"layout_ref_id": layout.id, "layout_asset_id": layout.asset_id}

    monkeypatch.setattr(continuation, "extract_clip_tail_frame", fake_extract)
    class FakeService:
        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            captured.append({"rewrite": shot_id})

    await continuation.prepare_planned_tail(run, FakeService())
    updated = load_shot(run.project_id, "sht_2")
    assert captured[0]["source_job_id"] == "job_first"
    assert captured[0]["source_shot_id"] == "sht_1"
    assert updated.layout_refs[0].selected_for_h3 is True
    assert updated.layout_refs[0].feedback_source == "managed_run"
    assert read_run(run.project_id, run.run_id).prepared_tail_layout_ids["sht_2"] == "lref_tail"


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_user_decision", [False, True])
async def test_tail_prompt_failure_hands_selected_frame_and_error_to_agent(monkeypatch, needs_user_decision) -> None:
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import _save_run
    from app.core.projects.store import load_shot
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.agents.director.chat_orchestrator import ChatResult
    from app.agents.director.tail_prompt_review import CreativeQuestion

    run = _run()
    run = _save_run(run.model_copy(update={"steps": [
        RunStep(shot_id="sht_1"),
        RunStep(shot_id="sht_2", tail_from_shot_id="sht_1", tail_reason="Continue motion"),
    ]}))
    bind_job(run.project_id, run.run_id, "sht_1", "job_first")
    run = record_terminal(run.project_id, "job_first", JobStatus.succeeded)

    def fake_extract(**kwargs):
        shot = load_shot(run.project_id, "sht_2")
        layout = LayoutReference(id="lref_tail", asset_id="lay_tail", purpose="continuity",
                                 review_status=LayoutReviewStatus.pending_review)
        save_shot(shot.model_copy(update={"layout_refs": [layout]}))
        return {"layout_ref_id": layout.id, "layout_asset_id": layout.asset_id}

    class FailingService:
        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            if needs_user_decision:
                raise CreativeQuestion("Material review needs your decision: choose a camera angle")
            raise ValueError("tail dialogue mismatch")

    seen = []

    async def fake_agent(current, svc):
        seen.append((current.prompt_retry_count, current.prompt_retry_error,
                     current.prepared_tail_layout_ids.get("sht_2")))
        bind_job(current.project_id, current.run_id, "sht_2", "job_recovered")
        return ChatResult(reply="Job started")

    monkeypatch.setattr(continuation, "extract_clip_tail_frame", fake_extract)
    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    monkeypatch.setattr("app.agents.director.DirectorService", lambda **kwargs: FailingService())
    await continuation.continue_run(run.project_id, run.run_id)

    saved = load_run(run.project_id, run.run_id)
    if needs_user_decision:
        assert seen == []
        assert saved.state == "paused"
        assert "choose a camera angle" in saved.paused_reason
    else:
        assert seen == [(1, "tail dialogue mismatch", "lref_tail")]
        assert saved.current_job_id == "job_recovered"
    assert load_shot(run.project_id, "sht_2").layout_refs[0].selected_for_h3 is True


@pytest.mark.asyncio
async def test_empty_agent_turn_pauses_instead_of_repeating(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    run = _run()
    calls = []

    async def fake_agent(run, svc):
        calls.append(run.current_index)

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)
    assert calls == [0]
    assert load_run(run.project_id, run.run_id).state == "paused"


@pytest.mark.asyncio
async def test_prompt_failure_gets_one_fresh_agent_turn_with_error_feedback(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.agents.director.chat_orchestrator import ChatResult

    run = _run()
    attempts = []

    async def fake_agent(current, svc):
        attempts.append((current.prompt_retry_count, current.prompt_retry_error))
        if len(attempts) == 1:
            return ChatResult(reply="Prompt failed", failure_code="PROMPT_GENERATION_FAILED",
                              failure_message="dialogue validation failed")
        bind_job(current.project_id, current.run_id, "sht_1", "job_recovered")
        return ChatResult(reply="Job started")

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)

    saved = load_run(run.project_id, run.run_id)
    assert attempts == [(0, ""), (1, "dialogue validation failed")]
    assert saved.state == "active"
    assert saved.current_job_id == "job_recovered"


@pytest.mark.asyncio
async def test_repeated_prompt_failure_pauses_without_third_agent_turn(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.agents.director.chat_orchestrator import ChatResult

    run = _run()
    attempts = []

    async def fake_agent(current, svc):
        attempts.append(current.prompt_retry_count)
        return ChatResult(reply="Prompt failed", failure_code="PROMPT_GENERATION_FAILED",
                          failure_message="dialogue validation failed")

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)

    saved = load_run(run.project_id, run.run_id)
    assert attempts == [0, 1]
    assert saved.state == "paused"
    assert "dialogue validation failed" in saved.paused_reason


@pytest.mark.asyncio
async def test_stop_during_failed_prompt_prevents_recovery_turn(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.agents.director.chat_orchestrator import ChatResult

    run = _run()
    attempts = []

    async def fake_agent(current, svc):
        attempts.append(current.current_index)
        request_stop(current.project_id, current.run_id)
        return ChatResult(reply="Prompt failed", failure_code="PROMPT_GENERATION_FAILED",
                          failure_message="dialogue validation failed")

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)

    assert attempts == [0]
    assert load_run(run.project_id, run.run_id).state == "stopping"


@pytest.mark.asyncio
async def test_post_start_agent_error_does_not_pause_bound_job(monkeypatch) -> None:
    """Once a Job is bound, its terminal event—not a late chat error—owns progress."""
    from app.core.managed_runs import continuation

    run = _run()

    async def fake_agent(current, svc):
        bind_job(current.project_id, current.run_id, "sht_1", "job_started")
        raise RuntimeError("late final-response failure")

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)

    saved = load_run(run.project_id, run.run_id)
    assert saved.state == "active"
    assert saved.current_job_id == "job_started"
    assert saved.paused_reason == ""


@pytest.mark.asyncio
async def test_stop_prevents_pending_agent_turn(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    run = _run()
    request_stop(run.project_id, run.run_id)
    called = []

    async def fake_agent(run, svc):
        called.append(True)

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)
    assert called == []


@pytest.mark.asyncio
async def test_restart_reconciles_terminal_bound_job_once(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    run = _run()
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                     project_id=run.project_id, params={"shot_id": "sht_1", "project_id": run.project_id})
    bind_job(run.project_id, run.run_id, "sht_1", job.id)
    job.status = JobStatus.succeeded
    save_job(job)
    scheduled = []
    monkeypatch.setattr(continuation, "schedule_continuation", lambda project_id: scheduled.append(project_id))

    continuation.schedule_pending_runs()

    updated = load_run(run.project_id, run.run_id)
    assert updated.current_index == 1
    assert updated.completed_job_ids["sht_1"] == job.id
    assert scheduled == [run.project_id]


@pytest.mark.asyncio
async def test_normal_h3_runner_schedules_after_comfy_release(monkeypatch) -> None:
    from app.core.jobs import runner
    from app.core.jobs.store import load_job
    from app.core.jobs.shot_sync import on_pipeline_job_terminal
    from app.core.managed_runs import continuation

    run = _run()
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                     project_id=run.project_id,
                     params={"shot_id": "sht_1", "project_id": run.project_id})
    bind_job(run.project_id, run.run_id, "sht_1", job.id)
    calls = []

    class FakeAdapter:
        id = "comfy"

        async def run(self, job, pipeline, images, cancel, runtime):
            job.status = JobStatus.succeeded
            save_job(job)
            on_pipeline_job_terminal(job)

    class FakeOrchestrator:
        async def release_generation(self, job_id):
            calls.append("released")

    monkeypatch.setattr(runner._execution_adapters, "resolve", lambda pipeline, job: FakeAdapter())
    monkeypatch.setattr(runner, "_runtime_for", lambda adapter: object())
    monkeypatch.setattr(runner, "get_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(continuation, "schedule_continuation", lambda project_id: calls.append("scheduled"))

    await runner._run_job(job.id, {}, asyncio.Event())

    assert load_job(job.id).status == JobStatus.succeeded
    assert calls == ["released", "scheduled"]


def test_restart_adopts_tagged_job_created_before_run_bind(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    run = _run()
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                     project_id=run.project_id, params={
                         "shot_id": "sht_1", "project_id": run.project_id,
                         "managed_run_id": run.run_id,
                         "managed_step_shot_id": "sht_1",
                         "managed_event_id": run.pending_event_id,
                     })
    scheduled = []
    monkeypatch.setattr(continuation, "schedule_continuation", lambda project_id: scheduled.append(project_id))

    continuation.schedule_pending_runs()

    assert load_run(run.project_id, run.run_id).current_job_id == job.id
    assert scheduled == [run.project_id]


@pytest.mark.asyncio
async def test_restart_retries_stopping_run_cancellation(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    run = _run()
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                     project_id=run.project_id, params={"shot_id": "sht_1"})
    bind_job(run.project_id, run.run_id, "sht_1", job.id)
    request_stop(run.project_id, run.run_id)
    cancelled = []

    async def fake_cancel(job_id):
        cancelled.append(job_id)

    monkeypatch.setattr(continuation, "cancel_job", fake_cancel)
    await continuation.reconcile_stopping_runs()
    assert cancelled == [job.id]
    assert load_run(run.project_id, run.run_id).state == "stopped"


@pytest.mark.asyncio
async def test_restart_cancels_late_tagged_job_after_run_was_stopped(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import finish_stop

    run = _run()
    request_stop(run.project_id, run.run_id)
    finish_stop(run.project_id, run.run_id)
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="late",
                     project_id=run.project_id, params={
                         "shot_id": "sht_1", "managed_run_id": run.run_id,
                         "managed_step_shot_id": "sht_1", "managed_event_id": "start",
                     })
    cancelled = []

    async def fake_cancel(job_id):
        cancelled.append(job_id)

    monkeypatch.setattr(continuation, "cancel_job", fake_cancel)
    await continuation.reconcile_stopping_runs()

    assert cancelled == [job.id]
    assert load_run(run.project_id, run.run_id).state == "stopped"


@pytest.mark.asyncio
async def test_agent_continuation_gets_compact_job_feedback_in_isolated_session(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.api import projects as projects_api
    from app.agents.director import chat as director_chat
    from app.agents.director.chat_orchestrator import ChatResult
    from app.agents.director.harness_runtime import harness_session_id

    run = _run()
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="first",
                     project_id=run.project_id, params={"shot_id": "sht_1"})
    bind_job(run.project_id, run.run_id, "sht_1", job.id)
    job.status = JobStatus.succeeded
    save_job(job)
    run = record_terminal(run.project_id, job.id, JobStatus.succeeded)
    run = record_prompt_retry(run.project_id, run.run_id, "dialogue validation failed")
    captured = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(reply="Ready")

    async def fake_make_chat_fn(*, on_progress=None):
        return object()

    monkeypatch.setattr(director_chat, "handle_chat", fake_chat)
    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    await continuation._agent_turn(run, object())

    assert captured["history"] == []
    assert captured["managed_session_id"] != harness_session_id(run.project_id)
    assert job.id in captured["message"]
    assert "succeeded" in captured["message"]
    assert "sht_2" in captured["message"]
    assert "dialogue validation failed" in captured["message"]


@pytest.mark.asyncio
async def test_agent_continuation_waits_for_user_chat_session(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    from app.api import projects as projects_api
    from app.agents.director import chat as director_chat
    from app.agents.director.chat_orchestrator import ChatResult
    from app.core.projects.chat_sessions import director_chat_sessions

    run = _run()
    user_session = await director_chat_sessions.reserve(run.project_id)
    entered = asyncio.Event()

    async def fake_chat(**kwargs):
        entered.set()
        return ChatResult(reply="Ready")

    async def fake_make_chat_fn(*, on_progress=None):
        return object()

    monkeypatch.setattr(director_chat, "handle_chat", fake_chat)
    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    task = asyncio.create_task(continuation._agent_turn(run, object()))
    await asyncio.sleep(0)
    assert not entered.is_set()
    await director_chat_sessions.finish(run.project_id, user_session.session_id or "")
    await task
    assert entered.is_set()


@pytest.mark.asyncio
async def test_two_shots_advance_on_terminal_events_without_polling(monkeypatch) -> None:
    from app.core.managed_runs import continuation
    run = _run()
    started = []

    async def fake_agent(current, svc):
        step = current_step(current)
        assert step is not None
        shot_id = step.shot_id
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name=shot_id,
                         project_id=current.project_id, params={"shot_id": shot_id})
        bind_job(current.project_id, current.run_id, shot_id, job.id)
        started.append((shot_id, job.id))

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent)
    await continuation.continue_run(run.project_id, run.run_id)
    assert [shot for shot, _ in started] == ["sht_1"]
    first_job = started[0][1]
    record_terminal(run.project_id, first_job, JobStatus.succeeded)
    await continuation.continue_run(run.project_id, run.run_id)
    assert [shot for shot, _ in started] == ["sht_1", "sht_2"]
    record_terminal(run.project_id, started[1][1], JobStatus.succeeded)
    assert load_run(run.project_id, run.run_id).state == "completed"
