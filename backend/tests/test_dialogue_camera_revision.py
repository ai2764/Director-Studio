"""Dialogue evidence survives prompt invalidation, without freezing creative direction."""
import json

import pytest

from app.agents.director.dialogue_preflight import prepare_dialogue
from app.agents.director.prompt_retry import authored_payload, record_prompt_failure, run_prompt_retry
from app.agents.director.service import DirectorService
from app.core.projects.dialogue import apply_dialogue_update, verify_dialogue_sources
from app.core.projects.chat_history import append_chat_message
from app.core.projects.models import PromptSections
from app.core.projects.store import load_shot, save_shot
from test_dialogue_authoring_boundary import call_tool
from test_director_material_review import material_shot, Orchestrator, Provider, sections
from test_legacy_dialogue_recovery import legacy_shot, MESSAGE, WORDS, response, SourceReview


COVERAGE = ("正反打：男主面部特写说：" + WORDS[0] + "切到kira急忙抬头说：" + WORDS[1]
            + "切回男主微笑，kira低头轻声害羞说：" + WORDS[2])


class RecoveryProvider(SourceReview, Provider):
    def __init__(self, orch):
        super().__init__(orch)
        self.extractions = 0

    async def complete(self, system, user, **kwargs):
        if "Extract the current shot's dialogue attribution" in system:
            self.extractions += 1
            request = json.JSONDecoder().raw_decode(user)[0]
            assert request["script"] == MESSAGE
            return json.dumps(response(MESSAGE, request["scene_id"]))
        if "reference review decision" in system.lower():
            return await super().complete(system, user, **kwargs)
        payload = sections()
        payload["detailed_description"] = "0-6 seconds: {{speech:l0}} {{speech:l1}} {{speech:l2}}"
        return json.dumps({"prompt_sections": payload})


@pytest.mark.asyncio
async def test_retry_then_camera_edit_then_retry_preserves_verified_speakers(material_shot):
    project, shot, neighbor = legacy_shot(material_shot)
    source = append_chat_message(project.id, role="user", content=MESSAGE)
    orch = Orchestrator()
    provider = RecoveryProvider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await run_prompt_retry(project.id, record_prompt_failure(shot, "", ValueError("source")), svc)
    first_lines = first.meta["prompt_dialogue_contract"]["lines"]
    await call_tool(project, svc, "revise_shot", {"shot_id": shot.id,
        "script_beat": COVERAGE, "composition": "Reverse angles and reaction close-ups."}, "shot3正反打")
    revised = load_shot(project.id, shot.id)
    assert revised.prompt_sections == PromptSections()
    assert "prompt_dialogue_contract" not in revised.meta
    assert revised.meta["dialogue_grounding"]["lines"] == first_lines
    saved = await run_prompt_retry(project.id, record_prompt_failure(revised, "", ValueError("source")), svc)
    assert authored_payload(saved) == authored_payload(revised)
    assert saved.dialogue_lines is None  # Derived evidence must not invent an authored revision.
    assert load_shot(project.id, neighbor.id) == neighbor
    lines = saved.meta["prompt_dialogue_contract"]["lines"]
    assert [line["speaker_id"] for line in lines] == ["male_lead", "char_kira", "char_kira"]
    assert all(line["source"]["source_id"] == source.id for line in lines)
    assert all(saved.prompt_sections.detailed_description.count(text) == 1 for text in WORDS)
    assert provider.extractions == 1  # A camera edit should review, not re-invent attribution.


@pytest.mark.asyncio
async def test_retry_recovers_already_revised_legacy_beat_without_rewriting_it(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    shot = shot.model_copy(update={"script_beat": COVERAGE})
    save_shot(shot)
    orch = Orchestrator()
    saved = await run_prompt_retry(project.id, record_prompt_failure(shot, "", ValueError("source")),
                                  DirectorService(plan_provider=RecoveryProvider(orch), orchestrator=orch))
    assert authored_payload(saved) == authored_payload(shot)
    assert [line["speaker_id"] for line in saved.meta["prompt_dialogue_contract"]["lines"]] == [
        "male_lead", "char_kira", "char_kira"]


@pytest.mark.asyncio
@pytest.mark.parametrize("old_prompt_only_record", [False, True])
async def test_camera_only_edit_can_reuse_evidence_after_prompt_was_cleared(material_shot, old_prompt_only_record):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    orch = Orchestrator()
    svc = DirectorService(plan_provider=RecoveryProvider(orch), orchestrator=orch)
    saved = await run_prompt_retry(project.id, record_prompt_failure(shot, "", ValueError("source")), svc)
    if old_prompt_only_record:
        saved.meta.pop("dialogue_grounding", None)
    revised = apply_dialogue_update(saved, {"camera_motion": "slow dolly", "prompt_sections": PromptSections()})
    class NoInference:
        async def complete(self, *args, **kwargs):
            raise AssertionError("Same evidence needs no inference")
    lines = await prepare_dialogue(project, revised, NoInference())
    assert [line.speaker_id for line in lines] == ["male_lead", "char_kira", "char_kira"]
    verify_dialogue_sources(project, revised, lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("audit", ['{"valid": false, "issues": []}', 'not a review'])
async def test_legacy_source_requires_successful_semantic_review(material_shot, audit):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    revised = shot.model_copy(update={"script_beat": COVERAGE})
    class RejectReview(RecoveryProvider):
        async def complete_bounded(self, *args, **kwargs):
            return audit
    with pytest.raises(ValueError, match="dialogue_(speaker_unresolved|attribution_review_invalid)"):
        await prepare_dialogue(project, revised, RejectReview(Orchestrator()))
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_changed_speaker_direction_cannot_silently_reuse_old_evidence(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    orch = Orchestrator()
    saved = await run_prompt_retry(project.id, record_prompt_failure(shot, "", ValueError("source")),
                                  DirectorService(plan_provider=RecoveryProvider(orch), orchestrator=orch))
    changed = apply_dialogue_update(saved, {"script_beat": "The male lead now speaks all three lines.",
                                           "prompt_sections": PromptSections()})
    class RejectChangedSpeaker:
        async def complete_bounded(self, system, user, **kwargs):
            assert "The male lead now speaks all three lines." in user
            return json.dumps({"valid": False, "issues": [{"code": "dialogue_speaker_unresolved",
                "action": "Explicitly revise dialogue_lines for the requested new attribution."}]})
        async def complete(self, *args, **kwargs):
            raise AssertionError("Do not silently re-extract different speakers")
    with pytest.raises(ValueError, match="dialogue_speaker_unresolved"):
        await prepare_dialogue(project, changed, RejectChangedSpeaker())


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [{"dialogue": ["New words."]}, {"scene_id": "another-scene"}])
async def test_dialogue_or_scene_changes_discard_derived_evidence(material_shot, updates):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    orch = Orchestrator()
    saved = await run_prompt_retry(project.id, record_prompt_failure(shot, "", ValueError("source")),
                                  DirectorService(plan_provider=RecoveryProvider(orch), orchestrator=orch))
    assert saved.meta.get("dialogue_grounding")
    revised = apply_dialogue_update(saved, updates)
    assert "dialogue_grounding" not in revised.meta


@pytest.mark.asyncio
async def test_language_edit_and_speaker_discovery_use_preserved_evidence(material_shot):
    from app.agents.director.dialogue_authoring import known_speakers
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    orch = Orchestrator()
    svc = DirectorService(plan_provider=RecoveryProvider(orch), orchestrator=orch)
    await run_prompt_retry(project.id, record_prompt_failure(shot, "", ValueError("source")), svc)
    await call_tool(project, svc, "revise_shot", {"shot_id": shot.id, "camera_motion": "slow dolly"}, "Slow dolly")
    assert {s["speaker_id"] for s in known_speakers(project)} >= {"male_lead", "char_kira"}
    await call_tool(project, svc, "revise_shot", {"shot_id": shot.id,
        "dialogue_language_updates": [{"line_id": "l0", "language": "Chinese"}]}, "Set the first line to Chinese")
    saved = load_shot(project.id, shot.id)
    assert saved.dialogue == WORDS
    assert saved.dialogue_lines[0].language == "Chinese"
    assert [line.speaker_id for line in saved.dialogue_lines] == ["male_lead", "char_kira", "char_kira"]
