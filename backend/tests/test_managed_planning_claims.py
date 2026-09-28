"""Planning claims need valid evidence at every stage and semantic adjudication."""
import json

import pytest
from fastapi import HTTPException

from app.api import projects as projects_api
from app.api.managed_runs import plan_managed_run
from app.core.managed_runs.store import list_runs
from app.core.projects.models import Shot
from app.core.projects.store import create_project, load_shot, save_project, save_shot


@pytest.fixture
def documentary():
    project = create_project("A day in the market", "Ari and Sam explore the market together. Ari films and explains while Sam interviews traders.")
    shot = Shot(id="sht_interview", project_id=project.id, scene_id="market",
        title="Interview", script_beat="Sam asks the trader a question; Ari records and comments from behind the camera.",
        composition="Sam and the trader face the lens.", camera_motion="Handheld by Ari.",
        dialogue=["How long have you worked here?", "Twenty years.", "This market predates the station."], duration_s=15)
    save_shot(shot)
    project = project.model_copy(update={"shot_ids": [shot.id]})
    save_project(project)
    return project, shot


def claim(project, shot, **changes):
    return {"requirement_quote": project.script_text, "shot_id": shot.id, "field": "composition",
        "shot_quote": shot.composition, "reason": "Ari must appear in this composition because both travelers explore the market.", **changes}


def infer_sequence(monkeypatch, responses):
    calls = []
    async def make(**kwargs):
        async def infer(system, user, **kwargs):
            calls.append((system, user))
            assert len(calls) <= len(responses), "Planning recovery exceeded its bounded calls"
            return {"content": json.dumps(responses[len(calls) - 1])}
        return infer
    monkeypatch.setattr(projects_api, "_make_chat_fn", make)
    return calls


def proposal(issues=(), refinements=()):
    return {"plan": {"storyboard_issues": list(issues), "tail_handoffs": []},
        "camera_refinements": list(refinements), "reason": "Assess the requirement against the whole film and camera ownership."}


@pytest.mark.asyncio
async def test_discarded_initial_claim_is_removed_from_every_repair_input(documentary, monkeypatch):
    project, shot = documentary
    valid_quote = claim(project, shot)
    unsupported = claim(project, shot, field="dialogue", shot_quote="Sam must speak in every shot.", reason="Unsupported dialogue requirement")
    calls = infer_sequence(monkeypatch, [
        {"storyboard_issues": [valid_quote, unsupported]}, proposal(), {"valid": True, "issues": []}])
    result = await plan_managed_run(project.id)
    assert result.state == "draft"
    request = json.loads(calls[1][1])
    assert request["original_claims"] == [valid_quote]
    assert request["previous_plan"]["storyboard_issues"] == [valid_quote]
    assert "Unsupported dialogue requirement" not in calls[2][1]
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_repair_cannot_resurrect_a_claim_without_field_evidence(documentary, monkeypatch):
    project, shot = documentary
    unsupported = claim(project, shot, field="dialogue", shot_quote="Words never saved", reason="Invented line")
    calls = infer_sequence(monkeypatch, [
        {"storyboard_issues": [claim(project, shot)]}, proposal([unsupported]), {"valid": True, "issues": []}])
    result = await plan_managed_run(project.id)
    assert result.state == "draft"
    assert len(calls) == 3
    assert json.loads(calls[2][1])["candidate_plan"]["storyboard_issues"] == []
    assert "Invented line" not in calls[2][1]


@pytest.mark.asyncio
async def test_residual_claim_reaches_independent_review_before_blocking(documentary, monkeypatch):
    project, shot = documentary
    original = claim(project, shot)
    calls = infer_sequence(monkeypatch, [
        {"storyboard_issues": [original]}, proposal([original]), {"valid": True, "issues": []}])
    result = await plan_managed_run(project.id)
    assert result.state == "draft"
    assert len(calls) == 3
    assert result.recovery_history[0]["review"]["valid"] is True
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_final_review_must_ground_its_blocking_claim(documentary, monkeypatch):
    project, shot = documentary
    unsupported = claim(project, shot, requirement_quote="Sam must talk in every scene.", reason="Unsupported requirement")
    infer_sequence(monkeypatch, [{"storyboard_issues": [claim(project, shot)]}, proposal(),
        {"valid": False, "issues": [unsupported]}])
    with pytest.raises(HTTPException) as error:
        await plan_managed_run(project.id)
    assert "review evidence" in str(error.value.detail).lower()
    assert "Planning needs your decision" not in str(error.value.detail)
    assert "Unsupported requirement" not in str(error.value.detail)
    assert list_runs(project.id) == []


@pytest.mark.asyncio
async def test_only_review_confirmed_conflict_is_shown_to_user(documentary, monkeypatch):
    project, shot = documentary
    requirement = "This shot must show Ari speaking directly to the lens."
    project = project.model_copy(update={"script_text": project.script_text + " " + requirement})
    save_project(project)
    confirmed = claim(project, shot, requirement_quote=requirement,
        reason="The specifically requested on-camera address is absent from this shot.")
    infer_sequence(monkeypatch, [{"storyboard_issues": [claim(project, shot)]}, proposal([confirmed]),
        {"valid": False, "issues": [confirmed]}])
    with pytest.raises(HTTPException) as error:
        await plan_managed_run(project.id)
    detail = str(error.value.detail)
    assert "Planning needs your decision" in detail
    assert confirmed["reason"] in detail
    assert "because both travelers" not in detail
    assert load_shot(project.id, shot.id) == shot
    assert list_runs(project.id) == []


@pytest.mark.asyncio
async def test_review_quote_must_match_candidate_after_camera_refinement(documentary, monkeypatch):
    project, shot = documentary
    original = claim(project, shot)
    refinement = {"shot_id": shot.id, "changes": {"composition": "All three are visible."},
                  "reason": "A candidate composition requiring separate review."}
    infer_sequence(monkeypatch, [{"storyboard_issues": [original]}, proposal(refinements=[refinement]),
        {"valid": False, "issues": [original]}])
    with pytest.raises(HTTPException) as error:
        await plan_managed_run(project.id)
    assert "review evidence" in str(error.value.detail).lower()
    assert load_shot(project.id, shot.id) == shot
