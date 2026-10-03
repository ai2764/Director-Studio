import json
import pytest
from app.agents.director.material_review import observe_reference
from test_director_material_review import material_shot, Orchestrator, Provider


class Vision:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def complete_with_images(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        return json.dumps(self.answers.pop(0))


def certify_reference_test_shot(project, shot):
    """Already-reviewed snapshot for tests about transport, not visual inference."""
    from app.core.projects.layouts import sync_selected_layout_refs
    from app.agents.director.material_review import capture_references
    from app.agents.director.reference_facts import (reference_context_signature,
        reference_intent_signature, certify_reference_prompt)
    if not shot.refs or project.mode.value != "director":
        return shot
    shot = sync_selected_layout_refs(shot)
    records, _, signature = capture_references(shot)
    review = dict(signature=signature, facts_signature=reference_context_signature(project, records),
        intent_signature=reference_intent_signature(shot), references=records)
    shot = shot.model_copy(update={"meta": {**shot.meta, "material_review": review}})
    return shot.model_copy(update={"meta": {**shot.meta,
        "prompt_reference_contract": certify_reference_prompt(project, shot)}})


def observation(description="A beige blazer and pencil skirt.", **extra):
    return dict(readable=True, description=description, concerns=[], facts=[], conflicts=[], **extra)


def conflict_observation():
    result = observation("A pantsuit consisting of a blazer and pencil skirt.")
    result["conflicts"] = [dict(attribute="wardrobe", quote=result["description"], reason="The outfit labels contradict each other.")]
    return result


@pytest.mark.asyncio
async def test_structure_error_during_reinspection_has_its_own_repair_budget():
    malformed = observation("Only the upper body is visible.")
    malformed["facts"] = [dict(attribute="lower garment", value="trousers",
        visibility="uncertain", evidence="The lower body is outside the image.")]
    clean = observation("Only the upper body is visible.")
    clean["facts"] = [dict(attribute="lower garment", value=None,
        visibility="uncertain", evidence="The lower body is outside the image.")]
    provider = Vision(conflict_observation(), malformed, clean)
    result = await observe_reference(provider, {}, "image")
    assert result["facts"][0]["value"] is None
    assert result["description"] == "Only the upper body is visible."
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_distinct_source_errors_can_be_repaired_locally():
    wrong_source = observation()
    wrong_source["facts"] = [dict(attribute="wardrobe", value="beige",
        visibility="observed", evidence="Visible fabric", source_id="missing", source_quote="invented")]
    wrong_conflict = observation()
    wrong_conflict["conflicts"] = [dict(attribute="wardrobe", quote="not present", reason="Mismatch")]
    provider = Vision(wrong_source, wrong_conflict, observation())
    result = await observe_reference(provider, {"sources": []}, "image")
    assert result["description"] == "A beige blazer and pencil skirt."
    assert result["conflicts"] == []
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_mixed_reinspection_and_schema_repairs_remain_bounded():
    wrong_source = observation()
    wrong_source["facts"] = [dict(attribute="wardrobe", value="beige",
        visibility="observed", evidence="Visible fabric", source_id="missing", source_quote="invented")]
    wrong_conflict = observation()
    wrong_conflict["conflicts"] = [dict(attribute="wardrobe", quote="not present", reason="Mismatch")]
    provider = Vision(wrong_source, conflict_observation(), wrong_conflict, observation())
    result = await observe_reference(provider, {"sources": []}, "image")
    assert result["description"] == "A beige blazer and pencil skirt."
    assert len(provider.calls) == 4

    # A further invalid response exhausts the budget instead of asking the user
    # to unknowingly pay for an unbounded retry loop.
    provider = Vision(wrong_source, conflict_observation(), wrong_conflict, wrong_source)
    with pytest.raises(ValueError, match="source"):
        await observe_reference(provider, {"sources": []}, "image")
    assert len(provider.calls) == 4


@pytest.mark.asyncio
async def test_targeted_conflict_reinspection_replaces_bad_description():
    clean = observation()
    clean["facts"] = [dict(attribute="lower garment", value="pencil skirt", visibility="observed", evidence="A continuous fabric silhouette extends to the knees.")]
    provider = Vision(conflict_observation(), clean)
    result = await observe_reference(provider, {"picture_index": 2, "asset_id": "person_17"}, "image")
    assert result["description"] == "A beige blazer and pencil skirt."
    assert result["facts"][0]["value"] == "pencil skirt"
    assert len(provider.calls) == 2
    assert "The outfit labels contradict" in provider.calls[1][1]


@pytest.mark.asyncio
async def test_unresolved_dispute_is_not_sent_as_a_visible_fact():
    provider = Vision(conflict_observation(), conflict_observation())
    result = await observe_reference(provider, {"picture_index": 1}, "image")
    assert "pantsuit" not in result["description"]
    assert result["uncertainties"][0]["attribute"] == "wardrobe"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_cropped_unknown_does_not_invent_clothing_or_block():
    cropped = observation("Head and shoulders only.")
    cropped["facts"] = [dict(attribute="lower garment", value=None, visibility="not_visible", evidence="The image ends above the waist.")]
    result = await observe_reference(Vision(cropped), {}, "image")
    assert result["facts"][0]["value"] is None
    assert result["facts"][0]["visibility"] == "not_visible"


@pytest.mark.asyncio
async def test_model_cannot_claim_nonexistent_user_source():
    forged = observation()
    forged["facts"] = [dict(attribute="wardrobe", value="anything", visibility="observed", evidence="Visible fabric.", source_id="user-confirmed", source_quote="Approved by the user.")]
    with pytest.raises(ValueError, match="source"):
        await observe_reference(Vision(forged, forged), {"sources": []}, "image")


@pytest.mark.asyncio
async def test_source_authority_is_server_owned_and_new_appearance_is_allowed():
    result = observation("A violet jumpsuit.")
    result["facts"] = [dict(attribute="wardrobe", value="violet jumpsuit", visibility="observed", evidence="A continuous violet garment.", source_id="request-2", source_quote="Use the new violet jumpsuit.")]
    sources = [dict(id="request-2", kind="user_directing_request", text="Use the new violet jumpsuit.")]
    observed = await observe_reference(Vision(result), {"sources": sources}, "image")
    assert observed["facts"][0]["source_kind"] == "model_observation"
    assert observed["facts"][0]["cited_source_kind"] == "user_directing_request"
    assert observed["facts"][0]["value"] == "violet jumpsuit"


@pytest.mark.asyncio
async def test_invalid_conflict_quote_cannot_delete_unrelated_description():
    wrong = observation()
    wrong["conflicts"] = [dict(attribute="wardrobe", quote="not present", reason="invented")]
    with pytest.raises(ValueError, match="quote"):
        await observe_reference(Vision(wrong, wrong), {}, "image")


@pytest.mark.asyncio
async def test_conflict_quote_repair_targets_description_not_external_sources():
    wrong = observation("Hands grip a wooden seat beside the bed.")
    wrong["concerns"] = ["No backrest is visible."]
    wrong["conflicts"] = [dict(attribute="chair_structure", quote="No backrest is visible.",
        reason="The reference notes describe a chair back.")]
    provider = Vision(wrong, observation("Hands grip a wooden seat beside the bed."))
    await observe_reference(provider, {"sources": []}, "image")
    repair = provider.calls[1][1]
    assert "conflicts[].quote must be copied verbatim from your CURRENT description" in repair
    assert "not from concerns, source metadata, or the previous response" in repair


@pytest.mark.asyncio
async def test_writer_carries_structured_evidence_and_unknowns(material_shot):
    from app.agents.director.service import DirectorService
    project, shot, _, _ = material_shot
    class EvidenceProvider(Provider):
        async def complete_with_images(self, *args, **kwargs):
            data = json.loads(await super().complete_with_images(*args, **kwargs))
            data["facts"] = [dict(attribute="hidden mechanism", value=None, visibility="not_visible", evidence="Exterior view only.")]
            return json.dumps(data)
    orch = Orchestrator()
    provider = EvidenceProvider(orch)
    result = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    writer = provider.text[-1][1]
    assert '"visibility": "not_visible"' in writer
    assert "Exterior view only." in writer
    assert result.meta["prompt_reference_contract"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["bytes", "metadata", "order", "requirements", "prompt"])
async def test_reference_contract_rejects_changed_source_or_output(material_shot, change):
    from app.agents.director.reference_facts import reference_contract_current
    from app.agents.director.service import DirectorService
    from app.agents.director.brief import remember_directing_request
    from PIL import Image
    project, shot, _, files = material_shot
    orch = Orchestrator()
    result = await DirectorService(plan_provider=Provider(orch), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert reference_contract_current(project, result)
    if change == "bytes":
        Image.new("RGB", (40, 40), "red").save(files[0])
    elif change == "metadata":
        path = files[0].parent / "asset.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["notes"] = "New authored design"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif change == "order":
        result = result.model_copy(update={"refs": [r.model_copy(update={"picture_index": 10-r.picture_index}) for r in result.refs]})
    elif change == "requirements":
        remember_directing_request(project.id, "Use the new approved reference appearance.")
    else:
        result = result.model_copy(update={"prompt_sections": result.prompt_sections.model_copy(update={"summary": "Changed"})})
    assert not reference_contract_current(project, result)


@pytest.mark.asyncio
async def test_derived_reference_facts_do_not_stale_authored_managed_plan(material_shot):
    from app.core.managed_runs.store import _fingerprint
    from app.core.projects.store import project_dir
    from app.agents.director.service import DirectorService
    project, shot, _, _ = material_shot
    before = _fingerprint(project.id)
    orch = Orchestrator()
    await DirectorService(plan_provider=Provider(orch), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert _fingerprint(project.id) == before
    records = json.loads((project_dir(project.id) / "agent/reference_facts.json").read_text(encoding="utf-8"))
    assert len(records["references"]) == 9
    assert all(r["content_sha256"] for r in records["references"].values())
    assert all(r["observation_model"]["provider"] == "Provider" for r in records["references"].values())


@pytest.mark.asyncio
async def test_submission_cannot_bypass_reference_refresh(material_shot, monkeypatch):
    from app.api.projects import submit_shot_endpoint
    from app.core.projects.models import ShotStatus
    from app.core.projects.store import save_shot
    from fastapi import HTTPException
    _, shot, _, _ = material_shot
    shot = shot.model_copy(update={"status": ShotStatus.approved, "meta": {**shot.meta, "material_review_pending": False}})
    save_shot(shot)
    class StaleRefresher:
        calls = 0
        async def write_prompts_after_layout(self, id):
            self.calls += 1
            return shot
    refresher = StaleRefresher()
    def forbidden_job(**kwargs):
        pytest.fail("Stale reference evidence must be rejected before job creation")
    monkeypatch.setattr("app.api.projects.create_job", forbidden_job)
    with pytest.raises(HTTPException) as error:
        await submit_shot_endpoint(shot.id, svc=refresher, options=None)
    assert refresher.calls == 1
    assert "reference_facts_stale" in str(error.value.detail)


@pytest.mark.asyncio
async def test_directing_change_refreshes_sources_and_prevents_old_observation_reuse(material_shot):
    from app.agents.director.service import DirectorService
    from app.agents.director.brief import remember_directing_request
    from app.core.projects.store import project_dir
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    await svc.write_prompts_after_layout(shot.id)
    remember_directing_request(project.id, "Use the new violet design, not the earlier silver one.")
    await svc.write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 18
    ledger = json.loads((project_dir(project.id) / "agent/reference_facts.json").read_text(encoding="utf-8"))
    assert any(s["kind"] == "user_directing_request" and "violet" in s["text"]
        for r in ledger["references"].values() for s in r["sources"])


@pytest.mark.asyncio
async def test_submission_checks_the_actual_staged_bytes(material_shot, monkeypatch):
    from app.agents.director.service import DirectorService
    from app.api import projects as api
    from app.core.projects.store import save_shot
    from app.core.projects.models import ShotStatus
    from fastapi import HTTPException
    _, shot, _, _ = material_shot
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    updated = await svc.write_prompts_after_layout(shot.id)
    save_shot(updated.model_copy(update={"status": ShotStatus.approved}))
    collect = api._collect_h3_images
    def changed_bytes(shot):
        images = collect(shot)
        key = next(iter(images))
        images[key] = (images[key][0], b"unreviewed-input-bytes")
        return images
    def forbidden_job(**kwargs):
        pytest.fail("Unreviewed bytes must not reach job creation")
    monkeypatch.setattr(api, "_collect_h3_images", changed_bytes)
    monkeypatch.setattr(api, "create_job", forbidden_job)
    with pytest.raises(HTTPException) as error:
        await api.submit_shot_endpoint(shot.id, svc=svc, options=None)
    assert "reference_input_changed" in str(error.value.detail)


@pytest.mark.asyncio
async def test_cached_observation_with_invalid_authority_is_reobserved(material_shot):
    from app.agents.director.material_review import capture_references, observe_references_cached
    from app.core.projects.store import project_dir
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    provider = Vision(observation(), observation("Fresh observation."))
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    cache = next((project_dir(project.id) / "agent/reference_observations").glob("*.json"))
    cached = json.loads(cache.read_text(encoding="utf-8"))
    cached["facts"] = [dict(attribute="wardrobe", value="invented", visibility="observed",
        evidence="fabric", source_id="nonexistent", source_quote="approved")]
    cache.write_text(json.dumps(cached), encoding="utf-8")
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert len(provider.calls) == 2
    assert result[0]["description"] == "Fresh observation."


def test_repair_key_changes_with_reference_policy_and_reviewed_evidence(material_shot, monkeypatch):
    from app.agents.director import reference_facts
    from app.agents.director.prompt_repair import repair_key, load_repair, save_repair
    project, shot, _, _ = material_shot
    old = repair_key(project, shot, "same-images", "", "same-model")
    save_repair(shot, old, '{"summary":"outdated appearance"}', "invalid dialogue")
    monkeypatch.setattr(reference_facts, "REFERENCE_POLICY_VERSION", 999)
    current = repair_key(project, shot, "same-images", "", "same-model")
    assert current != old
    assert load_repair(shot, current) is None
    first = repair_key(project, shot, "same-images", "", "same-model", reference_evidence=[{"description": "old"}])
    second = repair_key(project, shot, "same-images", "", "same-model", reference_evidence=[{"description": "corrected"}])
    assert first != second


@pytest.mark.asyncio
async def test_conflicts_and_existing_uncertainties_remain_schema_valid():
    from app.agents.director.material_review import ReferenceObservation
    answer = conflict_observation()
    answer["uncertainties"] = [dict(attribute=f"hidden-{i}", quote="Not visible", reason="Cropped") for i in range(8)]
    result = await observe_reference(Vision(answer, answer), {}, "image")
    assert len(ReferenceObservation.model_validate(result).uncertainties) == 9


@pytest.mark.asyncio
async def test_real_quote_does_not_promote_a_model_interpretation():
    answer = observation("Orange trousers.")
    answer["facts"] = [dict(attribute="wardrobe", value="orange trousers", visibility="observed",
        evidence="Visible cloth", source_id="request", source_quote="Use the violet jumpsuit.")]
    result = await observe_reference(Vision(answer), {"sources": [dict(id="request",
        kind="user_directing_request", text="Use the violet jumpsuit.")]}, "image")
    assert result["facts"][0]["source_kind"] == "model_observation"
    assert result["facts"][0]["cited_source_kind"] == "user_directing_request"


@pytest.mark.asyncio
async def test_numeric_visible_value_is_losslessly_normalized_without_retry():
    answer = observation("One subject shown in three views.")
    answer["facts"] = [dict(attribute="subject count", value=1, visibility="observed",
        evidence="Same subject across the sheet.")]
    provider = Vision(answer)
    result = await observe_reference(provider, {}, "image")
    assert result["facts"][0]["value"] == "1"
    assert len(provider.calls) == 1
