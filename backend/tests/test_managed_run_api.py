"""Managed local-H3 run plan and activation API behavior."""

from __future__ import annotations

import json
import pytest

from fastapi.testclient import TestClient

from app.api import projects as projects_api
from app.core.projects.models import Shot
from app.core.projects.store import create_project, load_project, save_project, save_shot
from app.core.managed_runs.models import RunStep
from app.core.managed_runs.store import _save_run, create_draft
from app.main import create_app


@pytest.fixture(autouse=True)
def isolate_managed_agent_worker(monkeypatch):
    """API contract tests must not launch an unmocked local LLM background turn."""
    from app.core.managed_runs import continuation
    monkeypatch.setattr(continuation, "schedule_pending_runs", lambda: None)
    monkeypatch.setattr(continuation, "schedule_continuation", lambda _project_id: None)
    from app import main as app_main
    async def no_recovery():
        return []
    monkeypatch.setattr(app_main, "recover_interrupted_jobs", no_recovery)


def _two_shot_project():
    project = create_project("Tail continuity", "Two linked shots")
    first = Shot(
        id="sht_run_first", project_id=project.id, scene_id="sc01",
        title="Door closes", script_beat="The door closes behind Mia.", duration_s=5,
    )
    second = Shot(
        id="sht_run_second", project_id=project.id, scene_id="sc01",
        title="Door opens", script_beat="Continue from the prior door closing frame.", duration_s=5,
    )
    save_shot(first)
    save_shot(second)
    save_project(project.model_copy(update={"shot_ids": [first.id, second.id]}))
    return project, first, second


def test_plan_rejects_inputs_changed_during_inference(monkeypatch):
    project, first, _ = _two_shot_project()
    async def make(**kwargs):
        async def infer(*args, **kwargs):
            save_shot(first.model_copy(update={"script_beat": "New authored action"}))
            return {"content": '{"tail_handoffs":[]}'}
        return infer
    monkeypatch.setattr(projects_api, "_make_chat_fn", make)
    with TestClient(create_app()) as client:
        result = client.post(f"/api/projects/{project.id}/managed-run/plan")
    assert result.status_code == 409


def test_plan_rejects_storyboard_that_misses_authored_runtime(monkeypatch):
    project, _, _ = _two_shot_project()
    save_project(load_project(project.id).model_copy(update={"script_text": "Create a 2–3 minute film."}))
    async def make(**kwargs):
        async def infer(*args, **kwargs):
            return {"content": '{"tail_handoffs":[]}'}
        return infer
    monkeypatch.setattr(projects_api, "_make_chat_fn", make)
    with TestClient(create_app()) as client:
        result = client.post(f"/api/projects/{project.id}/managed-run/plan")
    assert result.status_code == 422
    assert "120s" in result.json()["detail"]


@pytest.mark.parametrize("grounded", [True, False])
def test_plan_reports_only_grounded_directing_conflicts(monkeypatch, grounded):
    from app.agents.director.brief import remember_directing_request
    project, _, second = _two_shot_project()
    requirement = "Tao must operate the handheld camera."
    remember_directing_request(project.id, requirement)
    save_shot(second.model_copy(update={"camera_motion": "Locked external camera."}))
    async def make(**kwargs):
        async def infer(*args, **kwargs):
            return {"content": json.dumps({"tail_handoffs": [], "storyboard_issues": [
                {"requirement_quote": requirement, "shot_id": second.id, "field": "camera_motion",
                 "shot_quote": "Locked external camera." if grounded else "Invented dialogue not in this shot.",
                 "reason": "The handheld camera requirement conflicts with a locked external camera."}
            ]})}
        return infer
    monkeypatch.setattr(projects_api, "_make_chat_fn", make)
    with TestClient(create_app()) as client:
        result = client.post(f"/api/projects/{project.id}/managed-run/plan")
    assert result.status_code == (422 if grounded else 200)
    if grounded:
        assert "handheld camera" in result.json()["detail"]


def test_plan_route_saves_agent_tail_handoff_as_draft(monkeypatch) -> None:
    project, first, second = _two_shot_project()
    observed = {}

    async def fake_make_chat_fn(*, on_progress=None):
        async def chat_fn(system, user, **kwargs):
            observed["system"] = system
            observed["user"] = user
            observed["format"] = kwargs.get("format")
            return {"content": json.dumps({"tail_handoffs": [{
                "target_shot_id": second.id,
                "source_shot_id": first.id,
                "reason": "The brief continues the door-closing state.",
            }]})}
        return chat_fn

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/plan")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "draft"
    assert body["steps"][1]["tail_from_shot_id"] == first.id
    assert body["steps"][1]["tail_reason"] == "The brief continues the door-closing state."
    assert "Continue from the prior door closing frame" in observed["user"]
    assert observed["format"]["type"] == "object"


def test_plan_route_builds_complete_project_order_from_sparse_handoff_decisions(monkeypatch) -> None:
    """The model chooses continuity; it must not have to copy the Shot roster."""
    project, first, second = _two_shot_project()

    async def fake_make_chat_fn(*, on_progress=None):
        async def chat_fn(system, user, **kwargs):
            return {"content": json.dumps({"tail_handoffs": [{
                "target_shot_id": second.id,
                "source_shot_id": first.id,
                "reason": "Continue the closing door into the next opening frame.",
            }]})}
        return chat_fn

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/plan")

    assert response.status_code == 200
    assert response.json()["steps"] == [
        {"shot_id": first.id, "tail_from_shot_id": None, "tail_reason": ""},
        {
            "shot_id": second.id,
            "tail_from_shot_id": first.id,
            "tail_reason": "Continue the closing door into the next opening frame.",
        },
    ]


def test_plan_route_rejects_handoff_for_unknown_target(monkeypatch) -> None:
    """A hallucinated target ID must not be silently ignored."""
    project, first, _second = _two_shot_project()

    async def fake_make_chat_fn(*, on_progress=None):
        async def chat_fn(system, user, **kwargs):
            return {"content": json.dumps({"tail_handoffs": [{
                "target_shot_id": "sht_hallucinated",
                "source_shot_id": first.id,
                "reason": "Invalid target.",
            }]})}
        return chat_fn

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/plan")

    assert response.status_code == 422
    assert "unknown Shot" in response.json()["detail"]


def test_plan_route_rejects_duplicate_handoff_target(monkeypatch) -> None:
    """Conflicting model decisions for one target must not overwrite each other."""
    project, first, second = _two_shot_project()

    async def fake_make_chat_fn(*, on_progress=None):
        async def chat_fn(system, user, **kwargs):
            return {"content": json.dumps({"tail_handoffs": [
                {
                    "target_shot_id": second.id,
                    "source_shot_id": first.id,
                    "reason": "First decision.",
                },
                {
                    "target_shot_id": second.id,
                    "source_shot_id": first.id,
                    "reason": "Conflicting duplicate.",
                },
            ]})}
        return chat_fn

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/plan")

    assert response.status_code == 422
    assert "more than once" in response.json()["detail"]


def test_start_reads_resolution_from_json_body() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/start",
            json={"resolution_preset": "landscape-768"},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["resolution_preset"] == "landscape-768"
    assert response.json()["selected_shot_ids"] == [first.id, second.id]
    assert response.json()["pending_shot_ids"] == [first.id, second.id]


def test_run_route_accepts_exact_selection_and_returns_warnings() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={
                "shot_ids": [second.id],
                "resolution_preset": "landscape-768",
            },
        )

    assert response.status_code == 200
    assert response.json()["selected_shot_ids"] == [second.id]
    assert response.json()["pending_shot_ids"] == [second.id]
    assert response.json()["skipped_shots"] == {}


def test_get_marks_changed_brief_stale_and_run_refuses_it() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    save_shot(second.model_copy(update={"script_beat": "Changed"}))

    with TestClient(create_app()) as client:
        view = client.get(f"/api/projects/{project.id}/managed-run")
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={"shot_ids": [second.id]},
        )

    assert view.status_code == 200
    assert view.json()["is_stale"] is True
    assert view.json()["stale_reason"]
    assert response.status_code == 409


def test_run_route_rejects_empty_and_unknown_selections() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )

    with TestClient(create_app()) as client:
        empty = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={"shot_ids": [], "resolution_preset": "landscape-480"},
        )
        unknown = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={
                "shot_ids": ["sht_unknown"],
                "resolution_preset": "landscape-480",
            },
        )

    assert empty.status_code == 409
    assert "Select at least one" in empty.json()["detail"]
    assert unknown.status_code == 409
    assert "outside this plan" in unknown.json()["detail"]


def test_run_route_preserves_resolution_across_batches() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )

    with TestClient(create_app()) as client:
        started = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={
                "shot_ids": [first.id],
                "resolution_preset": "landscape-480",
            },
        )
        stopped = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/stop"
        )
        changed = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={
                "shot_ids": [second.id],
                "resolution_preset": "portrait-480",
            },
        )

    assert started.status_code == 200
    assert stopped.status_code == 200
    assert changed.status_code == 409
    assert "cannot change" in changed.json()["detail"]


@pytest.mark.parametrize("state", ["paused", "stopped", "completed"])
def test_run_route_resumes_inactive_saved_plan(state: str) -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    _save_run(draft.model_copy(update={
        "state": state,
        "resolution_preset": "landscape-480",
    }))

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={"shot_ids": [second.id]},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["pending_shot_ids"] == [second.id]


def test_run_route_returns_dependency_warning_without_starting() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [
        RunStep(shot_id=first.id),
        RunStep(
            shot_id=second.id,
            tail_from_shot_id=first.id,
            tail_reason="Continue the door",
        ),
    ])

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={
                "shot_ids": [second.id],
                "resolution_preset": "landscape-480",
            },
        )

    assert response.status_code == 200
    assert response.json()["state"] == "paused"
    assert response.json()["pending_shot_ids"] == []
    assert second.id in response.json()["skipped_shots"]


def test_run_route_does_not_delete_existing_job_outputs() -> None:
    from app.core.jobs.store import create_job, job_dir

    project, first, second = _two_shot_project()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="existing video",
        project_id=project.id,
        params={"shot_id": first.id},
    )
    output = job_dir(job.id, project_id=project.id) / "outputs" / "video.mp4"
    output.write_bytes(b"existing-video")

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
            json={
                "shot_ids": [first.id],
                "resolution_preset": "landscape-480",
            },
        )

    assert response.status_code == 200
    assert output.read_bytes() == b"existing-video"


def test_current_run_prefers_active_over_a_newer_draft() -> None:
    project, first, second = _two_shot_project()
    steps = [RunStep(shot_id=first.id), RunStep(shot_id=second.id)]
    active = create_draft(project.id, steps)
    from app.core.managed_runs.store import activate_run
    activate_run(project.id, active.run_id, "portrait-480")
    create_draft(project.id, steps)

    with TestClient(create_app()) as client:
        response = client.get(f"/api/projects/{project.id}/managed-run")

    assert response.status_code == 200
    assert response.json()["run_id"] == active.run_id


def test_start_rejects_story_changed_after_plan() -> None:
    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    changed = load_project(project.id)
    assert changed is not None
    save_project(changed.model_copy(update={"script_text": "A different story"}))

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/projects/{project.id}/managed-run/{draft.run_id}/start",
            json={"resolution_preset": "landscape-768"},
        )

    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()


def test_stop_marks_run_inactive_before_any_next_shot() -> None:
    project, first, second = _two_shot_project()
    from app.core.managed_runs.store import activate_run

    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    activate_run(project.id, draft.run_id, "landscape-480")

    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/{draft.run_id}/stop")

    assert response.status_code == 200
    assert response.json()["state"] == "stopped"


def test_stop_cancels_tagged_job_created_before_binding(monkeypatch) -> None:
    from app.api import managed_runs as managed_api
    from app.core.jobs.store import create_job
    from app.core.managed_runs.store import activate_run, load_run

    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    run = activate_run(project.id, draft.run_id, "landscape-480")
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="unbound",
                     project_id=project.id, params={
                         "shot_id": first.id, "managed_run_id": run.run_id,
                         "managed_step_shot_id": first.id, "managed_event_id": "start",
                     })
    cancelled = []

    async def fake_cancel(job_id):
        cancelled.append((job_id, load_run(project.id, run.run_id).state))

    monkeypatch.setattr(managed_api, "cancel_job", fake_cancel)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/{run.run_id}/stop")

    assert response.status_code == 200
    assert response.json()["state"] == "stopped"
    assert cancelled == [(job.id, "stopping")]


def test_stop_marks_stopping_before_cancelling_bound_job(monkeypatch) -> None:
    from app.api import managed_runs as managed_api
    from app.core.managed_runs.store import activate_run, bind_job, load_run
    from app.core.jobs.store import create_job

    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    activate_run(project.id, draft.run_id, "landscape-480")
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                     project_id=project.id, params={"shot_id": first.id})
    bind_job(project.id, draft.run_id, first.id, job.id)
    observed = []

    async def fake_cancel(job_id):
        observed.append((job_id, load_run(project.id, draft.run_id).state))

    monkeypatch.setattr(managed_api, "cancel_job", fake_cancel)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/{draft.run_id}/stop")

    assert response.status_code == 200
    assert observed == [(job.id, "stopping")]
    assert response.json()["state"] == "stopped"


def test_failed_cancel_remains_retryable_stopping(monkeypatch) -> None:
    from app.api import managed_runs as managed_api
    from app.core.managed_runs.store import activate_run, bind_job, load_run
    from app.core.jobs.store import create_job

    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    activate_run(project.id, draft.run_id, "landscape-480")
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="test",
                     project_id=project.id, params={"shot_id": first.id})
    bind_job(project.id, draft.run_id, first.id, job.id)

    async def fake_cancel(_job_id):
        raise RuntimeError("Comfy unavailable")

    monkeypatch.setattr(managed_api, "cancel_job", fake_cancel)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/projects/{project.id}/managed-run/{draft.run_id}/stop")

    assert response.status_code == 503
    assert load_run(project.id, draft.run_id).state == "stopping"


def test_manual_h3_submit_is_blocked_while_management_active() -> None:
    from app.core.managed_runs.store import activate_run

    project, first, second = _two_shot_project()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    activate_run(project.id, draft.run_id, "landscape-480")
    with TestClient(create_app()) as client:
        response = client.post(f"/api/shots/{first.id}/submit", json={"h3_provider": "local"})
    assert response.status_code == 409
    assert "Stop the managed run" in response.json()["detail"]
