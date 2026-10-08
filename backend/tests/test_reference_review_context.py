"""Numbered directing feedback and visual corrections reach real review boundaries."""
import json

import pytest

from app.agents.director.service import DirectorService
from app.core.projects.models import Shot
from app.core.projects.store import save_project, save_shot
from test_director_material_review import material_shot, Orchestrator, Provider


@pytest.mark.asyncio
async def test_fresh_cached_keep_decision_skips_rewriting_a_valid_prompt(material_shot):
    _, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch, rewrite=False)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    assert len(provider.text) == 1
    second = await svc.write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 9
    assert len(provider.text) == 1
    assert second.prompt_sections == first.prompt_sections


def test_changed_music_submit_interval_invalidates_timing_review(material_shot):
    from app.core.projects.models import ShotMusicSegment
    from app.agents.director.reference_facts import reference_intent_signature
    _, shot, _, _ = material_shot
    shot.music_segment = ShotMusicSegment(core_start_s=1, core_end_s=5,
        submit_start_s=.5, submit_end_s=5.5, use_as_audio_reference=False)
    old = reference_intent_signature(shot)
    shot.music_segment = shot.music_segment.model_copy(update={"submit_end_s": 6.5})
    assert reference_intent_signature(shot) != old


@pytest.mark.asyncio
async def test_reviewer_and_writer_share_actual_music_submit_duration(material_shot):
    from app.core.projects.models import ProjectMusicMaster, ShotMusicSegment, AgentContext, ProjectMode
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import _script_hash
    from app.core.projects.store import load_shot
    project, shot, _, _ = material_shot
    project = project.model_copy(update={"mode": ProjectMode.mv, "music_master": ProjectMusicMaster(
        filename="song.wav", relative_path="music/master.wav", duration_s=30,
        content_sha256="a" * 64, source_format="wav")})
    shot.music_segment = ShotMusicSegment(core_start_s=1, core_end_s=5,
        submit_start_s=.5, submit_end_s=5.5, use_as_audio_reference=False)
    save_project(project)
    save_shot(shot)
    save_agent_context(project.id, AgentContext(project_id=project.id,
        script_hash=_script_hash(project.script_text), shot_summaries=[{"id": shot.id, "duration_s": 6}]))
    orch = Orchestrator()
    provider = Provider(orch)
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    review = json.loads(next(user for system, user in provider.text
                             if "reference review decision" in system.lower()))
    assert review["shot"]["duration_s"] == 5
    assert review["shot"]["storyboard_duration_s"] == 6
    writer = provider.text[-1][1]
    assert '"execution_duration_s": 5.0' in writer
    assert '"storyboard_duration_s": 6.0' in writer
    assert load_shot(project.id, shot.id).duration_s == 6


@pytest.mark.asyncio
async def test_current_visual_correction_reinspects_instead_of_reusing_old_labels(material_shot):
    _, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    await svc.write_prompts_after_layout(shot.id)
    correction = "This approved image has a hip-length jacket and no backpack. Reinspect those details."
    await svc.write_prompts_after_layout(shot.id, revision_request=correction)
    assert len(provider.visual) == 18
    assert all(correction in user for user, _ in provider.visual[9:])
    # Identical correction and image bytes can still share the bounded inspection.
    await svc.write_prompts_after_layout(shot.id, revision_request=correction)
    assert len(provider.visual) == 18


@pytest.mark.asyncio
async def test_reviewer_receives_saved_order_for_numbered_shot_requirements(material_shot):
    project, shot, _, _ = material_shot
    other = Shot(id="sht_sleeve_detail", project_id=project.id, scene_id="sleeve",
                 title="Single sleeve detail", script_beat="Sand lands on one sleeve.", duration_s=6)
    save_shot(other)
    save_project(project.model_copy(update={"shot_ids": [shot.id, other.id]}))
    orch = Orchestrator()
    provider = Provider(orch)
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(
        shot.id, revision_request="Shot 2 is the sleeve insert; retain Shot 1's full-body action.")
    request = json.loads(next(user for system, user in provider.text
                              if "reference review decision" in system.lower()))
    assert request["intent"]["current_shot"] == {"id": shot.id, "index": 1, "title": shot.title}
    assert request["intent"]["shot_order"] == [
        {"id": shot.id, "index": 1, "title": shot.title},
        {"id": other.id, "index": 2, "title": other.title},
    ]


@pytest.mark.asyncio
async def test_saved_numbered_review_is_stale_after_reordering_shots(material_shot):
    from app.agents.director.reference_facts import reference_contract_current
    from app.core.projects.store import load_project
    project, shot, _, _ = material_shot
    other = Shot(id="sht_second", project_id=project.id, scene_id="second", title="Second",
                 script_beat="A separate shot.", duration_s=6)
    save_shot(other)
    project = project.model_copy(update={"shot_ids": [shot.id, other.id]})
    save_project(project)
    orch = Orchestrator()
    result = await DirectorService(plan_provider=Provider(orch), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert reference_contract_current(project, result)
    save_project(project.model_copy(update={"shot_ids": [other.id, shot.id]}))
    assert not reference_contract_current(load_project(project.id), result)


@pytest.mark.asyncio
async def test_writer_receives_resolved_review_instead_of_only_stale_feedback(material_shot):
    _, shot, _, _ = material_shot
    reason = "The current request replaces the earlier touch action. Keep the approved wind-only sleeve."
    class ResolvedProvider(Provider):
        async def complete(self, system, user, **kwargs):
            result = json.loads(await super().complete(system, user, **kwargs))
            if "reference review decision" in system.lower():
                result["reason"] = reason
            return json.dumps(result)
    orch = Orchestrator()
    provider = ResolvedProvider(orch)
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    user = provider.text[-1][1]
    marker = "Reviewed reference suitability (current inputs; not story authoring):\n"
    assert marker in user
    review, _ = json.JSONDecoder().raw_decode(user.split(marker, 1)[1])
    assert review["reason"] == reason
    assert review["brief"] is None


@pytest.mark.asyncio
async def test_visual_correction_has_an_exact_citable_source(material_shot):
    from app.agents.director.material_review import capture_references, observe_references_cached
    _, shot, _, _ = material_shot
    correction = "腰包在腰前。"
    class CitingVision:
        async def complete_with_images(self, system, user, **kwargs):
            return json.dumps({"readable": True, "description": "A pouch at the front waist.",
                "concerns": [], "facts": [{"attribute": "pouch_position", "value": "front",
                    "visibility": "observed", "evidence": "Visible waist pouch.",
                    "source_id": "current_inspection_request", "source_quote": correction}]})
    records, images, _ = capture_references(shot)
    result = await observe_references_cached(CitingVision(), shot.project_id,
        records[:1], images[:1], lambda: None, inspection_request=correction)
    source = next(s for s in result[0]["sources"] if s["id"] == "current_inspection_request")
    assert source["text"] == correction
    assert source["kind"] == "prompt_revision_request"
    # The source citation does not turn a model observation into human approval.
    assert result[0]["facts"][0]["source_kind"] == "model_observation"
