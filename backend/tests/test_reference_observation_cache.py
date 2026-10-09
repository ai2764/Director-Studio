"""Presentation-only changes reuse observations without weakening source checks."""
import pytest

from app.agents.director.material_review import capture_references, observe_references_cached
from test_director_material_review import material_shot
from test_reference_facts import Vision, observation


@pytest.mark.asyncio
async def test_renamed_delivery_and_picture_order_reuse_observation(material_shot):
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    provider = Vision(observation(), observation())
    events = []
    async def progress(event): events.append(event)
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None, on_progress=progress)
    renamed = [{**records[0], "filename": "renamed-delivery.png", "picture_index": 7}]
    result = await observe_references_cached(provider, project.id, renamed, images[:1], lambda: None, on_progress=progress)
    assert len(provider.calls) == 1
    assert result[0]["picture_index"] == 7
    assert result[0]["filename"] == "renamed-delivery.png"
    assert any(e.get("phase") == "reference_cache" and e.get("cache_hits") == 1 for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["bytes", "metadata", "model", "instruction"])
async def test_meaningful_changes_reinspect_and_explain_miss(material_shot, change):
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    provider = Vision(observation(), observation())
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    updated = dict(records[0])
    request = ""
    if change == "bytes": updated["content_sha256"] = "new-bytes"
    elif change == "metadata": updated["approved_notes"] = "A revised design requirement."
    elif change == "model": provider.model = "different-model"
    else: request = "Reinspect the current sleeve design."
    events = []
    async def progress(event): events.append(event)
    await observe_references_cached(provider, project.id, [updated], images[:1], lambda: None,
                                    inspection_request=request, on_progress=progress)
    assert len(provider.calls) == 2
    assert any(e.get("phase") == "reference_cache" and e.get("cache_misses") == 1 for e in events)
    expected = {"bytes": "image_changed", "metadata": "metadata_changed", "model": "model_changed", "instruction": "source_evidence_changed"}[change]
    assert any(e.get("cache_reason") == expected for e in events)


@pytest.mark.asyncio
async def test_existing_unicode_v5_cache_is_reused_and_migrated(material_shot):
    import hashlib
    import json
    from app.config import settings
    from app.agents.director.reference_facts import REFERENCE_POLICY_VERSION, sourced_records
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    records[0]["filename"] = "参考图.png"
    provider = Vision()
    stable = {k: v for k, v in sourced_records(project, records[:1])[0].items() if k not in {"picture_index", "reference_notes"}}
    identity = {"version": 5, "fact_policy": REFERENCE_POLICY_VERSION, "record": stable,
                "inspection_request": "", "model": "", "provider": "Vision", "endpoint": ""}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = settings.projects_dir / project.id / "agent/reference_observations"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.json").write_text(json.dumps(observation()), encoding="utf-8")
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    await observe_references_cached(provider, project.id, [{**records[0], "filename": "renamed.png"}], images[:1], lambda: None)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_same_evidence_with_new_citation_ids_rebinds_without_vision(material_shot, monkeypatch):
    from app.agents.director import material_review
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    source = {"id": "original-id", "kind": "library_metadata", "text": "Approved sleeve design."}
    monkeypatch.setattr(material_review, "sourced_records", lambda _p, rs: [{**r, "sources": [source.copy()]} for r in rs])
    provider = Vision({**observation(), "facts": [{"attribute": "wardrobe", "value": "sleeve design", "visibility": "observed",
        "evidence": "Visible sleeve", "source_id": source["id"], "source_quote": source["text"]}]})
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    source["id"] = "current-id"
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert len(provider.calls) == 1
    assert result[0]["facts"][0]["source_id"] == "current-id"
    assert result[0]["facts"][0]["source_id_repaired_from"] == "original-id"
