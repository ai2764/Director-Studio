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
