"""Regression coverage for issue #35 using real files, cache and storage.

Only external visual inference is replaced with counted responses.
"""
import pytest

from app.agents.director.brief import remember_directing_request
from app.agents.director.material_review import capture_references, observe_references_cached
from app.core.projects.store import save_shot
from test_director_material_review import material_shot, Orchestrator, Provider
from test_reference_facts import Vision, observation


@pytest.mark.asyncio
@pytest.mark.parametrize("change,expected_calls,expected_reason", [
    ("identical", 0, None),
    ("new_provider_instance", 0, None),
    ("duration_only", 0, None),
    ("shot_action_only", 0, None),
    ("explicit_reinspection", 5, "source_evidence_changed"),
    ("unrelated_directing_request", 0, None),
    ("model", 5, "model_changed"),
    ("one_image", 1, "image_changed"),
])
async def test_issue35_cache_trigger_matrix(material_shot, change, expected_calls, expected_reason):
    from PIL import Image

    project, shot, _, files = material_shot
    shot = shot.model_copy(update={"refs": shot.refs[:5]}, deep=True)
    save_shot(shot)
    records, images, _ = capture_references(shot)
    provider = Vision(*(observation() for _ in range(10)))
    await observe_references_cached(provider, project.id, records, images, lambda: None)
    assert len(provider.calls) == 5

    request = ""
    if change == "new_provider_instance":
        provider = Vision()
    elif change == "duration_only":
        shot = shot.model_copy(update={"duration_s": 8})
        save_shot(shot)
    elif change == "shot_action_only":
        shot = shot.model_copy(update={"script_beat": "The watchmaker sits down and laughs."})
        save_shot(shot)
    elif change == "explicit_reinspection":
        request = "Reinspect the current sleeve design."
    elif change == "unrelated_directing_request":
        remember_directing_request(project.id, "For the neighboring shot only, make it 8 seconds.")
    elif change == "model":
        provider.model = "different-quantization"
    elif change == "one_image":
        Image.effect_noise((640, 480), 80).convert("RGB").save(files[0])

    updated_records, updated_images, _ = capture_references(shot)
    changed_hashes = sum(a["content_sha256"] != b["content_sha256"]
                         for a, b in zip(records, updated_records, strict=True))
    assert changed_hashes == (1 if change == "one_image" else 0)
    before = len(provider.calls)
    events = []

    async def progress(event):
        events.append(event)

    await observe_references_cached(provider, project.id, updated_records, updated_images,
                                    lambda: None, inspection_request=request, on_progress=progress)
    assert len(provider.calls) - before == expected_calls
    misses = [event for event in events if event.get("state") == "miss"]
    assert len(misses) == expected_calls
    assert all(event["cache_reason"] == expected_reason for event in misses)
    print(f"{change}: {changed_hashes} image hashes changed, "
          f"{5 - expected_calls} reused, {expected_calls} vision calls; reason={expected_reason}")


@pytest.mark.asyncio
async def test_issue35_duration_edit_reviews_new_intent_without_reinspecting(material_shot):
    from app.agents.director.service import DirectorService

    project, shot, _, _ = material_shot
    provider = Provider(Orchestrator())
    service = DirectorService(plan_provider=provider, orchestrator=provider.orch)
    first = await service.write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 9
    before, _, _ = capture_references(first)
    save_shot(first.model_copy(update={"duration_s": 8}))
    events = []

    async def progress(event):
        events.append(event)

    second = await service.write_prompts_after_layout(
        shot.id, revision_request="Make this shot 8 seconds so the character can finish speaking.",
        on_progress=progress)
    after, _, _ = capture_references(second)
    assert [r["content_sha256"] for r in before] == [r["content_sha256"] for r in after]
    assert len(provider.visual) == 9
    assert second.duration_s == 8
    misses = [e for e in events if e.get("phase") == "reference_cache" and e.get("state") == "miss"]
    assert misses == []
    hits = [e for e in events if e.get("phase") == "reference_cache" and e.get("state") == "hit"]
    assert len(hits) == 9
    import json
    review_requests = [json.loads(user) for system, user in provider.text
                      if "reference review decision" in system.lower()]
    assert len(review_requests) == 2
    assert review_requests[-1]["intent"]["current_request"] == (
        "Make this shot 8 seconds so the character can finish speaking.")
    assert review_requests[-1]["shot"]["duration_s"] == 8
    print("prompt service duration edit: 0 image hashes changed, 9 reused, "
          "0 additional vision calls; latest intent reviewed")


@pytest.mark.asyncio
async def test_directing_history_is_not_used_to_describe_unchanged_pixels(material_shot):
    project, shot, _, _ = material_shot
    request = "For the neighboring shot only, make it 8 seconds."
    remember_directing_request(project.id, request)
    records, images, _ = capture_references(shot)
    provider = Vision(observation())
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    vision_user = provider.calls[0][1]
    assert request not in vision_user
    assert "Approved gear design" in vision_user
    # The current evidence still travels to downstream suitability review.
    assert any(source["text"] == request for source in result[0]["sources"])


@pytest.mark.asyncio
async def test_review_cannot_loop_reinspection_of_unchanged_pictures(material_shot):
    import json
    from app.agents.director.material_review import review_references
    from app.core.prompt_errors import MaterialReviewError

    project, shot, _, _ = material_shot

    class RepeatingReviewer(Provider):
        async def complete(self, system, user, **kwargs):
            response = json.loads(await super().complete(system, user, **kwargs))
            response["reinspect_pictures"] = [1]
            return json.dumps(response)

    orch = Orchestrator()
    provider = RepeatingReviewer(orch)
    records, images, signature = capture_references(shot)
    async with orch.llm_session():
        with pytest.raises(MaterialReviewError, match="reinspection.*budget"):
            await review_references(provider, project, shot, records, images, signature, lambda: None,
                                    revision_request="Check the jacket again.")
    assert len(provider.visual) == 10
