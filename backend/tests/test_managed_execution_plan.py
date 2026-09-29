"""Managed planning schedules the saved edit; it never reauthors or reapproves it."""
import json

import pytest
from fastapi import HTTPException

from app.api import projects as projects_api
from app.api.managed_runs import plan_managed_run
from app.core.managed_runs import store
from app.core.projects.models import PromptSections, Shot, ShotStatus
from app.core.projects.store import create_project, save_project, save_shot, project_dir


@pytest.fixture
def saved_edit():
    project = create_project("Documentary", "Ari films while Sam interviews a trader.")
    first = Shot(id="sht_open", project_id=project.id, scene_id="market", title="Arrival",
        script_beat="The pair arrive at the market.", duration_s=15)
    second = Shot(id="sht_interview", project_id=project.id, scene_id="market", title="Interview", duration_s=15,
        composition="Sam and the trader face the camera.",
        script_beat="Ari records from behind the camera.", h3_job_id="job_existing",
        prompt_sections=PromptSections(summary="Keep this authored prompt."))
    save_shot(first)
    save_shot(second)
    project = project.model_copy(update={"shot_ids": [first.id, second.id]})
    save_project(project)
    return project, first, second


def inference(monkeypatch, response):
    calls = []
    async def make(**kwargs):
        async def infer(system, user, **kwargs):
            calls.append({"system": system, "user": user, **kwargs})
            assert len(calls) == 1, "Execution planning must not start a storyboard repair loop"
            return {"content": json.dumps(response)}
        return infer
    monkeypatch.setattr(projects_api, "_make_chat_fn", make)
    return calls


def authored_files(project_id):
    root = project_dir(project_id)
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in [root / "project.json", *sorted((root / "shots").glob("*.json"))]}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ShotStatus.needs_review, ShotStatus.running])
async def test_unsolicited_creative_judgment_cannot_block_or_rewrite_saved_shots(saved_edit, monkeypatch, status):
    project, first, second = saved_edit
    save_shot(second.model_copy(update={"status": status}))
    before = authored_files(project.id)
    calls = inference(monkeypatch, {"tail_handoffs": [], "storyboard_issues": [{
        "requirement_quote": project.script_text, "shot_id": second.id,
        "field": "composition", "shot_quote": second.composition,
        "reason": "Ari must appear in every shot."}],
        "camera_refinements": [{"shot_id": second.id, "changes": {"composition": "Show Ari too."}}]})
    result = await plan_managed_run(project.id)
    assert result.state == "draft"
    assert [step.shot_id for step in result.steps] == [first.id, second.id]
    assert result.recovery_history == []
    assert authored_files(project.id) == before
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_saved_short_edit_is_not_rejected_against_old_film_runtime(saved_edit, monkeypatch):
    project, _, _ = saved_edit
    save_project(project.model_copy(update={"script_text": "Create a 2–3 minute film."}))
    before = authored_files(project.id)
    inference(monkeypatch, {"tail_handoffs": []})
    result = await plan_managed_run(project.id)
    assert len(result.steps) == 2
    assert authored_files(project.id) == before


@pytest.mark.asyncio
async def test_model_contract_only_requests_handoffs_and_preserves_them(saved_edit, monkeypatch):
    project, first, second = saved_edit
    calls = inference(monkeypatch, {"tail_handoffs": [{"target_shot_id": second.id,
        "source_shot_id": first.id, "reason": "Continue the same door action."}]})
    result = await plan_managed_run(project.id)
    assert result.steps[1].tail_from_shot_id == first.id
    assert set(calls[0]["format"]["properties"]) == {"tail_handoffs"}


@pytest.mark.asyncio
async def test_local_h3_duration_limit_still_blocks_before_inference(saved_edit, monkeypatch):
    project, _, second = saved_edit
    save_shot(second.model_copy(update={"duration_s": 20}))
    calls = inference(monkeypatch, {"tail_handoffs": []})
    with pytest.raises(HTTPException) as error:
        await plan_managed_run(project.id)
    assert error.value.status_code == 422
    assert "unsupported" in str(error.value.detail)
    assert calls == []
    assert store.list_runs(project.id) == []


@pytest.mark.asyncio
async def test_failed_draft_save_never_touches_authored_files(saved_edit, monkeypatch):
    project, _, _ = saved_edit
    before = authored_files(project.id)
    inference(monkeypatch, {"tail_handoffs": []})
    def fail(_run):
        raise OSError("Fixture disk failure")
    monkeypatch.setattr(store, "_save_run", fail)
    with pytest.raises(OSError, match="Fixture disk failure"):
        await plan_managed_run(project.id)
    assert authored_files(project.id) == before
    assert store.list_runs(project.id) == []


@pytest.mark.asyncio
async def test_edit_at_publication_boundary_prevents_stale_draft(saved_edit, monkeypatch):
    from contextlib import contextmanager
    from app.api import managed_runs as managed_api
    from app.core.projects.store import load_shot

    project, _, second = saved_edit
    inference(monkeypatch, {"tail_handoffs": []})
    edited = second.model_copy(update={"feedback": "Keep my latest direction."})

    @contextmanager
    def concurrent_edit(project_id):
        # Feedback is not covered by the run fingerprint. Simulate an edit
        # after inference validation but before the publication lock is held.
        save_shot(edited)
        with store._project_lock(project_id):
            yield

    monkeypatch.setattr(managed_api, "_project_lock", concurrent_edit)
    with pytest.raises(HTTPException) as error:
        await plan_managed_run(project.id)
    assert error.value.status_code == 409
    assert load_shot(project.id, second.id) == edited
    assert store.list_runs(project.id) == []
