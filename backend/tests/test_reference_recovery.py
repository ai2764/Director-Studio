"""Upstream material failures must not turn into creative prompt retries."""
import json

import pytest

from test_director_material_review import material_shot, Orchestrator, Provider
from test_reference_facts import Vision, observation
from app.agents.director.material_review import observe_reference, capture_references
from app.agents.director.service import DirectorService
from app.agents.director.prompt_retry import pending_prompt_retry
from app.core.projects.store import create_project, save_project, save_shot, load_shot, project_dir
from app.core.library.store import load_asset, assign_asset_project


def cited_fact(attribute, quote):
    return dict(attribute=attribute, value="visible", visibility="observed", evidence="Visible pixels",
                source_id="approved_notes", source_quote=quote)


@pytest.mark.asyncio
async def test_multiple_bad_quotes_report_each_location_and_repair_progress():
    first = observation()
    first["facts"] = [cited_fact("wardrobe", "bad coat"), cited_fact("prop", "bad object")]
    second = observation()
    second["facts"] = [cited_fact("wardrobe", "blue coat"), cited_fact("prop", "bad object")]
    third = observation()
    third["facts"] = [cited_fact("wardrobe", "blue coat"), cited_fact("prop", "brass watch")]
    provider = Vision(first, second, third)
    result = await observe_reference(provider, {"sources": [dict(id="approved_notes",
        kind="library_metadata", text="blue coat and brass watch")]}, "image")
    assert result["facts"][1]["source_quote"] == "brass watch"
    correction = provider.calls[1][1]
    assert "facts[0]" in correction and "facts[1]" in correction
    assert "bad coat" in correction and "bad object" in correction
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_repeated_invalid_citation_is_bounded_and_failed_evidence_not_published(material_shot):
    from app.agents.director.material_review import observe_references_cached
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    bad = observation()
    bad["facts"] = [cited_fact("wardrobe", "invented")]
    provider = Vision(bad, bad)
    with pytest.raises(ValueError) as error:
        await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert getattr(error.value, "code", None) == "MATERIAL_REVIEW_INVALID"
    assert len(provider.calls) == 2
    assert not (project_dir(project.id) / "agent/reference_facts.json").exists()
    diagnostics = list((project_dir(project.id) / "agent/reference_review_failures").glob("*.json"))
    assert len(diagnostics) == 1
    saved = json.loads(diagnostics[0].read_text(encoding="utf-8"))
    assert saved["reference"]["asset_id"] == shot.refs[0].asset_id
    assert len(saved["attempts"]) == 2
    assert saved["attempts"][0]["issues"][0]["path"] == "facts[0].source_quote"
    assert "invented" in saved["attempts"][0]["raw"]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["asset", "file", "decode"])
async def test_missing_or_unreadable_material_blocks_prompt_retry_and_names_binding(material_shot, defect):
    project, shot, _, files = material_shot
    if defect == "asset":
        (files[0].parent / "asset.json").unlink()
    elif defect == "file":
        files[0].unlink()
    else:
        files[0].write_bytes(b"not an image" * 1000)
    orch = Orchestrator()
    provider = Provider(orch)
    with pytest.raises(ValueError) as error:
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert getattr(error.value, "code", None) == "MATERIAL_INPUT_INVALID"
    assert error.value.issues[0]["asset_id"] == shot.refs[0].asset_id
    assert error.value.issues[0]["picture_index"] == 1
    assert error.value.issues[0]["reason"] == {"asset": "asset_missing", "file": "image_missing", "decode": "image_unreadable"}[defect]
    assert pending_prompt_retry(project.id) is None
    assert provider.visual == [] and provider.text == []
    assert load_shot(project.id, shot.id).refs == shot.refs


@pytest.mark.asyncio
async def test_delete_marks_all_linked_projects_without_replacing_or_removing_refs(material_shot):
    from app.api.library import delete_library_asset
    project, shot, _, _ = material_shot
    asset_id = shot.refs[0].asset_id
    assign_asset_project("props", asset_id, project.id)
    other = create_project("Other film", "Another scene.")
    other_shot = shot.model_copy(update={"id": "sht_other", "project_id": other.id})
    save_shot(other_shot)
    save_project(other.model_copy(update={"shot_ids": [other_shot.id]}))
    untouched = shot.model_copy(update={"id": "sht_unrelated", "refs": shot.refs[1:]})
    save_shot(untouched)
    save_project(project.model_copy(update={"shot_ids": [shot.id, untouched.id]}))
    result = await delete_library_asset("props", asset_id)
    assert result["ok"] and load_asset("props", asset_id) is None
    assert {item["shot_id"] for item in result["affected_shots"]} == {shot.id, other_shot.id}
    for original in (shot, other_shot):
        saved = load_shot(original.project_id, original.id)
        assert saved.refs == original.refs
        assert saved.prompt_sections == original.prompt_sections
        assert saved.meta["material_review_pending"] is True
        assert saved.meta["material_changes"]["deleted_assets"][0]["asset_id"] == asset_id
    assert load_shot(project.id, untouched.id) == untouched


@pytest.mark.asyncio
async def test_harness_preserves_material_failure_and_next_turn_can_relink(material_shot):
    from app.agents.director.harness_runtime import BackendTurn
    project, shot, _, files = material_shot
    files[0].unlink()
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    turn = BackendTurn(project.id, "Write the prompt", svc, None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"name": "write_prompt",
        "arguments": {"shot_id": shot.id}, "call_id": "missing-material"})
    assert result["code"] == "MATERIAL_INPUT_INVALID"
    assert result["issues"][0]["picture_index"] == 1
    finished = turn.finish({"reply": "Done", "thinking": ""})
    assert finished.failure_code == "MATERIAL_INPUT_INVALID"
    assert "bounded internal repair" not in finished.reply
    fresh = BackendTurn(project.id, "Relink my references", svc, None)
    assert "patch_shot_refs" in {t["function"]["name"] for t in fresh.context()["tools"]}


@pytest.mark.asyncio
async def test_stale_pending_retry_is_blocked_when_material_preflight_fails(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    project, shot, _, files = material_shot
    files[0].unlink()
    receipt = record_prompt_failure(shot, "", ValueError("Old prompt failure"))
    orch = Orchestrator()
    with pytest.raises(ValueError) as error:
        await run_prompt_retry(project.id, receipt, DirectorService(plan_provider=Provider(orch), orchestrator=orch))
    assert getattr(error.value, "code", None) == "MATERIAL_INPUT_INVALID"
    assert pending_prompt_retry(project.id) is None


@pytest.mark.asyncio
async def test_scoped_retry_api_keeps_upstream_failure_code(material_shot):
    from app.api.projects import _run_scoped_prompt_retry, _chat_result_to_response
    from app.agents.director.prompt_retry import record_prompt_failure, PromptRetryRequest
    project, shot, _, files = material_shot
    files[0].unlink()
    receipt = record_prompt_failure(shot, "", ValueError("Old prompt failure"))
    orch = Orchestrator()
    result = await _run_scoped_prompt_retry(project.id, PromptRetryRequest(**receipt),
        DirectorService(plan_provider=Provider(orch), orchestrator=orch))
    assert result.failure_code == "MATERIAL_INPUT_INVALID"
    assert "Prompt repair did not complete" not in result.reply
    assert _chat_result_to_response(result).prompt_retry is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reader,reason", [("resolve_asset_image", "image_unavailable"), ("load_asset", "asset_unavailable")])
async def test_material_file_read_error_is_not_a_prompt_failure(material_shot, monkeypatch, reader, reason):
    project, shot, _, _ = material_shot
    def unreadable(*args, **kwargs):
        raise PermissionError("reference file is temporarily inaccessible")
    monkeypatch.setattr(f"app.agents.director.material_review.{reader}", unreadable)
    orch = Orchestrator()
    with pytest.raises(ValueError) as error:
        await DirectorService(plan_provider=Provider(orch), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert getattr(error.value, "code", None) == "MATERIAL_INPUT_INVALID"
    assert error.value.issues[0]["picture_index"] == 1
    assert error.value.issues[0]["reason"] == reason
    assert pending_prompt_retry(project.id) is None


@pytest.mark.asyncio
async def test_managed_material_failure_pauses_without_spending_prompt_retry(monkeypatch):
    from test_managed_run_continuation import _run
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import load_run
    from app.agents.director.chat_orchestrator import ChatResult
    run = _run()
    async def material_failure(current, svc):
        assert current.prompt_retry_count == 0
        return ChatResult(reply="Restore Picture 1", failure_code="MATERIAL_INPUT_INVALID",
                          failure_message="Restore Picture 1", failure_kind="contract")
    monkeypatch.setattr(continuation, "_agent_turn", material_failure)
    await continuation.continue_run(run.project_id, run.run_id)
    saved = load_run(run.project_id, run.run_id)
    assert saved.state == "paused"
    assert saved.paused_reason == "Restore Picture 1"
    assert saved.prompt_retry_count == 0


@pytest.mark.asyncio
async def test_automatic_tail_material_failure_does_not_mark_ready_or_create_retry(monkeypatch):
    from test_managed_run_continuation import _run
    from app.core.managed_runs import continuation
    from app.core.managed_runs.models import RunStep
    from app.core.managed_runs.store import _save_run, bind_job, record_terminal, load_run
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.schemas import JobStatus
    from app.core.prompt_errors import MaterialInputError
    run = _run()
    run = _save_run(run.model_copy(update={"steps": [RunStep(shot_id="sht_1"),
        RunStep(shot_id="sht_2", tail_from_shot_id="sht_1", tail_reason="Continue the action")]}))
    bind_job(run.project_id, run.run_id, "sht_1", "job_first")
    run = record_terminal(run.project_id, "job_first", JobStatus.succeeded)
    def extract(**kwargs):
        target = load_shot(run.project_id, "sht_2")
        layout = LayoutReference(id="lref_tail", asset_id="lay_missing", purpose="continuity",
                                 review_status=LayoutReviewStatus.pending_review)
        save_shot(target.model_copy(update={"layout_refs": [layout]}))
        return {"layout_ref_id": layout.id, "layout_asset_id": layout.asset_id}
    monkeypatch.setattr(continuation, "extract_clip_tail_frame", extract)
    class MissingMaterialService:
        async def write_prompts_after_layout(self, shot_id):
            raise MaterialInputError("Missing Picture 1")
    with pytest.raises(MaterialInputError):
        await continuation.prepare_planned_tail(run, MissingMaterialService())
    saved = load_run(run.project_id, run.run_id)
    assert saved.prompt_retry_count == 0
    assert saved.prepared_tail_layout_ids == {}


@pytest.mark.asyncio
async def test_successful_ref_repair_clears_missing_material_marker(material_shot):
    from app.api.library import delete_library_asset
    from app.core.projects.models import ShotRef
    project, shot, _, _ = material_shot
    await delete_library_asset("props", shot.refs[0].asset_id)
    changed = load_shot(project.id, shot.id)
    # Explicit user relinking is still allowed; the system never picks a fallback.
    new_ref = ShotRef(**{**shot.refs[1].model_dump(), "picture_index": 1})
    changed = changed.model_copy(update={"refs": [new_ref, *changed.refs[1:]]})
    save_shot(changed)
    orch = Orchestrator()
    result = await DirectorService(plan_provider=Provider(orch), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert result.refs[0].asset_id == shot.refs[1].asset_id
    assert result.meta["material_review_pending"] is False
    assert "material_changes" not in result.meta
