import pytest
from test_director_material_review import material_shot

from app.core.projects.store import create_project


def test_stages_follow_observed_work_and_prompt_only_completion():
    from app.agents.director.execution_progress import ExecutionProgress
    project = create_project("Film", "A short scene.")
    progress = ExecutionProgress.start(project.id, "run-1")
    progress.apply_event({"type": "execution_operation", "tool": "write_prompt", "state": "started", "shot_id": "s1"})
    progress.apply_event({"type": "status", "phase": "material_review", "state": "reused", "text": "Evidence unchanged"})
    progress.apply_event({"type": "status", "phase": "prompt_writing", "state": "started"})
    progress.apply_event({"type": "status", "phase": "prompt_writing", "state": "completed"})
    progress.apply_event({"type": "execution_operation", "tool": "write_prompt", "state": "completed", "shot_id": "s1"})
    progress.finish("completed")
    state = progress.state
    assert state["stages"]["materials"]["status"] == "reused"
    assert state["stages"]["prompt"]["status"] == "completed"
    assert state["stages"]["generation"]["status"] == "not_requested"
    assert state["shot_id"] == "s1"
    assert "percent" not in state


def test_persisted_progress_rejects_previous_run_writes():
    from app.agents.director.execution_progress import ExecutionProgress, load_execution_progress
    project = create_project("Film", "A scene.")
    old = ExecutionProgress.start(project.id, "old")
    current = ExecutionProgress.start(project.id, "new")
    old.apply_event({"type": "status", "phase": "prompt_writing", "state": "failed"})
    old.finish("cancelled")
    assert load_execution_progress(project.id)["run_id"] == "new"
    current.finish("cancelled")
    assert load_execution_progress(project.id)["status"] == "cancelled"


def test_submitted_generation_is_not_quality_approval_and_unknown_events_do_not_advance():
    from app.agents.director.execution_progress import ExecutionProgress
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    progress.apply_event({"type": "status", "text": "Maybe it is done", "phase": "unknown"})
    assert progress.state["current_stage"] == "plan"
    progress.apply_event({"type": "execution_operation", "tool": "start_h3_video", "state": "started", "shot_id": "s1"})
    progress.apply_event({"type": "execution_operation", "tool": "start_h3_video", "state": "completed", "shot_id": "s1", "job_id": "j1"})
    progress.finish("completed")
    assert progress.state["stages"]["generation"]["status"] == "submitted"
    assert not progress.state.get("quality_approved", False)


def test_failure_and_cancel_are_recovered_from_current_stage():
    from app.agents.director.execution_progress import ExecutionProgress
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    progress.apply_event({"type": "status", "phase": "prompt_repair", "state": "started", "text": "Repairing prompt"})
    progress.finish("failed")
    assert progress.state["stages"]["prompt"]["status"] == "failed"
    assert progress.state["stages"]["generation"]["status"] == "not_requested"


def test_token_stream_does_not_read_or_write_progress_files(monkeypatch):
    from app.agents.director import execution_progress as module
    project = create_project("Film", "A scene.")
    progress = module.ExecutionProgress.start(project.id, "run")
    def unexpected(*args):
        raise AssertionError("Token events must not touch the progress store")
    monkeypatch.setattr(module, "load_execution_progress", unexpected)
    assert progress.apply_event({"type": "token", "text": "a"}) is False


@pytest.mark.asyncio
async def test_cancelled_phase_is_not_reported_as_failed():
    import asyncio
    from app.agents.director.execution_progress import ExecutionProgress
    from app.agents.director.progress import report_phase
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    async def observe(event):
        progress.apply_event(event)
    with pytest.raises(asyncio.CancelledError):
        async with report_phase(observe, "prompt_writing", "Writing prompt"):
            raise asyncio.CancelledError()
    progress.finish("cancelled")
    assert progress.state["stages"]["prompt"]["status"] == "cancelled"


def test_inner_writer_completion_does_not_finish_outer_validations():
    from app.agents.director.execution_progress import ExecutionProgress
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    progress.apply_event({"type": "execution_operation", "tool": "write_prompt", "state": "started"})
    progress.apply_event({"type": "status", "phase": "prompt_writing", "state": "started"})
    progress.apply_event({"type": "status", "phase": "prompt_writing", "state": "completed"})
    assert progress.state["stages"]["prompt"]["status"] == "running"
    progress.apply_event({"type": "execution_operation", "tool": "write_prompt", "state": "failed"})
    assert progress.state["stages"]["prompt"]["status"] == "failed"


@pytest.mark.asyncio
async def test_completed_reference_job_waits_for_evidence_review(monkeypatch):
    from types import SimpleNamespace
    from app.agents.director.execution_progress import ExecutionProgress
    from app.api.projects import project_chat_session_endpoint
    from app.core import jobs
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    progress.apply_event({"type": "execution_operation", "tool": "queue_ref_frame",
                          "state": "submitted", "shot_id": "s1", "job_id": "job"})
    progress.finish("completed")
    monkeypatch.setattr(jobs, "load_job", lambda _: SimpleNamespace(
        project_id=project.id, params={"shot_id": "s1"}, status="succeeded", updated_at="2026-11-03T10:10:00Z"))
    snapshot = await project_chat_session_endpoint(project.id)
    assert snapshot.execution_progress["stages"]["materials"]["status"] == "ready"


@pytest.mark.asyncio
async def test_stream_exposes_durable_stage_events_and_session_recovers_them(monkeypatch):
    import json
    from app.api import projects as api
    from app.agents.director import chat
    from app.agents.director.chat import ChatResult
    project = create_project("Film", "A scene.")
    async def make_chat_fn(**kwargs):
        return None
    async def handle_chat(*, on_progress, **kwargs):
        await on_progress({"type": "status", "phase": "prompt_writing", "state": "started"})
        await on_progress({"type": "status", "phase": "prompt_writing", "state": "completed"})
        return ChatResult(reply="Prompt ready", project=project)
    monkeypatch.setattr(api, "_make_chat_fn", make_chat_fn)
    monkeypatch.setattr(chat, "handle_chat", handle_chat)
    response = await api.project_chat_stream_endpoint(project.id, api.ChatBody(message="Write prompt"), svc=object())
    events = [json.loads(chunk.strip()[5:]) async for chunk in response.body_iterator]
    stages = [ev["data"] for ev in events if ev["type"] == "execution_progress"]
    assert stages[0]["current_stage"] == "plan"
    assert stages[-1]["status"] == "completed"
    snapshot = await api.project_chat_session_endpoint(project.id)
    assert snapshot.execution_progress["run_id"] == stages[-1]["run_id"]
    assert snapshot.execution_progress["stages"]["generation"]["status"] == "not_requested"


@pytest.mark.asyncio
async def test_session_tracks_actual_video_job_without_claiming_quality(monkeypatch):
    from types import SimpleNamespace
    from app.agents.director.execution_progress import ExecutionProgress
    from app.api.projects import project_chat_session_endpoint
    from app.core import jobs
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    progress.apply_event({"type": "execution_operation", "tool": "start_h3_video", "state": "completed", "shot_id": "s1", "job_id": "job"})
    progress.finish("completed")
    monkeypatch.setattr(jobs, "load_job", lambda _: SimpleNamespace(
        project_id=project.id, params={"shot_id": "s1"}, status="succeeded", updated_at="2026-11-03T10:10:00Z"))
    snapshot = await project_chat_session_endpoint(project.id)
    generation = snapshot.execution_progress["stages"]["generation"]
    assert generation["status"] == "completed"
    assert generation["quality_status"] == "not_reviewed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status, label", [("running", "Generating video."), ("failed", "Video generation failed.")])
async def test_video_activity_does_not_claim_success_for_non_success_job(monkeypatch, status, label):
    from types import SimpleNamespace
    from app.agents.director.execution_progress import ExecutionProgress
    from app.api.projects import project_chat_session_endpoint
    from app.core import jobs
    project = create_project("Film", "A scene.")
    progress = ExecutionProgress.start(project.id, "run")
    progress.apply_event({"type": "execution_operation", "tool": "start_h3_video", "state": "completed", "shot_id": "s1", "job_id": "job"})
    progress.finish("completed")
    monkeypatch.setattr(jobs, "load_job", lambda _: SimpleNamespace(
        project_id=project.id, params={"shot_id": "s1"}, status=status, updated_at="2026-11-03T10:10:00Z"))
    snapshot = await project_chat_session_endpoint(project.id)
    assert snapshot.execution_progress["activity"] == label


@pytest.mark.asyncio
async def test_queue_action_without_job_does_not_complete_materials():
    from types import SimpleNamespace
    from app.agents.director.chat import _run_tools
    from app.agents.director.execution_progress import ExecutionProgress
    from app.core.projects.models import Shot
    from app.core.projects.store import save_project, save_shot
    project = create_project("Film", "A scene.")
    shot = Shot(id="s1", project_id=project.id, scene_id="scene", title="One", script_beat="Wait", duration_s=6)
    project.shot_ids = [shot.id]
    save_project(project); save_shot(shot)
    async def no_queue(*args, **kwargs):
        return []
    progress = ExecutionProgress.start(project.id, "run")
    async def observe(event):
        progress.apply_event(event)
    await _run_tools(project_id=project.id, tools=[{"name": "queue_ref_frame", "args": {"shot_id": shot.id}}],
                     svc=SimpleNamespace(queue_ref_frames=no_queue), actions=[], on_progress=observe)
    assert progress.state["stages"]["materials"]["status"] != "completed"


@pytest.mark.asyncio
async def test_prompt_alias_and_index_are_scoped_before_inner_phases(material_shot):
    from app.agents.director.chat import _run_tools
    from app.agents.director.execution_progress import ExecutionProgress
    from app.agents.director.service import DirectorService
    from test_director_material_review import Orchestrator, Provider
    project, _, neighbor, _ = material_shot
    progress = ExecutionProgress.start(project.id, "run")
    observed = []
    async def observe(event):
        progress.apply_event(event)
        if event.get("phase") == "prompt_writing":
            observed.append(progress.state["shot_id"])
    orch = Orchestrator()
    await _run_tools(project_id=project.id, tools=[{"name": "rewrite_prompt", "args": {"shot_index": 2}}],
                     svc=DirectorService(plan_provider=Provider(orch), orchestrator=orch),
                     actions=[], on_progress=observe)
    assert observed and set(observed) == {neighbor.id}


@pytest.mark.asyncio
async def test_malformed_selector_still_returns_a_recoverable_tool_receipt():
    from types import SimpleNamespace
    from app.agents.director.chat import _run_tools
    project = create_project("Film", "A scene.")
    receipts = []
    notes, _ = await _run_tools(project_id=project.id,
        tools=[{"name": "write_prompt", "args": {"shot_index": "unknown"}}],
        svc=SimpleNamespace(), actions=[], result_payloads=receipts)
    assert notes and receipts[-1]["ok"] is False
