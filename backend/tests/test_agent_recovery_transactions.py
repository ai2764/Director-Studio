import json
import pytest

from app.agents.director.chat import _run_tools
from app.agents.director.service import DirectorService, _script_hash
from app.agents.director.planner import ShotDraft
from app.core.projects.chat_history import append_chat_message
from app.core.projects.store import load_project, list_shots, save_project, save_shot
from test_storyboard_replacement_confirmation import _existing_project, _replacement_submission
from test_director_agent import FakePlanProvider, RecordingOrchestrator
from test_director_material_review import material_shot, Orchestrator, Provider


def service():
    return DirectorService(plan_provider=FakePlanProvider(responses=[json.dumps({"valid": True, "issues": []})] * 4),
                           orchestrator=RecordingOrchestrator())


async def call(project, svc, name, args, message):
    payloads = []
    notes, _ = await _run_tools(project_id=project.id, tools=[{"name": name, "args": args}],
        svc=svc, actions=[], user_feedback=message, result_payloads=payloads)
    return notes, payloads


@pytest.mark.asyncio
async def test_same_confirmation_words_in_a_new_message_can_execute_once(tmp_projects_dir):
    project, old = _existing_project()
    svc = service()
    text = "确认清除并重写全部 shots"
    append_chat_message(project.id, role="user", content=text)
    _, proposed = await call(project, svc, "save_storyboard", _replacement_submission(project), text)
    proposal_id = proposed[-1]["proposal_id"]
    # A call made in the original message cannot confirm its own proposal.
    _, rejected = await call(project, svc, "confirm_storyboard_replacement", {"proposal_id": proposal_id}, text)
    assert rejected[-1]["ok"] is False
    assert list_shots(project.id) == [old]
    append_chat_message(project.id, role="user", content=text)
    _, confirmed = await call(project, svc, "confirm_storyboard_replacement", {"proposal_id": proposal_id}, text)
    assert confirmed[-1].get("ok", True) is True
    saved = list_shots(project.id)
    assert saved[0].title == "Replacement shot"
    _, replay = await call(project, svc, "confirm_storyboard_replacement", {"proposal_id": proposal_id}, text)
    assert replay[-1].get("already_applied") is True
    assert list_shots(project.id) == saved


@pytest.mark.asyncio
async def test_invalid_voice_rejected_before_confirmation_proposal(tmp_projects_dir):
    from app.agents.director.tool_handlers.project import _load_storyboard_replacement
    project, old = _existing_project()
    draft = _replacement_submission(project)
    draft["shots"][0]["voice_matches"] = [{"asset_id": "act_image_not_voice", "audio_index": 1}]
    _, payloads = await call(project, service(), "save_storyboard", draft, "Replace the storyboard")
    assert payloads[-1]["ok"] is False
    assert _load_storyboard_replacement(project.id) is None
    assert list_shots(project.id) == [old]


@pytest.mark.asyncio
async def test_omitted_existing_dialogue_is_preserved_and_deletion_is_in_diff(tmp_projects_dir):
    project, old = _existing_project()
    old = old.model_copy(update={"dialogue": ["Stay here."]})
    save_shot(old)
    draft = _replacement_submission(project)
    draft["shots"][0]["shot_id"] = old.id
    draft["shots"][0].pop("dialogue")
    svc = service()
    saved = await svc.save_storyboard(project.id, [ShotDraft.model_validate(draft["shots"][0])], _script_hash(project.script_text))
    assert saved[0].dialogue == ["Stay here."]
    draft["shots"][0]["dialogue"] = []
    _, payloads = await call(project, svc, "save_storyboard", draft, "Remove its dialogue")
    assert payloads[-1]["changes"]["removed_dialogue"] == [{"shot_id": old.id, "lines": ["Stay here."]}]


@pytest.mark.asyncio
async def test_preview_does_not_publish_and_concurrent_edit_survives(tmp_projects_dir):
    project, old = _existing_project()
    svc = service()
    draft = [ShotDraft.model_validate(x) for x in _replacement_submission(project)["shots"]]
    before = load_project(project.id)
    await svc.preview_storyboard(project.id, draft, _script_hash(project.script_text))
    assert list_shots(project.id) == [old]
    assert load_project(project.id) == before
    class EditingProvider(FakePlanProvider):
        async def complete(self, *args, **kwargs):
            save_shot(old.model_copy(update={"title": "User edited during validation"}))
            return json.dumps({"valid": True, "issues": []})
    svc.plan_provider = EditingProvider()
    with pytest.raises(ValueError, match="changed"):
        await svc.save_storyboard(project.id, draft, _script_hash(project.script_text))
    assert list_shots(project.id)[0].title == "User edited during validation"


@pytest.mark.asyncio
async def test_reference_purpose_survives_patch_and_reaches_writer(material_shot):
    from app.agents.director.planner import ShotRefsPatch
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    refs = [ref.model_dump(mode="json", exclude={"notes"}) for ref in shot.refs]
    refs[0]["notes"] = "Surface material only; ignore composition."
    patch = lambda: [ShotRefsPatch(shot_id=shot.id, refs=refs)]
    saved = svc.patch_shot_refs(project.id, patch())[0]
    assert saved.refs[0].notes == refs[0]["notes"]
    await svc.write_prompts_after_layout(shot.id)
    assert refs[0]["notes"] in provider.text[-1][1]
    refs[0].pop("notes")
    saved = svc.patch_shot_refs(project.id, patch())[0]
    assert saved.refs[0].notes == "Surface material only; ignore composition."
    assert saved.meta["material_review_pending"] is False
    refs[0]["notes"] = ""
    saved = svc.patch_shot_refs(project.id, patch())[0]
    assert saved.refs[0].notes == ""
    assert saved.meta["material_review_pending"] is True


@pytest.mark.asyncio
async def test_storyboard_casting_persists_reference_purpose(material_shot):
    project, shot, _, _ = material_shot
    data = _replacement_submission(project)["shots"][0]
    data["asset_matches"] = [{**shot.refs[0].model_dump(mode="json"), "notes": "Shape only."}]
    preview = await service().preview_storyboard(project.id, [ShotDraft.model_validate(data)], _script_hash(project.script_text))
    assert preview[0].refs[0].notes == "Shape only."


@pytest.mark.asyncio
async def test_cancelled_confirmation_can_be_retried_without_stuck_executing(tmp_projects_dir):
    import asyncio
    from app.agents.director.tool_handlers.project import _load_storyboard_replacement
    project, old = _existing_project()
    svc = service()
    _, proposed = await call(project, svc, "save_storyboard", _replacement_submission(project), "Replace")
    class CancelledProvider(FakePlanProvider):
        async def complete(self, *args, **kwargs):
            raise asyncio.CancelledError()
    svc.plan_provider = CancelledProvider()
    args = {"proposal_id": proposed[-1]["proposal_id"]}
    with pytest.raises(asyncio.CancelledError):
        await call(project, svc, "confirm_storyboard_replacement", args, "确认清除并重写全部 shots")
    assert _load_storyboard_replacement(project.id)["state"] == "failed"
    assert list_shots(project.id) == [old]
    _, result = await call(project, service(), "confirm_storyboard_replacement", args, "确认清除并重写全部 shots")
    assert result[-1].get("ok", True) is True


@pytest.mark.asyncio
async def test_latest_restored_directing_intent_reaches_validator(tmp_projects_dir):
    from app.agents.director.brief import remember_directing_request
    project, _ = _existing_project()
    for text in ("Use setting A.", "Use setting B.", "Use setting A."):
        remember_directing_request(project.id, text)
    svc = service()
    draft = [ShotDraft.model_validate(x) for x in _replacement_submission(project)["shots"]]
    await svc.preview_storyboard(project.id, draft, _script_hash(project.script_text))
    request = svc.plan_provider.calls[-1][1]
    assert "Use setting A.\n\nUse setting B.\n\nUse setting A." in request


@pytest.mark.asyncio
@pytest.mark.parametrize("collect_payloads", [True, False])
async def test_failed_prompt_cannot_expand_batch_into_script_rewrite(tmp_projects_dir, collect_payloads):
    project, old = _existing_project()
    class FailedPromptService:
        async def write_prompts_after_layout(self, *args, **kwargs):
            raise ValueError("Source attribution needs explicit author input")
    payloads = []
    await _run_tools(project_id=project.id, tools=[
        {"name": "write_prompt", "args": {"shot_id": old.id}},
        {"name": "set_script", "args": {"script": "Unauthorized workaround"}},
    ], svc=FailedPromptService(), actions=[], result_payloads=payloads if collect_payloads else None)
    assert load_project(project.id).script_text == project.script_text
    if collect_payloads:
        assert payloads[-1]["concludes_turn"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", [None, "New purpose."])
async def test_storyboard_update_preserves_omitted_ref_purpose_but_honors_explicit_change(material_shot, notes):
    from test_storyboard_replacement_confirmation import CAMERA_DRAFT
    project, shot, _, _ = material_shot
    shot = shot.model_copy(update=CAMERA_DRAFT)
    shot.refs[0].notes = "Original purpose."
    save_shot(shot)
    fields = ("scene_id", "title", "script_beat", "shot_type", "camera_angle", "camera_motion", "composition", "duration_s")
    data = {field: getattr(shot, field) for field in fields}
    data.update(shot_id=shot.id, asset_matches=[r.model_dump(mode="json", exclude={"notes"}) for r in shot.refs])
    if notes is not None:
        data["asset_matches"][0]["notes"] = notes
    else:
        data["title"] = "Revised title only"
    preview = await service().preview_storyboard(project.id, [ShotDraft.model_validate(data)], _script_hash(project.script_text))
    assert preview[0].refs[0].notes == (notes or "Original purpose.")


@pytest.mark.asyncio
async def test_missing_or_wrong_proposal_identity_cannot_confirm(tmp_projects_dir):
    project, old = _existing_project()
    svc = service()
    await call(project, svc, "save_storyboard", _replacement_submission(project), "Replace")
    for args in ({}, {"proposal_id": "old-proposal"}):
        _, result = await call(project, svc, "confirm_storyboard_replacement", args, "确认清除并重写全部 shots")
        assert result[-1]["ok"] is False
        assert list_shots(project.id) == [old]


def test_script_version_change_does_not_force_whole_board_replacement(tmp_projects_dir):
    from app.agents.director.chat_orchestrator import sanitize_tools_for_pipeline
    from app.agents.director.harness_runtime import BackendTurn
    project, old = _existing_project()
    calls = [{"name": "set_script", "args": {"script": "New speaker evidence"}},
             {"name": "write_prompt", "args": {"shot_id": old.id}}]
    safe, _ = sanitize_tools_for_pipeline(calls, project=project, shots=[old])
    assert safe == calls
    context = BackendTurn(project.id, "Update the existing shot prompt", service(), None).context()
    assert "write_prompt" in [tool["function"]["name"] for tool in context["tools"]]
    state = json.loads(context["state"])
    assert state["shots_stale_vs_script"] is True
    assert state["recommended_next_step"] == "review_existing_shots"


@pytest.mark.asyncio
async def test_legacy_planner_cannot_bypass_replacement_confirmation(tmp_projects_dir):
    project, old = _existing_project()
    class LegacyService:
        called = False
        async def plan_project(self, project_id):
            self.called = True
    svc = LegacyService()
    notes, _ = await call(project, svc, "plan_shots", {}, "Repair the current shot")
    assert not svc.called
    assert "save_storyboard" in notes[-1]
    assert list_shots(project.id) == [old]
