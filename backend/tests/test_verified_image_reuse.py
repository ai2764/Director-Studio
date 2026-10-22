"""Real reference files and saved observations; only inference is substituted."""
import json

import pytest

from app.agents.director.material_review import capture_references, observe_references_cached
from app.agents.director.vision import collect_vision_attachments
from app.core.projects.store import save_shot
from test_director_material_review import material_shot


class Vision:
    model = "test-vision"

    async def complete_with_images(self, system, user, **kwargs):
        return json.dumps({"readable": True, "description": "A brass gear at screen left.",
            "concerns": [], "facts": [{"attribute": "material", "value": "brass",
                "visibility": "observed", "evidence": "Visible brass surface."}]})


async def seed_observations(shot):
    records, images, _ = capture_references(shot)
    await observe_references_cached(Vision(), shot.project_id, records, images, lambda: None)


@pytest.mark.asyncio
async def test_verified_images_are_replaced_with_numbered_visual_evidence(material_shot):
    from app.config import settings
    _, shot, _, _ = material_shot
    await seed_observations(shot)
    pack = collect_vision_attachments(project_id=shot.project_id, shots=[shot], message="看图")
    assert pack["images_b64"] == []
    assert pack["reused_image_count"] == 9
    assert "Picture 9" in pack["note"]
    assert "A brass gear at screen left." in pack["note"]
    assert '"material"' in pack["note"]
    assert "inspect_asset" in pack["note"]
    rows = [json.loads(line) for line in (settings.data_dir / "logs" / "llm-performance.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["kind"] == "image_context"
    assert rows[-1]["image_count"] == 0
    assert rows[-1]["reused_image_count"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["bytes", "metadata", "missing_cache", "invalid_cache", "uncertainty", "uncertain_fact", "source"])
async def test_unverified_or_changed_image_is_attached(material_shot, change):
    from app.config import settings
    from app.core.library.store import load_asset
    from app.core.projects.store import save_project
    project, shot, _, files = material_shot
    shot = shot.model_copy(update={"refs": shot.refs[:1]})
    save_shot(shot)
    await seed_observations(shot)
    cache = settings.projects_dir / project.id / "agent" / "reference_facts.json"
    if change == "bytes":
        from PIL import Image
        Image.new("RGB", (100, 100), "red").save(files[0])
    elif change == "metadata":
        asset = load_asset("props", shot.refs[0].asset_id)
        path = files[0].parent / "asset.json"
        path.write_text(asset.model_copy(update={"notes": "New intent"}).model_dump_json(), encoding="utf-8")
    elif change == "missing_cache":
        cache.unlink()
    elif change == "invalid_cache":
        cache.write_text('{"references": []}', encoding="utf-8")
    elif change == "source":
        save_project(project.model_copy(update={"script_text": "A different scene."}))
    else:
        data = json.loads(cache.read_text(encoding="utf-8"))
        for record in data["references"].values():
            if change == "uncertain_fact":
                record["facts"][0].update(visibility="uncertain", value=None)
            else:
                record["concerns"] = ["The material is unclear."]
        cache.write_text(json.dumps(data), encoding="utf-8")
    pack = collect_vision_attachments(project_id=project.id, shots=[shot], message="看图")
    assert len(pack["images_b64"]) == 1
    assert pack.get("reused_image_count", 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["重新看图，确认细节", "Reinspect the image", "Look at the image again"])
async def test_explicit_reinspection_keeps_pixels(material_shot, message):
    _, shot, _, _ = material_shot
    await seed_observations(shot)
    pack = collect_vision_attachments(project_id=shot.project_id, shots=[shot], message=message)
    assert len(pack["images_b64"]) == 4
    assert pack.get("reused_image_count", 0) == 0


@pytest.mark.asyncio
async def test_harness_receives_reused_evidence_without_image_budget(material_shot, monkeypatch):
    from app.agents.director.harness_runtime import handle_harness_chat
    from app.config import settings
    _, shot, _, _ = material_shot
    await seed_observations(shot)
    monkeypatch.setattr(settings, "director_task_context_mode", "off")
    monkeypatch.setattr(settings, "harness_internal_token", "test-token")
    async def run(self, request, dispatch, on_progress):
        context = await dispatch("context", {})
        assert "A brass gear at screen left." in context["system"]
        assert "Picture 9" in context["system"]
        assert "# Visual QC" in context["system"]
        assert request["context_window"] == 32768 - settings.director_num_predict
        return {"reply": "The gear is brass."}
    monkeypatch.setattr("app.agents.director.harness_runtime.HarnessClient.run", run)
    await handle_harness_chat(project_id=shot.project_id, message="看图", svc=object(),
                              context_capacity=32768)


@pytest.mark.asyncio
async def test_legacy_chat_receives_cached_evidence(material_shot, monkeypatch):
    from app.agents.director.chat import handle_chat
    from app.config import settings
    _, shot, _, _ = material_shot
    await seed_observations(shot)
    monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
    calls = []
    async def chat(system, user, **kwargs):
        calls.append((user, kwargs))
        return "The gear is brass."
    await handle_chat(project_id=shot.project_id, message="看图", svc=object(), chat_fn=chat)
    assert calls
    user, request = calls[0]
    assert "A brass gear at screen left." in user
    assert "Picture 9" in user
    assert not request.get("images")
    assert "visual-qc" in request.get("guides", ())


@pytest.mark.asyncio
async def test_reuse_can_be_disabled_for_same_request_benchmark(material_shot, monkeypatch):
    from app.config import settings
    _, shot, _, _ = material_shot
    await seed_observations(shot)
    monkeypatch.setattr(settings, "director_reuse_verified_images", False)
    pack = collect_vision_attachments(project_id=shot.project_id, shots=[shot], message="看图")
    assert len(pack["images_b64"]) == 4
    assert pack.get("reused_image_count", 0) == 0


@pytest.mark.asyncio
async def test_preexisting_verified_observations_can_be_reused(material_shot):
    from app.config import settings
    _, shot, _, _ = material_shot
    await seed_observations(shot)
    path = settings.projects_dir / shot.project_id / "agent" / "reference_facts.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in data["references"].values():
        record.pop("script_signature", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    pack = collect_vision_attachments(project_id=shot.project_id, shots=[shot], message="看图")
    assert pack["images_b64"] == []
    assert pack["reused_image_count"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("named", [False, True])
async def test_reused_layout_keeps_current_purpose_state_and_time(tmp_path, named):
    from app.config import settings
    from app.core.projects.models import ShotRef, RefRole
    from app.core.projects.store import create_project, save_project
    from test_director_vision import _seed_layout_image, _vision_shot
    project = create_project("Layout evidence", "An empty doorway.")
    _seed_layout_image(settings.library_root, "lay_before", (200, 30, 30))
    shot = _vision_shot(project.id).model_copy(update={"refs": [
        ShotRef(asset_id="lay_before", role=RefRole.layout_ref_frame, file_key="layout", picture_index=1)]})
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    save_shot(shot)
    await seed_observations(shot)
    pack = collect_vision_attachments(project_id=project.id, shots=[shot], message="看图",
                                      layout_ref_ids=["lr_before"] if named else None)
    assert pack["images_b64"] == []
    assert "empty doorway" in pack["note"]
    assert "Lu is alone inside" in pack["note"]
    assert "before Chen enters" in pack["note"]
