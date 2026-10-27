"""Generic synthetic fixtures; no user projects, conversations, images or endpoints."""
import io
import json
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from app.main import create_app
from app.core.projects.store import create_project, load_shot, save_shot, save_project
from app.core.library.store import create_external_asset, write_asset
from app.core.projects.models import ShotRef
from app.core.projects.dialogue import verify_dialogue_sources
from app.shot_workspace.router import add_shot, CreateShot, save_brief, Brief, replace_materials, Materials, Material
from app.shot_workspace.references import snapshot
from app.shot_workspace.writer import write_prompt, prompt_check


@pytest.fixture
def image_bytes():
    stream = io.BytesIO()
    Image.effect_noise((96, 96), 30).convert("RGB").save(stream, format="PNG")
    return stream.getvalue()


@pytest_asyncio.fixture
async def board(image_bytes):
    project = create_project("Workspace test", "", authoring_mode="shot_workspace")
    shot = await add_shot(project.id, CreateShot(request_id="create-1"))
    asset = create_external_asset(kind="props", name="Reference object", notes="A blue reference object with a small round handle.",
        project_id=project.id, image_bytes=image_bytes, image_filename="reference.png", file_key="master")
    shot.refs = [ShotRef(role="prop", asset_id=asset.id, file_key="master", picture_index=1)]
    shot.script_beat = "An object turns slowly on a plain table. No dialogue."
    save_shot(shot)
    return project, shot, asset


class Model:
    def __init__(self, change=None, dialogue=False):
        self.calls = []
        self.change = change
        self.dialogue = dialogue

    async def complete(self, system, payload, **kwargs):
        self.calls.append((system, payload, kwargs))
        assert not kwargs.get("images"), "Shot writing must not invoke vision"
        assert "history" not in payload and "library_inventory" not in payload
        detail = "The object turns slowly on the table."
        if self.dialogue:
            detail = "The guide speaks {{speech:line_1}}. The visitor replies {{speech:line_2}}."
        sections = {"subject_definitions": "Reference object <Picture 1>.", "summary": "A short static shot.",
            "retention_analysis": "Retain the object appearance from <Picture 1>.", "detailed_description": detail,
            "overall_soundscape": "Quiet room tone.", "non_diegetic_music": "None. No background music."}
        if self.change:
            self.change()
        return json.dumps({"prompt_sections": sections})


@pytest.mark.asyncio
async def test_manual_editing_and_reference_notes_use_no_model(board):
    project, shot, asset = board
    body = Brief(description="The object remains still.", title="Object view", duration_s=5, expected_version=snapshot(shot)["version"])
    saved = await save_brief(shot.id, body)
    assert saved.script_beat == body.description and saved.refs == shot.refs
    result = await replace_materials(shot.id, Materials(materials=[Material(role="prop", asset_id=asset.id, file_key="master", notes="Keep the handle visible")], expected_refs=saved.refs))
    assert result.refs[0].notes == "Keep the handle visible"
    with pytest.raises(Exception, match="References changed"):
        await replace_materials(shot.id, Materials(materials=[], expected_refs=shot.refs))


@pytest.mark.asyncio
async def test_writer_uses_only_selected_descriptions_and_certifies_current_inputs(board):
    project, shot, asset = board
    model = Model()
    saved = await write_prompt(project.id, shot.id, model=model)
    assert len(model.calls) == 1
    assert model.calls[0][1]["references"][0]["description"] == asset.notes
    assert model.calls[0][1]["references"][0]["picture_tag"] == "<Picture 1>"
    assert model.calls[0][1]["required_picture_tags"] == ["<Picture 1>"]
    assert "content_sha256" not in model.calls[0][1]["references"][0]
    assert prompt_check(saved)["valid"]
    from app.agents.director.reference_facts import require_current_reference_contract
    from app.agents.director.dialogue_preflight import require_current_dialogue_contract
    require_current_reference_contract(project, saved)
    require_current_dialogue_contract(project, saved)
    asset.notes = "A revised description of the same object."
    write_asset(asset)
    assert prompt_check(saved)["stale"]


@pytest.mark.asyncio
async def test_stale_model_response_never_overwrites_a_new_brief(board):
    project, shot, _ = board
    def change():
        save_shot(shot.model_copy(update={"script_beat": "A newly edited action."}))
    with pytest.raises(ValueError, match="changed"):
        await write_prompt(project.id, shot.id, model=Model(change=change))
    current = load_shot(project.id, shot.id)
    assert current.script_beat == "A newly edited action."
    assert not current.prompt_sections.summary


@pytest.mark.asyncio
async def test_explicit_two_speaker_cues_compile_exact_words_without_attribution_inference(board):
    project, shot, _ = board
    saved = await save_brief(shot.id, Brief(description="A guide greets a visitor beside the reference object.", title="Greeting", duration_s=5,
        dialogue=["Guide [English]: Welcome.", "Visitor [English]: Thank you."], expected_version=snapshot(shot)["version"]))
    verify_dialogue_sources(project, saved, saved.dialogue_lines)
    assert [line.speaker_name for line in saved.dialogue_lines] == ["Guide", "Visitor"]
    saved = await write_prompt(project.id, shot.id, model=Model(dialogue=True))
    assert "<d>[English] Welcome.</d>" in saved.prompt_sections.detailed_description
    assert "<d>[English] Thank you.</d>" in saved.prompt_sections.detailed_description
    from app.agents.director.dialogue_preflight import require_current_dialogue_contract
    require_current_dialogue_contract(project, saved)


@pytest.mark.asyncio
async def test_workspace_routes_and_mode_switch_do_not_start_general_agent(board):
    project, shot, _ = board
    from app.api.projects import get_director_service
    app = create_app()
    app.dependency_overrides[get_director_service] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/projects/{project.id}/chat", json={"message": "Plan shots"})
        assert response.status_code == 409
        response = await client.patch(f"/api/projects/{project.id}", json={"authoring_mode": "director_agent", "expected_authoring_mode": "shot_workspace"})
        assert response.status_code == 200 and response.json()["shot_ids"] == [shot.id]
        response = await client.get(f"/api/shot-workspace/shots/{shot.id}/description")
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_single_image_description_cache_uses_name_and_existing_text(board, image_bytes, monkeypatch):
    from app.shot_workspace.library_description import describe_upload
    import app.core.vram as vram
    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self, **kwargs): yield
        async def ensure_llm_ready(self): pass
    monkeypatch.setattr(vram, "get_orchestrator", lambda: Orchestrator())
    class Vision:
        calls = []
        async def complete(self, system, payload, **kwargs):
            self.calls.append(payload)
            assert len(kwargs["images"]) == 1
            return json.dumps({"description": "A reference object with a clearly visible handle."})
    model = Vision()
    first = await describe_upload(image_bytes, "Object", existing_description="Check the handle", model=model)
    again = await describe_upload(image_bytes, "Object", existing_description="Check the handle", model=model)
    assert not first["cached"] and again["cached"] and len(model.calls) == 1
    await describe_upload(image_bytes, "Object", existing_description="Check the reverse side", model=model)
    assert len(model.calls) == 2
    assert model.calls[0]["existing_description"] == "Check the handle"

@pytest.mark.asyncio
async def test_layout_selection_matches_native_material_projection(board, image_bytes):
    project, shot, _ = board
    asset = create_external_asset(kind="layouts", name="Room layout", notes="A plain room with one table.", project_id=project.id,
        image_bytes=image_bytes, image_filename="layout.png", file_key="layout")
    saved = await replace_materials(shot.id, Materials(materials=[Material(role="layout_ref_frame", asset_id=asset.id, file_key="layout", notes="Keep table placement")], expected_refs=shot.refs))
    from app.core.projects.layouts import sync_selected_layout_refs
    assert sync_selected_layout_refs(saved).refs == saved.refs
    assert saved.layout_refs[0].selected_for_h3
    removed = await replace_materials(shot.id, Materials(materials=[], expected_refs=saved.refs))
    assert not sync_selected_layout_refs(removed).refs
    assert not removed.layout_refs[0].selected_for_h3


@pytest.mark.asyncio
async def test_task_receipt_refresh_and_cancellation(board, monkeypatch):
    import asyncio
    from app.shot_workspace import tasks
    from app.core import vram
    from app.shot_workspace import writer
    project, shot, _ = board
    entered = asyncio.Event()
    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self, **kwargs):
            yield
        async def ensure_llm_ready(self):
            pass
    async def pending(*args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(vram, "get_orchestrator", lambda: Orchestrator())
    monkeypatch.setattr(writer, "write_prompt", pending)
    receipt = await tasks.start(project.id, shot.id, "action-1")
    await asyncio.wait_for(entered.wait(), 1)
    assert tasks.status(project.id)["run_id"] == receipt["run_id"]
    assert (await tasks.start(project.id, shot.id, "action-1"))["run_id"] == receipt["run_id"]
    await tasks.cancel(project.id)
    await asyncio.sleep(0)
    assert tasks.status(project.id)["status"] == "cancelled"
    assert load_shot(project.id, shot.id).script_beat == shot.script_beat

@pytest.mark.asyncio
async def test_workspace_video_submission_keeps_native_preflight_without_agent(board, monkeypatch):
    from app.api import projects as api
    project, shot, _ = board
    await write_prompt(project.id, shot.id, model=Model())
    staged = []
    async def start(job, **kwargs):
        staged.append((job, kwargs))
    class NoAgent:
        async def write_prompts_after_layout(self, *args):
            raise AssertionError("Workspace submission must not dispatch the Agent")
    monkeypatch.setattr(api, "start_pipeline_job", start)
    result = await api.submit_shot_endpoint(shot.id, svc=NoAgent())
    assert result.h3_job_id and len(staged) == 1
    assert staged[0][0].params["reference_evidence"][0]["evidence_mode"] == "saved_library_description"

@pytest.mark.asyncio
async def test_rewrite_replaces_legacy_stale_signature(board):
    project, shot, _ = board
    shot.meta.update(prompt_brief_signature="older-version", prompt_brief_stale=True, material_review_pending=True)
    save_shot(shot)
    saved = await write_prompt(project.id, shot.id, model=Model())
    assert prompt_check(saved)["valid"]
    assert not prompt_check(saved)["stale"]


@pytest.mark.asyncio
async def test_dialogue_format_gets_one_bounded_repair(board):
    project, shot, _ = board
    shot = await save_brief(shot.id, Brief(description="The guide greets the visitor.", duration_s=5, expected_version=snapshot(shot)["version"],
        dialogue=["Guide [English]: Welcome.", "Visitor [English]: Thank you."]))
    class RepairModel(Model):
        async def complete(self, system, payload, **kwargs):
            self.dialogue = bool(self.calls)
            return await super().complete(system, payload, **kwargs)
    model = RepairModel()
    saved = await write_prompt(project.id, shot.id, model=model)
    assert len(model.calls) == 2 and "repair" in model.calls[1][1]
    assert prompt_check(saved)["valid"]

@pytest.mark.asyncio
@pytest.mark.parametrize("alter_words", [False, True])
async def test_exact_final_speech_format_uses_saved_sources_only(board, alter_words):
    project, shot, _ = board
    await save_brief(shot.id, Brief(description="A guide greets a visitor.", duration_s=5, expected_version=snapshot(shot)["version"],
        dialogue=["Guide [English]: Welcome.", "Visitor [English]: Thank you."]))
    class LiteralModel(Model):
        async def complete(self, *args, **kwargs):
            self.dialogue = True
            result = await super().complete(*args, **kwargs)
            return result.replace("{{speech:line_1}}", "<d>[English] " + ("Changed words." if alter_words else "Welcome.") + "</d>").replace("{{speech:line_2}}", "<d>[English] Thank you.</d>")
    model = LiteralModel()
    if alter_words:
        with pytest.raises(ValueError, match="No new prompt was saved"):
            await write_prompt(project.id, shot.id, model=model)
        assert len(model.calls) == 2
        assert not load_shot(project.id, shot.id).meta.get("workspace_input_version")
    else:
        saved = await write_prompt(project.id, shot.id, model=model)
        assert len(model.calls) == 1 and prompt_check(saved)["valid"]

@pytest.mark.asyncio
async def test_continuation_readiness_does_not_invalidate_authored_prompt(board, monkeypatch):
    from app.shot_workspace import references
    from app.core.projects.models import ShotVideoContext
    _, shot, _ = board
    shot.video_context = ShotVideoContext(mode="previous_shot", source_shot_id="sht_source")
    monkeypatch.setattr(references, "video_context_status", lambda shot: {"ready": False, "state": "pending"})
    first = snapshot(shot)
    monkeypatch.setattr(references, "video_context_status", lambda shot: {"ready": True, "state": "ready"})
    second = snapshot(shot)
    assert first["continuation"] != second["continuation"]
    assert first["version"] == second["version"]

@pytest.mark.asyncio
async def test_switching_continuation_variation_keeps_workspace_prompt_current(board):
    from app.core.projects.models import ShotVideoContext
    from app.core.projects.video_context import video_context_prompt_is_stale
    project, shot, _ = board
    shot.video_context = ShotVideoContext(mode="previous_shot", source_shot_id="sht_source", source_job_id="job_first", source_output_key="video")
    save_shot(shot)
    saved = await write_prompt(project.id, shot.id, model=Model())
    saved.video_context = saved.video_context.model_copy(update={"source_job_id": "job_other_variation", "source_output_key": "video_raw"})
    save_shot(saved)
    assert prompt_check(saved)["valid"]
    assert not video_context_prompt_is_stale(saved)
    saved.video_context = saved.video_context.model_copy(update={"source_shot_id": "sht_different_story"})
    save_shot(saved)
    assert prompt_check(saved)["stale"] and video_context_prompt_is_stale(saved)


@pytest.mark.asyncio
async def test_native_director_retains_video_source_freshness_policy(board):
    from app.core.projects.models import ShotVideoContext
    from app.core.projects.video_context import video_context_prompt_is_stale, video_context_prompt_signature
    project, shot, _ = board
    project.authoring_mode = "director_agent"
    save_project(project)
    shot.video_context = ShotVideoContext(mode="previous_shot", source_shot_id="sht_source", source_job_id="job_first")
    shot.meta["prompt_video_context_signature"] = video_context_prompt_signature(shot)
    assert not video_context_prompt_is_stale(shot)
    shot.video_context.source_job_id = "job_other_variation"
    assert video_context_prompt_is_stale(shot)


@pytest.mark.asyncio
async def test_standalone_shot_is_rejected_before_starting_context_only_workflow(board, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.api import projects as api
    from app.workflow_profiles.h3 import store
    project, shot, _ = board
    await write_prompt(project.id, shot.id, model=Model())
    monkeypatch.setattr(store.H3ProfileStore, "resolve_active", lambda self: SimpleNamespace(mapping=SimpleNamespace(context_video=object())))
    from app.config import settings
    monkeypatch.setattr(settings, "h3_standalone_profile_id", "")
    async def must_not_start(*args, **kwargs):
        raise AssertionError("No GPU generation should start for a missing continuation source")
    monkeypatch.setattr(api, "start_pipeline_job", must_not_start)
    with pytest.raises(HTTPException) as error:
        await api.submit_shot_endpoint(shot.id, svc=object())
    assert error.value.status_code == 400
    assert "requires a context video" in error.value.detail
    assert "standalone H3 workflow" in error.value.detail
    assert not load_shot(project.id, shot.id).h3_job_id

@pytest.mark.asyncio
@pytest.mark.parametrize("changed_description", [False, True])
async def test_legacy_prompt_adoption_requires_unchanged_certified_evidence(board, changed_description):
    from app.core.projects.dialogue import digest
    from app.shot_workspace.references import selected_references
    from app.shot_workspace.writer import adopt_verified_legacy_prompt
    from app.core.projects.video_context import video_context_prompt_is_stale
    project, shot, asset = board
    shot = await write_prompt(project.id, shot.id, model=Model())
    records = [{**ref, "asset_name": ref["name"], "reference_notes": ref["binding_notes"]} for ref in selected_references(shot)]
    review = {"facts_signature": "fixture-facts", "intent_signature": "fixture-intent", "references": records}
    shot.meta["material_review"] = review
    shot.meta["prompt_reference_contract"] = {"version": 2, "signature": digest([review["facts_signature"], review["intent_signature"], records, shot.prompt_sections.model_dump(mode="json")])}
    for key in ["workspace_input_version", "workspace_continuation_intent_signature", "prompt_brief_signature"]:
        shot.meta.pop(key, None)
    save_shot(shot)
    if changed_description:
        asset.notes = "A different reference description."
        write_asset(asset)
    upgraded = adopt_verified_legacy_prompt(shot)
    assert bool(upgraded.meta.get("workspace_input_version")) == (not changed_description)
    assert upgraded.prompt_sections == shot.prompt_sections
    if not changed_description:
        assert not video_context_prompt_is_stale(upgraded)
        assert prompt_check(upgraded)["valid"]


@pytest.mark.asyncio
@pytest.mark.parametrize("certificate_current", [True, False])
async def test_legacy_empty_language_reuses_only_matching_current_certificate(board, monkeypatch, certificate_current):
    from app.shot_workspace.dialogue import parse_cues
    from app.agents.director import dialogue_preflight
    _, shot, _ = board
    shot.meta["prompt_dialogue_contract"] = {"lines": [{"speaker_name": "Guide", "language": "English", "text": "Welcome."}]}
    monkeypatch.setattr(dialogue_preflight, "dialogue_contract_current", lambda *args: certificate_current)
    if certificate_current:
        lines = parse_cues(["Guide []: Welcome."], shot.scene_id, shot=shot)
        assert lines[0].language == "English" and lines[0].text == "Welcome."
    else:
        with pytest.raises(ValueError, match="Dialogue line 1 needs"):
            parse_cues(["Guide []: Welcome."], shot.scene_id, shot=shot)
    with pytest.raises(ValueError):
        parse_cues(["Visitor []: Different words."], shot.scene_id, shot=shot)

@pytest.mark.asyncio
async def test_amendment_rewrites_one_draft_without_saving_or_loading_history(board):
    from app.shot_workspace.refinement import refine
    project, shot, _ = board
    class Refiner:
        async def complete_shot_description(self, system, user, **kwargs):
            payload = json.loads(user)
            assert payload["amendment"] == "Replace rotation with a stationary view."
            assert payload["user_description"] == shot.script_beat
            assert "AMENDMENT MODE" in system and "history" not in payload
            return json.dumps(dict(description="The object remains stationary on the table.", title=shot.title, duration_s=shot.duration_s,
                shot_type="close-up", camera_angle="eye level", camera_motion="static", composition="centered", dialogue=[], questions=[]))
    result = await refine(shot, shot.script_beat, snapshot(shot)["version"], Refiner(), amendment="Replace rotation with a stationary view.")
    assert "stationary" in result["description"]
    assert load_shot(project.id, shot.id).script_beat == shot.script_beat
    assert load_shot(project.id, shot.id).refs == shot.refs


@pytest.mark.asyncio
async def test_amendment_allows_only_explicit_new_dialogue(board):
    from app.shot_workspace.refinement import refine
    _, shot, _ = board
    class Refiner:
        async def complete_shot_description(self, *args, **kwargs):
            return json.dumps(dict(description="The guide says Ready.", title=shot.title, duration_s=shot.duration_s,
                shot_type="medium", camera_angle="eye level", camera_motion="static", composition="centered",
                dialogue=["Guide [English]: Ready."], questions=[]))
    result = await refine(shot, shot.script_beat, snapshot(shot)["version"], Refiner(), amendment="Add Guide [English]: Ready.")
    assert result["dialogue"] == ["Guide [English]: Ready."]
    with pytest.raises(ValueError, match="invented dialogue"):
        await refine(shot, shot.script_beat, snapshot(shot)["version"], Refiner(), amendment="Keep a silent shot.")
