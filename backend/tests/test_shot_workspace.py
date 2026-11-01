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
async def test_operation_timing_never_overwrites_suggested_clip_duration(board):
    from app.shot_workspace.timings import timed, history
    _,shot,_=board
    @timed("refinement")
    async def operation(shot_id):
        return {"duration_s":8,"description":"A slow turn."}
    result=await operation(shot.id)
    assert result["duration_s"] == 8
    assert result["operation_duration_s"] == history(shot)[-1]["duration_s"]


@pytest.mark.parametrize("name,filename,expected", [
    ("Guide voice", "recording.wav", "Guide"),
    ("Recording", "Guide_sample.wav", "Guide"),
    ("GUIDE", "Guide.wav", "Guide"),
    ("Guide", "Visitor.wav", ""),
    ("Unknown voice", "sample.wav", ""),
    ("Guideline", "sample.wav", ""),
])
def test_automatic_voice_binding_uses_unique_asset_name_or_original_filename(name, filename, expected):
    from types import SimpleNamespace
    from app.shot_workspace.references import infer_voice_speaker
    shot = SimpleNamespace(refs=[],dialogue_lines=[SimpleNamespace(speaker_name="Guide"),SimpleNamespace(speaker_name="Visitor")])
    asset = SimpleNamespace(name=name,meta={"source_filename":filename})
    assert infer_voice_speaker(shot,asset) == expected


@pytest.mark.asyncio
async def test_assistant_prompt_route_does_not_write_or_replay_history(board):
    from app.shot_workspace.coordinator import converse
    project, shot, _ = board
    class RouterModel:
        async def complete(self, system, payload, **kwargs):
            assert set(payload) == {"request", "current_description"}
            return json.dumps({"intent":"prompt", "include_adjacent_descriptions":False,
                "reply_language":"English", "update_brief":False})
    result = await converse(shot, shot.script_beat, "Rewrite my prompt more concisely.",
        snapshot(shot)["version"], RouterModel(), turns=[{"role":"user", "text":"irrelevant"}])
    assert result["kind"] == "prompt" and result["prompt_request"] == "Rewrite my prompt more concisely."
    assert not any(load_shot(project.id, shot.id).prompt_sections.model_dump().values())


@pytest.mark.asyncio
async def test_prompt_action_that_changes_shot_routes_brief_update_before_writing(board):
    from app.shot_workspace.coordinator import converse
    _, shot, _ = board
    class EditModel:
        async def complete(self, *args, **kwargs):
            return json.dumps({"intent":"prompt", "include_adjacent_descriptions":False,
                "reply_language":"English", "update_brief":True})
        async def complete_shot_description(self, system, user, **kwargs):
            assert json.loads(user)["reference_descriptions"]
            return json.dumps(dict(description="A locked close-up shows the object.",title=shot.title,duration_s=5,
                shot_type="close-up",camera_angle="eye level",camera_motion="static",composition="centered",dialogue=[],questions=[]))
    result = await converse(shot, shot.script_beat, "Rewrite the prompt as a locked close-up.",
        snapshot(shot)["version"], EditModel())
    assert result["kind"] == "prompt" and result["update_brief"]
    assert "draft" not in result  # Refine only after the UI has saved current dialogue/camera edits.


@pytest.mark.asyncio
async def test_prompt_revision_receives_previous_prompt_and_rejects_invalid_replacement(board):
    project, shot, _ = board
    saved = await write_prompt(project.id, shot.id, model=Model())
    model = Model()
    revised = await write_prompt(project.id, shot.id, model=model, revision_request="Shorten the wording.")
    assert model.calls[0][1]["prompt_edit"]["previous_prompt"] == saved.prompt_sections.model_dump()
    assert model.calls[0][1]["prompt_edit"]["request"] == "Shorten the wording."
    assert revised.script_beat == saved.script_beat
    class InvalidModel:
        async def complete(self, *args, **kwargs):
            return json.dumps({"prompt_sections": {"summary":"Missing five sections."}})
    with pytest.raises(ValueError):
        await write_prompt(project.id, shot.id, model=InvalidModel(), revision_request="Shorten again.")
    assert load_shot(project.id, shot.id).prompt_sections == revised.prompt_sections


@pytest.mark.asyncio
async def test_voice_binding_scope_conflicts_and_description_freshness(board, monkeypatch):
    from types import SimpleNamespace
    from app.core.library import store
    from app.core.projects.models import ShotVoiceRef
    from app.shot_workspace.router import replace_voice_references, VoiceReferences
    project, shot, _ = board
    def normalize(source, target):
        target.write_bytes(b"synthetic-test-audio")
        return SimpleNamespace(duration_s=3,source_format="wav",source_sample_rate=24000,
            source_channels=1,reference_sample_rate=24000,reference_channels=1)
    monkeypatch.setattr(store, "normalize_voice_reference", normalize)
    voice = store.create_external_voice_asset(name="Guide sample", notes="A calm speaking voice.",
        project_id=project.id,audio_bytes=b"synthetic-test-audio",audio_filename="sample.wav")
    ref = ShotVoiceRef(asset_id=voice.id,audio_index=1,speaker="Guide")
    saved = await replace_voice_references(shot.id, VoiceReferences(voice_refs=[ref],expected_refs=[]))
    assert saved.voice_refs == [ref]
    with pytest.raises(Exception, match="Voice references changed"):
        await replace_voice_references(shot.id, VoiceReferences(voice_refs=[],expected_refs=[]))
    captured = snapshot(saved)
    assert captured["voices"][0]["description"] == voice.notes
    class VoiceModel(Model):
        async def complete(self, system, payload, **kwargs):
            result = json.loads(await super().complete(system, payload, **kwargs))
            result["prompt_sections"]["subject_definitions"] += " Guide uses <Audio 1>."
            return json.dumps(result)
    model = VoiceModel()
    await write_prompt(project.id, shot.id, model=model)
    assert model.calls[0][1]["voice_refs"][0]["speaker"] == "Guide"
    voice.notes = "A newly described delivery."
    store.write_asset(voice)
    assert snapshot(saved)["version"] != captured["version"]
    assert prompt_check(load_shot(project.id, shot.id))["stale"]
    other = create_project("Other test project", "", authoring_mode="shot_workspace")
    foreign = store.create_external_voice_asset(name="Foreign sample",project_id=other.id,
        audio_bytes=b"synthetic",audio_filename="sample.wav")
    with pytest.raises(Exception, match="another project"):
        await replace_voice_references(shot.id, VoiceReferences(voice_refs=[ref.model_copy(update={"asset_id":foreign.id})],expected_refs=[ref]))
    voice.meta["duration_s"] = 16
    store.write_asset(voice)
    with pytest.raises(Exception, match="15 seconds"):
        await replace_voice_references(shot.id, VoiceReferences(voice_refs=[ref],expected_refs=[ref]))
    current = load_shot(project.id, shot.id)
    current.source_audio_path = "synthetic-source.wav"
    save_shot(current)
    with pytest.raises(Exception, match="native source audio"):
        await replace_voice_references(shot.id, VoiceReferences(voice_refs=[ref],expected_refs=[ref]))


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
@pytest.mark.parametrize("revision", ["", "Use concise prompt wording."])
async def test_task_receipt_refresh_and_cancellation(board, monkeypatch, revision):
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
    async def pending(*args, **kwargs):
        assert kwargs.get("revision_request", "") == revision
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(vram, "get_orchestrator", lambda: Orchestrator())
    monkeypatch.setattr(writer, "write_prompt", pending)
    receipt = await tasks.start(project.id, shot.id, "action-1", revision_request=revision)
    await asyncio.wait_for(entered.wait(), 1)
    assert tasks.status(project.id)["run_id"] == receipt["run_id"]
    assert (await tasks.start(project.id, shot.id, "action-1", revision_request=revision))["run_id"] == receipt["run_id"]
    with pytest.raises(ValueError):
        await tasks.start(project.id, shot.id, "action-1", revision_request=revision + " different")
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


@pytest.mark.asyncio
async def test_silent_shot_repairs_invented_speech_in_both_description_and_dialogue(board):
    from app.shot_workspace.refinement import refine
    project,shot,_ = board
    class Refiner:
        calls = 0
        async def complete_shot_description(self,system,user,**kwargs):
            self.calls += 1
            context = json.loads(user)
            if self.calls == 2:
                assert context["user_description"] == shot.script_beat
                assert context["repair"]["previous_candidate"]["dialogue"] == ["Guide [English]: Hello there."]
            return json.dumps(dict(description="The object turns slowly." if self.calls == 2 else 'The guide says Hello there.',
                title=shot.title,duration_s=5,shot_type="medium",camera_angle="eye level",camera_motion="static",composition="centered",
                dialogue=[] if self.calls == 2 else ["Guide [English]: Hello there."],questions=[]))
    model = Refiner()
    result = await refine(shot,shot.script_beat,snapshot(shot)["version"],model)
    assert model.calls == 2 and result["dialogue"] == []
    assert "Hello" not in result["description"]
    assert load_shot(project.id,shot.id).dialogue == []


@pytest.mark.asyncio
@pytest.mark.parametrize("description_only", [False, True])
async def test_agent_duration_suggestion_is_editable_saved_and_used_by_writer(board, description_only):
    from app.shot_workspace.refinement import refine
    project, shot, _ = board
    saved = await write_prompt(project.id, shot.id, model=Model())
    class Refiner:
        async def complete_shot_description(self, system, user, **kwargs):
            assert json.loads(user)["duration_s"] == 7  # Unsaved current UI value, not the saved default 5.
            assert "TIMING POLICY" in system
            assert "No dialogue is NOT a reason to default" in system
            assert "re-estimate" in system
            assert "otherwise keep it if suitable" not in system
            return json.dumps(dict(description="The object turns slowly, then settles.",title=shot.title,duration_s=8,
                shot_type="medium",camera_angle="eye level",camera_motion="static",composition="centered",dialogue=[],questions=[]))
    draft = await refine(saved,saved.script_beat,snapshot(saved)["version"],Refiner(),
        amendment="Allow time for the action to settle.",description_only=description_only,duration_s=7)
    assert draft["duration_s"] == 8
    assert load_shot(project.id,shot.id).duration_s == 5  # Suggestion alone does not save.
    updated = await save_brief(shot.id, Brief(description=draft["description"],duration_s=draft["duration_s"],
        expected_version=snapshot(saved)["version"]))
    assert updated.duration_s == 8 and prompt_check(updated)["stale"]
    model = Model()
    await write_prompt(project.id,shot.id,model=model)
    assert model.calls[0][1]["shot"]["duration_s"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [0, -1, 121, float("inf"), float("nan")])
async def test_agent_cannot_return_invalid_duration(board, invalid):
    from app.shot_workspace.refinement import refine
    _,shot,_ = board
    class Refiner:
        async def complete_shot_description(self,*args,**kwargs):
            return json.dumps(dict(description=shot.script_beat,title=shot.title,duration_s=invalid,
                shot_type="medium",camera_angle="eye level",camera_motion="static",composition="centered",dialogue=[],questions=[]))
    with pytest.raises(ValueError):
        await refine(shot,shot.script_beat,snapshot(shot)["version"],Refiner())


@pytest.mark.asyncio
@pytest.mark.parametrize("description_only", [False, True])
async def test_refinement_keeps_dialogue_in_separate_field_without_requiring_duplicate_prose(board, description_only):
    from app.shot_workspace.refinement import refine
    project, shot, _ = board
    saved = await save_brief(shot.id, Brief(description="A guide gestures toward the object.",title=shot.title,duration_s=5,
        dialogue=["Guide [English]: Welcome.", "Visitor [English]: Thank you."],expected_version=snapshot(shot)["version"]))
    class Refiner:
        speaker = "Guide"
        text = "Welcome."
        async def complete_shot_description(self, system, user, **kwargs):
            context = json.loads(user)
            assert [line["text"] for line in context["current_dialogue_lines"]] == ["Welcome.","Thank you."]
            return json.dumps(dict(description="A close-up follows the guide's gesture as the visitor listens.",
                title=saved.title,duration_s=5,shot_type="close-up",camera_angle="eye level",camera_motion="static",composition="centered",
                dialogue=[f"{self.speaker} [English]: {self.text}","Visitor [English]: Thank you."],questions=[]))
    model = Refiner()
    result = await refine(saved, saved.script_beat,snapshot(saved)["version"],model,
        amendment="Use a close-up. Keep all dialogue.",description_only=description_only)
    assert result["dialogue"] == ["Guide [English]: Welcome.","Visitor [English]: Thank you."]
    assert "Welcome." not in result["description"]
    assert load_shot(project.id, shot.id).dialogue == saved.dialogue  # Still only a draft.
    model.text = "New invented speech."
    with pytest.raises(ValueError,match="invented dialogue"):
        await refine(saved,saved.script_beat,snapshot(saved)["version"],model,
            amendment="Use a close-up. Keep all dialogue.",description_only=description_only)
    model.text = "Welcome."
    model.speaker = "Unselected speaker"
    with pytest.raises(ValueError,match="invented dialogue"):
        await refine(saved,saved.script_beat,snapshot(saved)["version"],model,
            amendment="Use a close-up. Keep all dialogue.",description_only=description_only)


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", ["", "A blue box rotates on the table."])
async def test_description_assistant_sends_only_current_text_and_latest_request(board, previous):
    from app.shot_workspace.refinement import refine
    project, shot, _ = board
    class Assistant:
        async def complete_shot_description(self, system, user, **kwargs):
            payload = json.loads(user)
            assert set(payload) == {"current_shot_description", "new_request", "duration_s"}
            assert payload["current_shot_description"] == previous
            assert payload["new_request"] == "Keep the blue box still on the table."
            assert "new request has higher priority" in system
            return json.dumps(dict(description="A blue box remains still on the table.",title="Box view",duration_s=5,
                shot_type="medium",camera_angle="eye level",camera_motion="static",composition="centered",dialogue=[],questions=[]))
    result = await refine(shot, previous, snapshot(shot)["version"], Assistant(), amendment="Keep the blue box still on the table.", description_only=True)
    assert "remains still" in result["description"]
    assert load_shot(project.id, shot.id).script_beat == shot.script_beat


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["chat", "discuss", "neighbors", "amend"])
async def test_coordinator_routes_bounded_text_context_without_prompt_writer(board, intent):
    from app.shot_workspace.coordinator import converse
    project, shot, _ = board
    ids = ["sht_far", "sht_before", shot.id, "sht_after", "sht_far_next"]
    for identifier in ids:
        if identifier != shot.id:
            neighbor = shot.model_copy(deep=True, update={"id":identifier,"script_beat":identifier + " description"})
            neighbor.prompt_sections.summary = "PRIVATE_PROMPT_MUST_NOT_ENTER_COORDINATOR"
            save_shot(neighbor)
    project.shot_ids = ids
    save_project(project)
    class CoordinatorModel:
        calls = []
        async def complete(self, system, payload, **kwargs):
            self.calls.append(payload)
            assert not kwargs.get("images")
            if "Classify" in system:
                assert set(payload) == {"request", "current_description"}
                return json.dumps({"intent":"discuss" if intent == "neighbors" else intent,"include_adjacent_descriptions":intent == "neighbors","reply_language":"English","reply":"Hello." if intent == "chat" else ""})
            assert set(payload) <= {"current_description","request","reply_language","recent_exchange","adjacent_descriptions","reference_descriptions","previous_shot_description"}
            assert payload["previous_shot_description"]["description"] == "sht_before description"
            assert payload["reference_descriptions"]
            assert "PRIVATE_PROMPT" not in json.dumps(payload)
            assert len(payload["recent_exchange"]) == 2
            assert all(len(turn["text"]) <= 600 for turn in payload["recent_exchange"])
            if intent == "neighbors":
                assert payload["adjacent_descriptions"]["previous"]["description"] == "sht_before description"
                assert payload["adjacent_descriptions"]["next"]["description"] == "sht_after description"
                assert "sht_far" not in json.dumps(payload)
            else:
                assert "adjacent_descriptions" not in payload
            return json.dumps({"reply":"A locked medium shot would keep the action clear.","proposed_change":"Use a locked medium shot."})
        async def complete_shot_description(self, system, user, **kwargs):
            payload = json.loads(user)
            assert set(payload) == {"current_shot_description","new_request","duration_s","reference_descriptions","previous_shot_description"}
            assert payload["reference_descriptions"]
            assert payload["previous_shot_description"]["description"] == "sht_before description"
            return json.dumps(dict(description="The object remains still.",title=shot.title,duration_s=5,
                shot_type="medium",camera_angle="eye level",camera_motion="static",composition="centered",dialogue=[],questions=[]))
    model = CoordinatorModel()
    result = await converse(shot, shot.script_beat, "Keep the object still.", snapshot(shot)["version"], model,
        turns=[{"role":"user","text":"x"*1000},{"role":"assistant","text":"y"*1000}])
    assert result["kind"] == ("draft" if intent == "amend" else "reply")
    assert len(model.calls) == (1 if intent in {"chat","amend"} else 2)
    assert load_shot(project.id, shot.id).script_beat == shot.script_beat


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["discuss", "amend"])
@pytest.mark.parametrize("selected", [True, False])
async def test_coordinator_reference_text_is_always_available(board, intent, selected):
    from app.shot_workspace.coordinator import converse
    project, shot, asset = board
    if not selected:
        shot.refs = []
        save_shot(shot)
    class ReferenceModel:
        async def complete(self, system, payload, **kwargs):
            if "Classify" in system:
                assert "references" not in payload
                return json.dumps(dict(intent=intent, include_adjacent_descriptions=False,
                    include_reference_descriptions=False, reply_language="English"))
            refs = payload["reference_descriptions"]
            assert len(refs) == int(selected)
            if refs:
                assert set(refs[0]) == {"picture_index", "role", "name", "description", "binding_notes"}
                assert refs[0]["description"] == asset.notes
            return json.dumps(dict(reply="Which reference do you mean?", proposed_change=""))
        async def complete_shot_description(self, system, user, **kwargs):
            payload = json.loads(user)
            assert len(payload["reference_descriptions"]) == int(selected)
            assert "never image pixels" in system
            return json.dumps(dict(description=shot.script_beat, title=shot.title, duration_s=5,
                shot_type="medium", camera_angle="eye level", camera_motion="static", composition="centered",
                dialogue=[], questions=["Which reference do you mean?"]))
    result = await converse(shot, shot.script_beat, "Use the referenced object's detail.",
        snapshot(shot)["version"], ReferenceModel())
    assert result["kind"] == "reply"
    assert "selected reference descriptions" in result["context_scope"]
    assert load_shot(project.id, shot.id).script_beat == shot.script_beat


@pytest.mark.asyncio
async def test_coordinator_rejects_reference_description_changed_during_discussion(board):
    from app.shot_workspace.coordinator import converse
    _, shot, asset = board
    class ChangingModel:
        async def complete(self, system, payload, **kwargs):
            if "Classify" in system:
                return json.dumps(dict(intent="discuss", include_adjacent_descriptions=False,
                    include_reference_descriptions=True, reply_language="English"))
            asset.notes = "Updated reference details."
            write_asset(asset)
            return json.dumps(dict(reply="Use the handle.", proposed_change="Show the handle."))
    with pytest.raises(ValueError, match="changed"):
        await converse(shot, shot.script_beat, "What about the reference?", snapshot(shot)["version"], ChangingModel())


@pytest.mark.asyncio
async def test_reference_discussion_retries_failed_runtime_decoder_once(board):
    from app.shot_workspace.coordinator import converse
    from openai import InternalServerError
    _, shot, _ = board
    class DecoderModel:
        attempts = 0
        async def complete(self, system, payload, **kwargs):
            if "Classify" in system:
                return json.dumps(dict(intent="chat", include_adjacent_descriptions=False,
                    include_reference_descriptions=True, reply_language="English"))
            self.attempts += 1
            assert payload["reference_descriptions"]
            if self.attempts == 1:
                response = httpx.Response(502, request=httpx.Request("POST", "http://localhost/test"))
                raise InternalServerError("Decoder failed", response=response,
                    body={"error": {"code": "structured_output_failed"}})
            assert kwargs["schema"] is None
            return json.dumps(dict(reply="The reference describes a round handle.", proposed_change=""))
    model = DecoderModel()
    result = await converse(shot, shot.script_beat, "Can you read the reference description?",
        snapshot(shot)["version"], model)
    assert model.attempts == 2
    assert result["kind"] == "reply"


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["discuss", "amend"])
async def test_description_assistant_rejects_previous_shot_context_changed_mid_request(board, intent):
    from app.shot_workspace.coordinator import converse
    project, shot, _ = board
    previous = shot.model_copy(deep=True, update={"id":"sht_previous", "script_beat":"The object is placed on the table."})
    save_shot(previous)
    project.shot_ids = [previous.id, shot.id]
    save_project(project)
    def change_previous():
        previous.script_beat = "The object is removed from the table."
        save_shot(previous)
    class ContextModel:
        async def complete(self, system, payload, **kwargs):
            if "Classify" in system:
                return json.dumps(dict(intent=intent, include_adjacent_descriptions=False,
                    include_reference_descriptions=False, reply_language="English"))
            assert payload["previous_shot_description"]["description"] == "The object is placed on the table."
            change_previous()
            return json.dumps(dict(reply="Continue with a medium shot.", proposed_change="Use a medium shot."))
        async def complete_shot_description(self, system, user, **kwargs):
            payload = json.loads(user)
            assert payload["previous_shot_description"]["description"] == "The object is placed on the table."
            assert payload["reference_descriptions"]
            change_previous()
            return json.dumps(dict(description=shot.script_beat,title=shot.title,duration_s=5,
                shot_type="medium",camera_angle="eye level",camera_motion="static",composition="centered",dialogue=[],questions=[]))
    with pytest.raises(ValueError, match="Previous shot description changed"):
        await converse(shot, shot.script_beat, "Make the action clearer.", snapshot(shot)["version"], ContextModel())
    assert load_shot(project.id, shot.id).script_beat == shot.script_beat


@pytest.mark.asyncio
async def test_new_shot_inherits_previous_bindings_once_and_can_remove_them(board):
    project, previous, asset = board
    previous.refs[0].notes = "Keep the handle visible."
    previous.prompt_sections.summary = "Previous shot prompt."
    save_shot(previous)
    created = await add_shot(project.id, CreateShot(request_id="inherit-refs"))
    assert created.refs == previous.refs
    assert created.script_beat == ""
    assert not any(created.prompt_sections.model_dump().values())
    assert created.h3_job_id is None and created.video_context is None
    assert snapshot(created)["references"][0]["description"] == asset.notes
    cleared = await replace_materials(created.id, Materials(materials=[], expected_refs=created.refs))
    assert cleared.refs == []
    assert load_shot(project.id, previous.id).refs == previous.refs
    retried = await add_shot(project.id, CreateShot(request_id="inherit-refs"))
    assert retried.id == created.id and retried.refs == []
    following = await add_shot(project.id, CreateShot(request_id="inherit-empty"))
    assert following.refs == []


@pytest.mark.asyncio
async def test_first_shot_has_no_inherited_bindings():
    project = create_project("Empty workspace", "", authoring_mode="shot_workspace")
    created = await add_shot(project.id, CreateShot(request_id="first-shot"))
    assert created.refs == [] and created.layout_refs == []


@pytest.mark.asyncio
async def test_new_shot_inherits_layout_binding_without_generation_state(board):
    from app.core.projects.layouts import LayoutReference
    project, previous, _ = board
    previous.refs.append(ShotRef(role="layout_ref_frame", asset_id="layout_test", picture_index=2, file_key="master", notes="Use the composition."))
    previous.layout_refs = [LayoutReference(id="lref_old",asset_id="layout_test",job_id="job_previous",
        selected_for_h3=True,review_status="usable",state_description="A centered composition.")]
    save_shot(previous)
    created = await add_shot(project.id, CreateShot(request_id="inherit-layout"))
    assert created.refs == previous.refs
    assert len(created.layout_refs) == 1
    layout = created.layout_refs[0]
    assert layout.id != "lref_old" and layout.job_id is None
    assert layout.asset_id == "layout_test" and layout.selected_for_h3
    assert layout.review_status == "usable"
    assert created.ref_frame_job_id is None


@pytest.mark.asyncio
async def test_empty_brief_can_be_saved_without_removing_references(board):
    project, shot, _ = board
    shot.prompt_sections.summary = "Previously written prompt."
    save_shot(shot)
    saved = await save_brief(shot.id, Brief(description="",title=shot.title,duration_s=5,
        dialogue=[],expected_version=snapshot(shot)["version"]))
    reloaded = load_shot(project.id, shot.id)
    assert saved.script_beat == reloaded.script_beat == ""
    assert reloaded.refs == shot.refs
    assert reloaded.dialogue == []
    assert reloaded.meta["prompt_brief_stale"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_description_conversation_is_durable_per_shot_and_records_errors(board, monkeypatch, failure):
    import importlib
    import app.shot_workspace.conversations as conversations
    import app.shot_workspace.coordinator as coordinator
    import app.core.vram as vram
    from app.shot_workspace.router import assist_description, DescriptionAssist, description_conversation
    project, shot, _ = board
    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self, **kwargs): yield
        async def ensure_llm_ready(self): pass
    async def reply(*args, **kwargs):
        if failure:
            raise ValueError("The shot changed. Please retry.")
        return {"kind":"reply", "reply":"Use a medium shot.", "proposed_change":"Use a medium shot.", "context_scope":"Current description"}
    monkeypatch.setattr(vram, "get_orchestrator", lambda: Orchestrator())
    monkeypatch.setattr(coordinator, "converse", reply)
    version = snapshot(shot)["version"]
    body = DescriptionAssist(description=shot.script_beat,amendment="How should we frame this?",expected_version=version)
    if failure:
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await assist_description(shot.id, body)
    else:
        await assist_description(shot.id, body)
    import app.shot_workspace.timings as timings
    importlib.reload(timings)
    timing_records = timings.history(shot)
    assert len(timing_records) == 1
    assert timing_records[0]["stage"] == "assistant"
    assert timing_records[0]["status"] == ("failed" if failure else "completed")
    assert timing_records[0]["duration_s"] >= 0
    importlib.reload(conversations)
    messages = description_conversation(shot.id)["messages"]
    assert [message["role"] for message in messages] == ["brief", "user", "error" if failure else "assistant"]
    assert messages[0]["text"] == body.description
    assert messages[0]["scope"] == "Submitted brief"
    assert messages[1]["text"] == body.amendment
    if not failure:
        assert messages[2]["proposal"] == "Use a medium shot."
    assert snapshot(load_shot(project.id,shot.id))["version"] == version
    other = await add_shot(project.id, CreateShot(request_id="new-conversation"))
    assert description_conversation(other.id)["messages"] == []
    assert description_conversation(shot.id)["messages"] == messages
    assert timings.history(other) == []
    assert timings.history(shot) == timing_records


def test_brief_history_keeps_versions_and_deduplicates(board):
    from app.shot_workspace.conversations import append_brief, history
    project, shot, _ = board
    append_brief(shot, "Original idea", "Submitted brief")
    append_brief(shot, "Original idea", "Submitted brief")
    append_brief(shot, "Refined idea", "Refined draft")
    append_brief(shot, "Original idea", "Saved brief")
    messages = history(shot)
    assert [m["text"] for m in messages] == ["Original idea", "Refined idea", "Original idea"]
    assert all(m["role"] == "brief" and m["created_at"] for m in messages)


@pytest.mark.asyncio
async def test_assistant_description_edit_saves_before_acknowledging(board, monkeypatch):
    import app.shot_workspace.coordinator as coordinator
    import app.core.vram as vram
    from app.shot_workspace.router import assist_description, DescriptionAssist, description_conversation
    project, shot, _ = board
    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self, **kwargs): yield
        async def ensure_llm_ready(self): pass
    async def reply(*args, **kwargs):
        return {"kind":"draft","reply":"Review before saving.","draft":{
            "description":"The object pauses, then turns slowly.","title":"Slow turn","duration_s":8,
            "dialogue":[],"shot_type":"close-up","camera_angle":"eye level","camera_motion":"static",
            "composition":"centered","questions":[]},"context_scope":"Current shot"}
    monkeypatch.setattr(vram,"get_orchestrator",lambda:Orchestrator())
    monkeypatch.setattr(coordinator,"converse",reply)
    result=await assist_description(shot.id,DescriptionAssist(description=shot.script_beat,
        amendment="Add a pause before turning.",expected_version=snapshot(shot)["version"]))
    saved=load_shot(project.id,shot.id)
    assert result["kind"] == "updated"
    assert result["reply"] == "Description updated and saved."
    assert saved.script_beat == result["saved_shot"]["script_beat"] == "The object pauses, then turns slowly."
    assert saved.duration_s == 8 and saved.camera_motion == "static"
    assert result["version"] == snapshot(saved)["version"]
    assert description_conversation(shot.id)["messages"][-1]["text"].startswith("Description updated and saved.")


@pytest.mark.asyncio
async def test_cancelled_assistant_does_not_save_description(board, monkeypatch):
    import asyncio
    import app.shot_workspace.coordinator as coordinator
    import app.core.vram as vram
    from app.shot_workspace.router import assist_description, DescriptionAssist
    project, shot, _ = board
    stopped = asyncio.Event()
    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self, **kwargs): yield
        async def ensure_llm_ready(self): pass
    class Disconnected:
        async def is_disconnected(self): return True
    async def reply(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(vram,"get_orchestrator",lambda:Orchestrator())
    monkeypatch.setattr(coordinator,"converse",reply)
    with pytest.raises(asyncio.CancelledError):
        await assist_description(shot.id,DescriptionAssist(description=shot.script_beat,
            amendment="Change the action.",expected_version=snapshot(shot)["version"]),request=Disconnected())
    assert stopped.is_set()
    assert load_shot(project.id,shot.id).script_beat == shot.script_beat


@pytest.mark.asyncio
async def test_project_size_persists_and_is_shared_by_new_shots(board):
    from app.shot_workspace.router import get_project_resolution, set_project_resolution, ProjectResolution
    from app.shot_workspace.resolution import default_resolution
    from app.core.projects.store import load_project
    project, shot, _ = board
    assert get_project_resolution(project.id)["preset"] == "landscape-480"
    set_project_resolution(project.id, ProjectResolution(preset="portrait-720"))
    await add_shot(project.id, CreateShot(request_id="shared-size"))
    assert default_resolution(load_project(project.id)) == "portrait-720"
    assert get_project_resolution(project.id)["preset"] == "portrait-720"
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        set_project_resolution(project.id, ProjectResolution(preset="invalid"))
    assert get_project_resolution(project.id)["preset"] == "portrait-720"


def test_project_size_seeds_from_first_shot_video(board,monkeypatch):
    from types import SimpleNamespace
    import app.shot_workspace.resolution as resolutions
    project, shot, _ = board
    project.shot_ids = [shot.id]
    monkeypatch.setattr(resolutions,"load_shot",lambda *args:SimpleNamespace(h3_job_id="first-video"))
    monkeypatch.setattr(resolutions,"load_job",lambda *args:SimpleNamespace(params={"width":1280,"height":704}))
    assert resolutions.default_resolution(project) == "landscape-720"
    project.workspace_default_resolution = "portrait-480"
    assert resolutions.default_resolution(project) == "portrait-480"


def test_speech_protocol_supplies_exact_audio_tags_and_id_placeholders():
    from types import SimpleNamespace
    from app.shot_workspace.writer import speech_protocol
    shot=SimpleNamespace(dialogue_lines=[SimpleNamespace(line_id="line_a",speaker_name="Guide",language="English"),
        SimpleNamespace(line_id="line_b",speaker_name="Visitor",language="English")])
    protocol=speech_protocol(shot,[{"speaker":"Guide","audio_index":2}])
    assert protocol["required_audio_tags"] == ["<Audio 2>"]
    assert "Guide: use <Audio 2>" in protocol["subject_definitions_voice_bindings"][0]
    assert [item["placeholder"] for item in protocol["detailed_description_speech_sequence"]] == ["{{speech:line_a}}","{{speech:line_b}}"]
    assert "Never quote" in protocol["section_rules"]["summary"]
    assert "Never repeat dialogue" in protocol["section_rules"]["overall_soundscape"]
