"""Managed local-H3 run plan and activation API behavior."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import projects as projects_api
from app.core.projects.models import Shot
from app.core.projects.store import create_project, load_project, save_project, save_shot
from app.core.managed_runs.models import RunStep
from app.core.managed_runs.store import create_draft
from app.main import create_app


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


def test_plan_route_saves_agent_tail_handoff_as_draft(monkeypatch) -> None:
    project, first, second = _two_shot_project()
    observed = {}

    async def fake_make_chat_fn(*, on_progress=None):
        async def chat_fn(system, user, **kwargs):
            observed["system"] = system
            observed["user"] = user
            observed["format"] = kwargs.get("format")
            return {"content": json.dumps({"steps": [
                {"shot_id": first.id, "tail_from_shot_id": None, "tail_reason": ""},
                {"shot_id": second.id, "tail_from_shot_id": first.id,
                 "tail_reason": "The brief continues the door-closing state."},
            ]})}
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
