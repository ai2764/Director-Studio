"""Director availability and execution of the local H3 start tool."""

from __future__ import annotations

import pytest
import asyncio

from app.agents.director.tool_schema import director_tool_schemas, offered_tool_names
from app.core.managed_runs.models import RunStep
from app.core.managed_runs.store import activate_run, bind_job, create_draft, list_runs, load_run, record_terminal, request_stop
from app.core.projects.models import Shot
from app.core.projects.store import create_project, save_project, save_shot, load_shot
from app.core.jobs.store import create_job
from app.core.schemas import JobStatus
from app.core.managed_runs.context import ManagedTurnScope, managed_turn_scope


@pytest.fixture
def authorize_managed_turn():
    def authorize(run):
        managed_turn_scope.set(ManagedTurnScope(
            project_id=run.project_id, run_id=run.run_id,
            event_id=run.pending_event_id or "", shot_id=run.steps[run.current_index].shot_id,
        ))

    yield authorize
    managed_turn_scope.set(None)


def test_h3_start_tool_is_offered_only_while_management_is_active(authorize_managed_turn) -> None:
    project = create_project("Managed H3", "A short scene")
    shot = Shot(id="sht_managed", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    project = project.model_copy(update={"shot_ids": [shot.id]})

    before = offered_tool_names(director_tool_schemas(project, current_message="continue"))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id)])
    run = activate_run(project.id, draft.run_id, "landscape-480")
    authorize_managed_turn(run)
    during = offered_tool_names(director_tool_schemas(
        project, current_message=f"Managed local H3 run {draft.run_id}: continue"))
    managed_turn_scope.set(None)
    unrelated = offered_tool_names(director_tool_schemas(project, current_message="How is it going?"))

    assert "start_h3_video" not in before
    assert "start_h3_video" in during
    assert "start_h3_video" not in unrelated


def test_explicit_one_off_video_request_offers_tool_without_management() -> None:
    project = create_project("One-off H3", "A short scene")
    shot = Shot(id="sht_one_off", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    names = offered_tool_names(director_tool_schemas(
        project, current_message="Generate Shot 1's video with local H3 now"))
    by_title = offered_tool_names(director_tool_schemas(
        project, current_message="Generate Open video with local H3 now"))
    assert "start_h3_video" in names
    assert "start_h3_video" in by_title
    assert "start_h3_video" not in offered_tool_names(director_tool_schemas(
        project, current_message="How will Shot 1 look?"))


def test_legacy_sanitizer_does_not_inject_storyboard_replace_for_video_start() -> None:
    from app.agents.director.chat_orchestrator import sanitize_tools_for_pipeline
    project = create_project("One-off H3", "A short scene")
    shot = Shot(id="sht_one_off", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    requested = {"name": "start_h3_video", "args": {"shot_id": shot.id}}
    safe, _ = sanitize_tools_for_pipeline([requested], project=project, shots=[shot])
    assert safe == [requested]


@pytest.mark.asyncio
async def test_explicit_one_off_starts_only_named_shot(monkeypatch) -> None:
    from app.agents.director.harness_runtime import BackendTurn
    from app.api import projects as projects_api

    project = create_project("One-off H3", "A short scene")
    shot = Shot(id="sht_one_off", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    submitted = []

    async def fake_submit(shot_id, svc, options):
        submitted.append((shot_id, options.h3_provider))
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="one-off",
                         project_id=project.id, params={"shot_id": shot_id})
        saved = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(saved)
        return saved

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    turn = BackendTurn(project.id, "Generate Shot 1's video with local H3 now", object(), None)
    result = await turn.dispatch("tool", {"name": "start_h3_video", "arguments": {"shot_id": shot.id}, "call_id": "one-off"})
    assert result["ok"] and result["job_id"] == load_shot(project.id, shot.id).h3_job_id
    assert submitted == [(shot.id, "local")]
    assert list_runs(project.id) == []


@pytest.mark.asyncio
async def test_start_h3_video_submits_exact_next_shot_with_run_resolution(monkeypatch, authorize_managed_turn) -> None:
    from app.agents.director.tool_handlers.video import start_h3_video
    from app.api import projects as projects_api

    project = create_project("Managed H3", "A short scene")
    shot = Shot(id="sht_managed", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id)])
    authorize_managed_turn(activate_run(project.id, draft.run_id, "portrait-768"))
    submitted = []

    async def fake_submit(shot_id, svc, options):
        submitted.append((shot_id, options.h3_provider, options.width, options.height))
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                         project_id=project.id, params={"shot_id": shot_id, "h3_provider": "local"})
        saved = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(saved)
        return saved

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    result = await start_h3_video(project.id, shot.id, svc=object())
    repeat = await start_h3_video(project.id, shot.id, svc=object())

    assert submitted == [(shot.id, "local", 768, 1376)]
    assert repeat["job_id"] == result["job_id"]
    assert repeat["already_started"] is True
    assert load_run(project.id, draft.run_id).current_job_id == result["job_id"]


@pytest.mark.asyncio
async def test_start_h3_video_rejects_out_of_order_shot(monkeypatch, authorize_managed_turn) -> None:
    from app.agents.director.tool_handlers.video import start_h3_video

    project = create_project("Managed H3", "A short scene")
    shots = [Shot(id=f"sht_{index}", project_id=project.id, scene_id="scene_1",
                  title=f"Shot {index}", script_beat="A scene.", duration_s=5)
             for index in (1, 2)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id for shot in shots]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id) for shot in shots])
    authorize_managed_turn(activate_run(project.id, draft.run_id, "landscape-480"))

    with pytest.raises(ValueError, match="next planned Shot"):
        await start_h3_video(project.id, shots[1].id, svc=object())


@pytest.mark.asyncio
async def test_harness_tool_returns_bound_job_on_repeated_new_call_id(monkeypatch, authorize_managed_turn) -> None:
    from app.agents.director.harness_runtime import BackendTurn
    from app.api import projects as projects_api

    project = create_project("Managed H3", "A short scene")
    shot = Shot(id="sht_managed", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id)])
    authorize_managed_turn(activate_run(project.id, draft.run_id, "landscape-480"))
    submitted = []

    async def fake_submit(shot_id, svc, options):
        submitted.append(shot_id)
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                         project_id=project.id, params={"shot_id": shot_id})
        updated = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(updated)
        return updated

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    turn = BackendTurn(project.id, f"Managed local H3 run {draft.run_id}: next Shot", object(), None)
    first = await turn.dispatch("tool", {"name": "start_h3_video", "arguments": {"shot_id": shot.id}, "call_id": "call-1"})
    second = await turn.dispatch("tool", {"name": "start_h3_video", "arguments": {"shot_id": shot.id}, "call_id": "call-2"})
    third = await turn.dispatch("tool", {"name": "start_h3_video", "arguments": {"shot_id": shot.id}, "call_id": "call-3"})
    assert first["ok"] and second["ok"] and third["ok"]
    assert first["job_id"] == second["job_id"]
    assert second["job_id"] == third["job_id"]
    assert submitted == [shot.id]


@pytest.mark.asyncio
async def test_stop_during_preflight_cancels_late_job(monkeypatch, authorize_managed_turn) -> None:
    from app.agents.director.tool_handlers import video
    from app.api import projects as projects_api

    project = create_project("Stop race", "A short scene")
    shot = Shot(id="sht_race", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id)])
    authorize_managed_turn(activate_run(project.id, draft.run_id, "landscape-480"))
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = []

    async def fake_submit(shot_id, svc, options):
        entered.set()
        await release.wait()
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="late",
                         project_id=project.id, params={"shot_id": shot_id})
        saved = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(saved)
        return saved

    async def fake_cancel(job_id):
        cancelled.append(job_id)

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    monkeypatch.setattr(video, "cancel_job", fake_cancel)
    task = asyncio.create_task(video.start_h3_video(project.id, shot.id, svc=object()))
    await entered.wait()
    request_stop(project.id, draft.run_id)
    release.set()
    with pytest.raises(ValueError, match="stopped"):
        await task
    assert cancelled == [load_shot(project.id, shot.id).h3_job_id]
    assert load_run(project.id, draft.run_id).current_job_id is None


@pytest.mark.asyncio
async def test_active_run_rejects_ordinary_chat_and_unprepared_tail(authorize_managed_turn) -> None:
    from app.agents.director.tool_handlers.video import start_h3_video

    project = create_project("Tail guard", "Two linked shots")
    shots = [Shot(id=f"sht_{i}", project_id=project.id, scene_id="scene_1",
                  title=f"Shot {i}", script_beat="Door continuity", duration_s=5)
             for i in (1, 2)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id for shot in shots]}))
    draft = create_draft(project.id, [RunStep(shot_id=shots[0].id),
                                      RunStep(shot_id=shots[1].id, tail_from_shot_id=shots[0].id,
                                              tail_reason="Match the closing door")])
    activate_run(project.id, draft.run_id, "landscape-480")
    bind_job(project.id, draft.run_id, shots[0].id, "job_first")
    advanced = record_terminal(project.id, "job_first", JobStatus.succeeded)

    with pytest.raises(ValueError, match="coordinator-issued"):
        await start_h3_video(project.id, shots[1].id, svc=object(), one_off_authorized=True)
    authorize_managed_turn(advanced)
    with pytest.raises(ValueError, match="Planned tail frame"):
        await start_h3_video(project.id, shots[1].id, svc=object())


@pytest.mark.asyncio
async def test_failed_managed_submission_pauses_without_agent_retry(monkeypatch, authorize_managed_turn) -> None:
    from app.agents.director.tool_handlers.video import start_h3_video
    from app.api import projects as projects_api

    project = create_project("Failed H3", "One shot")
    shot = Shot(id="sht_failed", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id)])
    authorize_managed_turn(activate_run(project.id, draft.run_id, "landscape-480"))
    attempts = []

    async def fake_submit(shot_id, svc, options):
        attempts.append(shot_id)
        raise ValueError("missing Picture")

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    with pytest.raises(ValueError, match="missing Picture"):
        await start_h3_video(project.id, shot.id, svc=object())
    with pytest.raises(ValueError, match="No active managed H3 run"):
        await start_h3_video(project.id, shot.id, svc=object())
    assert attempts == [shot.id]
    assert load_run(project.id, draft.run_id).state == "paused"


@pytest.mark.asyncio
async def test_shot_edit_during_submit_cancels_job_and_pauses(monkeypatch, authorize_managed_turn) -> None:
    from app.agents.director.tool_handlers import video
    from app.api import projects as projects_api

    project = create_project("Changed H3", "One shot")
    shot = Shot(id="sht_changed", project_id=project.id, scene_id="scene_1",
                title="Open", script_beat="A door opens.", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    draft = create_draft(project.id, [RunStep(shot_id=shot.id)])
    authorize_managed_turn(activate_run(project.id, draft.run_id, "landscape-480"))
    cancelled = []

    async def fake_submit(shot_id, svc, options):
        changed = load_shot(project.id, shot_id).model_copy(update={"script_beat": "User changed beat"})
        save_shot(changed)
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="changed",
                         project_id=project.id, params={"shot_id": shot_id})
        changed = changed.model_copy(update={"h3_job_id": job.id})
        save_shot(changed)
        return changed

    async def fake_cancel(job_id):
        cancelled.append(job_id)

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    monkeypatch.setattr(video, "cancel_job", fake_cancel)
    with pytest.raises(ValueError, match="changed during H3 preflight"):
        await video.start_h3_video(project.id, shot.id, svc=object())
    assert cancelled == [load_shot(project.id, shot.id).h3_job_id]
    assert load_run(project.id, draft.run_id).state == "paused"
