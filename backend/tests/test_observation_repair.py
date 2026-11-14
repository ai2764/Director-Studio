"""Regression cases for repeated Writer vision requests, including unsafe repairs."""
import json

import pytest

pytestmark = pytest.mark.usefixtures("writer_reference_reuse")

from app.agents.director.material_review import observe_reference, observe_references_cached, capture_references
from test_director_material_review import material_shot
from test_reference_facts import Vision, observation


@pytest.mark.asyncio
async def test_duplicate_library_aliases_do_not_repeat_vision():
    raw = observation("Mia faces forward.")
    raw["facts"] = [dict(attribute="identity", value="Mia", visibility="observed",
        evidence="Mia faces forward.", source_id="reference_notes", source_quote="Face authority only.")]
    sources = [dict(id=key, kind="library_metadata", text="Face authority only.")
               for key in ("approved_notes", "approved_description")]
    provider = Vision(raw)
    result = await observe_reference(provider, {"sources": sources}, "image")
    assert len(provider.calls) == 1
    assert provider.text_calls == []
    assert result["facts"][0]["source_id"] == "approved_notes"
    assert result["facts"][0]["source_id_repaired_from"] == "reference_notes"
    assert result["facts"][0]["source_kind"] == "model_observation"


@pytest.mark.asyncio
async def test_missing_evidence_is_repaired_from_existing_text_without_vision():
    raw = observation("A brown sofa beside a sea window.")
    raw["facts"] = [dict(attribute="sofa", value="brown", visibility="observed")]
    provider = Vision(raw, {"repairs": [{"path": "facts[0].evidence", "value": "A brown sofa"}]})
    result = await observe_reference(provider, {}, "image")
    assert result["facts"][0]["evidence"] == "A brown sofa"
    assert len(provider.calls) == len(provider.text_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["brown", "red"])
async def test_missing_observed_value_must_already_exist_in_evidence(value):
    raw = observation("A brown sofa.")
    raw["facts"] = [dict(attribute="sofa", visibility="observed", evidence="A brown sofa.")]
    patch = {"repairs": [{"path": "facts[0].value", "value": value}]}
    provider = Vision(raw, patch, patch)
    if value == "brown":
        result = await observe_reference(provider, {}, "image")
        assert result["facts"][0]["value"] == "brown"
    else:
        with pytest.raises(ValueError, match="evidence"):
            await observe_reference(provider, {}, "image")
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["description", "fact", "evidence", "drop_fact", "concern", "uncertainty"])
async def test_text_repair_cannot_change_or_delete_visual_evidence(attack):
    raw = observation("A brown sofa beside a sea window.")
    raw["facts"] = [dict(attribute="sofa", value="brown", visibility="observed")]
    path, value = {"description": ("description", "A red sofa."),
        "fact": ("facts[0].value", "red"),
        "evidence": ("facts[0].evidence", "The hidden wall is blue."),
        "drop_fact": ("facts[0]", ""), "concern": ("concerns[0]", "new visual assertion"),
        "uncertainty": ("uncertainties[0].reason", "new assertion")}[attack]
    bad = {"repairs": [{"path": path, "value": value}]}
    provider = Vision(raw, bad, bad)
    with pytest.raises(ValueError, match="repair|evidence"):
        await observe_reference(provider, {}, "image")
    assert len(provider.calls) == 1
    assert len(provider.text_calls) == 2


@pytest.mark.asyncio
async def test_real_conflict_reinspection_has_independent_text_repair():
    raw = observation("A blue jacket.")
    raw["conflicts"] = [dict(attribute="jacket", description_quote="blue", reason="Color uncertain.")]
    next_view = observation("A brown jacket.")
    next_view["facts"] = [dict(attribute="jacket", value="brown", visibility="observed")]
    provider = Vision(raw, next_view, {"repairs": [{"path": "facts[0].evidence", "value": "A brown jacket."}]})
    result = await observe_reference(provider, {}, "image")
    assert result["description"] == "A brown jacket."
    assert len(provider.calls) == 2  # Actual unresolved dispute, not JSON repair.
    assert len(provider.text_calls) == 1


@pytest.mark.asyncio
async def test_five_materials_reused_after_story_order_and_timing_edits(material_shot):
    from app.core.projects.store import save_project
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    records = [{**records[0], "asset_id": f"asset_{i}", "picture_index": i} for i in range(1, 6)]
    images = [images[0]] * 5
    provider = Vision(*(observation() for _ in range(7)))
    await observe_references_cached(provider, project.id, records, images, lambda: None)
    assert len(provider.calls) == 5
    save_project(project.model_copy(update={"script_text": "A different story."}))
    reordered = [{**record, "picture_index": i, "reference_notes": "New position, hold for 8 seconds."}
                 for i, record in enumerate(reversed(records), 1)]
    result = await observe_references_cached(provider, project.id, reordered, images, lambda: None)
    assert len(provider.calls) == 5
    assert result[0]["asset_id"] == "asset_5"
    assert result[0]["reference_notes"] == "New position, hold for 8 seconds."
    reordered[2]["content_sha256"] = "changed pixels"
    await observe_references_cached(provider, project.id, reordered, images, lambda: None)
    assert len(provider.calls) == 6
    await observe_references_cached(provider, project.id, reordered, images, lambda: None,
                                    refresh_pictures={2}, refresh_request="Recheck sleeve detail.")
    assert len(provider.calls) == 7


@pytest.mark.asyncio
async def test_cache_write_failure_is_reported(material_shot, monkeypatch):
    from app.agents.director.reference_cache import ObservationCache
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    monkeypatch.setattr(ObservationCache, "save", lambda *_: False)
    events = []
    async def progress(event): events.append(event)
    await observe_references_cached(Vision(observation()), project.id, records[:1], images[:1],
                                    lambda: None, on_progress=progress)
    assert any(event.get("cache_reason") == "cache_persistence_failed" for event in events)
    resumed = Vision()
    await observe_references_cached(resumed, project.id, records[:1], images[:1], lambda: None)
    assert resumed.calls == [] and resumed.text_calls == []


def test_atomic_cache_write_fits_when_destination_fits(tmp_path):
    from app.agents.director.reference_cache import _write
    directory = tmp_path / ("d" * max(1, 178 - len(str(tmp_path))))
    path = directory / ("a" * 64 + ".json")
    _write(path, '{"readable":true}')
    assert json.loads(path.read_text()) == {"readable": True}
    assert list(directory.iterdir()) == [path]


@pytest.mark.parametrize("difference", ["kind", "text", "source_message_id"])
def test_equivalent_source_repair_rejects_distinct_provenance(difference):
    from app.agents.director.material_review import ReferenceObservation
    from app.agents.director.reference_facts import validate_observation_sources
    raw = observation()
    raw["facts"] = [dict(attribute="identity", value="Mia", visibility="observed", evidence="Visible face",
                         source_id="wrong", source_quote="Face authority only.")]
    sources = [dict(id="a", kind="library_metadata", text="Face authority only."),
               dict(id="b", kind="library_metadata", text="Face authority only.")]
    sources[1][difference] = {"kind": "user_directing_request", "text": "Face authority only. Updated.",
                               "source_message_id": "new-message"}[difference]
    with pytest.raises(ValueError, match="source"):
        validate_observation_sources(ReferenceObservation.model_validate(raw), sources)


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["not_visible", "observed"])
async def test_metadata_quote_repairs_missing_evidence_without_certifying_pixels(visibility):
    raw = observation("A portrait on a gray background.")
    raw["facts"] = [dict(attribute="role", visibility=visibility, value="identity" if visibility=="observed" else None,
                         source_id="approved_notes", source_quote="Face authority only.")]
    patch = {"repairs": [{"path": "facts[0].evidence", "value": "Face authority only."}]}
    provider = Vision(raw, patch, patch)
    record = {"sources": [dict(id="approved_notes", kind="library_metadata", text="Face authority only.")]}
    if visibility == "observed":
        with pytest.raises(ValueError, match="evidence"):
            await observe_reference(provider, record, "image")
    else:
        result = await observe_reference(provider, record, "image")
        assert result["facts"][0]["value"] is None
        assert result["facts"][0]["source_kind"] == "model_observation"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_failed_schema_repair_resumes_without_vision_on_next_request(material_shot):
    from app.config import settings
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    raw = observation("A brown sofa.")
    raw["facts"] = [dict(attribute="sofa", value="brown", visibility="observed")]
    bad = {"repairs": [{"path": "facts[0].evidence", "value": "a red sofa"}]}
    provider = Vision(raw, bad, bad)
    with pytest.raises(ValueError):
        await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    directory = settings.projects_dir/project.id/'agent/reference_observations'
    assert not list(directory.glob('*.json'))  # Pending is not accepted evidence.
    assert len(list((directory/'pending').glob('*.json'))) == 1
    resumed = Vision({"repairs": [{"path": "facts[0].evidence", "value": "A brown sofa"}]})
    result = await observe_references_cached(resumed, project.id, records[:1], images[:1], lambda: None)
    assert resumed.calls == []
    assert len(resumed.text_calls) == 1
    assert result[0]['facts'][0]['value'] == 'brown'
    assert not list((directory/'pending').glob('*.json'))
    assert len(list(directory.glob('*.json'))) == 1


@pytest.mark.asyncio
async def test_default_pixel_cache_does_not_read_metadata_as_visual_instructions(material_shot):
    from app.config import settings
    assert settings.director_visual_facts_cache is True
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    records[0]['approved_notes'] = 'Empty set reference; actors supplied separately.'
    provider = Vision(observation())
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert 'Empty set reference' not in provider.calls[0][1]
    updated = {**records[0], 'approved_notes': 'Revised authoritative notes.'}
    result = await observe_references_cached(provider, project.id, [updated], images[:1], lambda: None)
    assert len(provider.calls) == 1
    assert result[0]['approved_notes'] == updated['approved_notes']
    assert any(source['text'] == updated['approved_notes'] for source in result[0]['sources'])


@pytest.mark.asyncio
async def test_legacy_context_cache_is_reused_but_never_promoted_to_pixel_scope(material_shot, monkeypatch):
    from app.config import settings
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    monkeypatch.setattr(settings, 'director_visual_facts_cache', False)
    old = Vision(observation('A valid contextual observation.'))
    await observe_references_cached(old, project.id, records[:1], images[:1], lambda: None)
    monkeypatch.setattr(settings, 'director_visual_facts_cache', True)
    provider = Vision(observation('A fresh pixel observation.'))
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert provider.calls == []  # Upgrade does not trigger a reread storm.
    assert result[0]['description'] == 'A valid contextual observation.'
    updated = {**records[0], 'approved_notes': 'Completely different metadata.'}
    result = await observe_references_cached(provider, project.id, [updated], images[:1], lambda: None)
    assert len(provider.calls) == 1  # The contextual caption was not globally promoted.
    assert result[0]['description'] == 'A fresh pixel observation.'
    updated['approved_notes'] = 'Yet another edit.'
    await observe_references_cached(provider, project.id, [updated], images[:1], lambda: None)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['bytes', 'model', 'force'])
async def test_pending_candidate_respects_real_invalidation(material_shot, change):
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    raw = observation('A brown sofa.')
    raw['facts'] = [dict(attribute='sofa', value='brown', visibility='observed')]
    bad = {'repairs': [{'path': 'facts[0].evidence', 'value': 'red'}]}
    with pytest.raises(ValueError):
        await observe_references_cached(Vision(raw, bad, bad), project.id, records[:1], images[:1], lambda: None)
    provider = Vision(observation('Fresh current pixels.'))
    if change == 'bytes': records[0]['content_sha256'] = 'changed bytes'
    if change == 'model': provider.model = 'new-model'
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None,
                                            force_reinspect=change == 'force')
    assert len(provider.calls) == 1
    assert result[0]['description'] == 'Fresh current pixels.'


@pytest.mark.asyncio
async def test_failed_fresh_inspection_never_silently_restores_old_cache(material_shot):
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    await observe_references_cached(Vision(observation('Old observation.')), project.id, records[:1], images[:1], lambda: None)
    raw = observation('A brown sofa.')
    raw['facts'] = [dict(attribute='sofa', value='brown', visibility='observed')]
    bad = {'repairs': [{'path': 'facts[0].evidence', 'value': 'red'}]}
    with pytest.raises(ValueError):
        await observe_references_cached(Vision(raw, bad, bad), project.id, records[:1], images[:1], lambda: None,
                                        force_reinspect=True)
    resumed = Vision({'repairs': [{'path': 'facts[0].evidence', 'value': 'A brown sofa'}]})
    result = await observe_references_cached(resumed, project.id, records[:1], images[:1], lambda: None)
    assert resumed.calls == []
    assert len(resumed.text_calls) == 1
    assert result[0]['description'] == 'A brown sofa.'


@pytest.mark.asyncio
async def test_invalid_new_pixel_cache_does_not_restore_older_contextual_evidence(material_shot, monkeypatch):
    from app.config import settings
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    monkeypatch.setattr(settings, 'director_visual_facts_cache', False)
    await observe_references_cached(Vision(observation('Older contextual evidence.')), project.id, records[:1], images[:1], lambda: None)
    monkeypatch.setattr(settings, 'director_visual_facts_cache', True)
    await observe_references_cached(Vision(observation('Newer pixel evidence.')), project.id, records[:1], images[:1], lambda: None,
                                    force_reinspect=True)
    directory = settings.projects_dir/project.id/'agent/reference_observations'
    newer = next(path for path in directory.glob('*.json') if 'Newer pixel evidence' in path.read_text())
    newer.write_text('{"readable":true,"description":"broken"}', encoding='utf8')
    provider = Vision(observation('Fresh current evidence.'))
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert len(provider.calls) == 1
    assert result[0]['description'] == 'Fresh current evidence.'
