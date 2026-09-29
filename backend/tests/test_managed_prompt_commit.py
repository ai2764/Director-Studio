"""Reviewed prompt refinements must not invalidate their own managed run."""
import json

import pytest

from test_tail_prompt_review import Provider, candidate, verdict, tail_handoff_shot
from test_director_material_review import Orchestrator
from app.agents.director.service import DirectorService
from app.core.managed_runs.context import ManagedTurnScope, managed_turn_scope
from app.core.managed_runs.models import RunStep
from app.core.managed_runs import store
from app.core.projects.store import load_shot, save_shot, save_project, project_dir
from app.core.projects.models import Shot


@pytest.fixture
def managed_tail(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"layout_refs": [
        item.model_copy(update={"feedback_source": "managed_run"}) for item in shot.layout_refs]})
    save_shot(shot)
    draft = store.create_draft(project.id, [RunStep(shot_id=shot.id)])
    run = store.activate_run(project.id, draft.run_id, "portrait-720")
    token = managed_turn_scope.set(ManagedTurnScope(project.id, run.run_id, run.pending_event_id, shot.id))
    try:
        yield project, shot, run
    finally:
        managed_turn_scope.reset(token)


def writer(responses):
    provider = Provider([{"action": "keep", "reason": "Visible handoff is feasible."}, *responses])
    return DirectorService(plan_provider=provider, orchestrator=Orchestrator()), provider


@pytest.mark.asyncio
async def test_reviewed_camera_refinement_keeps_run_submittable(managed_tail):
    project, shot, run = managed_tail
    svc, _ = writer([candidate(), verdict()])
    updated = await svc.write_prompts_after_layout(shot.id)
    saved = store.load_run(project.id, run.run_id)
    assert updated.camera_motion.startswith("Pull back")
    assert saved.current_fingerprint == store._fingerprint(project.id)
    assert saved.plan_fingerprint == saved.current_fingerprint
    assert saved.pending_event_id == run.pending_event_id
    assert saved.recovery_history[-1]["kind"] == "prompt_refinement"
    assert saved.recovery_history[-1]["changes"]["camera_motion"]["before"] == shot.camera_motion
    bound = store.bind_job(project.id, run.run_id, shot.id, "job_fixture",
                           expected_fingerprint=saved.current_fingerprint,
                           expected_event_id=run.pending_event_id)
    assert bound.current_job_id == "job_fixture"


@pytest.mark.asyncio
async def test_story_change_is_retained_as_proposal_not_published(managed_tail):
    project, shot, run = managed_tail
    draft = candidate()
    draft["shot_patch"]["script_beat"] = "She abandons the scene and leaves."
    svc, _ = writer([draft, verdict()])
    with pytest.raises(ValueError, match="decision"):
        await svc.write_prompts_after_layout(shot.id)
    saved = load_shot(project.id, shot.id)
    assert saved.script_beat == shot.script_beat
    assert saved.camera_motion == shot.camera_motion
    assert saved.prompt_sections == shot.prompt_sections
    saved_run = store.load_run(project.id, run.run_id)
    assert saved_run.current_fingerprint == run.current_fingerprint
    diagnostic_dir = project_dir(project.id) / "agent" / "prompt_failures"
    diagnostics = [json.loads(p.read_text(encoding="utf-8")) for p in diagnostic_dir.glob("*.json")]
    assert any("She abandons the scene" in json.dumps(d) for d in diagnostics)


@pytest.mark.asyncio
async def test_external_project_edit_is_not_absorbed_into_refinement(managed_tail):
    project, shot, run = managed_tail
    svc, provider = writer([candidate(), verdict()])
    def change_project(call):
        if call == 3:
            # Different shot/order changes are outside the writer's target snapshot.
            other = Shot(id="sht_external", project_id=project.id, scene_id=shot.scene_id,
                         title="External edit", script_beat="Another scene.", duration_s=5)
            save_shot(other)
            save_project(project.model_copy(update={"shot_ids": [shot.id, other.id]}))
    provider.on_call = change_project
    with pytest.raises(ValueError, match="changed"):
        await svc.write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).camera_motion == shot.camera_motion
    assert store.load_run(project.id, run.run_id).current_fingerprint == run.current_fingerprint


@pytest.mark.asyncio
async def test_stopped_run_cannot_publish_reviewed_camera_change(managed_tail):
    project, shot, run = managed_tail
    svc, provider = writer([candidate(), verdict()])
    provider.on_call = lambda call: store.request_stop(project.id, run.run_id) if call == 3 else None
    with pytest.raises(ValueError, match="changed|stopped"):
        await svc.write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).camera_motion == shot.camera_motion


@pytest.mark.asyncio
async def test_run_write_failure_rolls_back_published_candidate(managed_tail, monkeypatch):
    project, shot, run = managed_tail
    svc, _ = writer([candidate(), verdict()])
    def fail_save(_):
        raise OSError("run disk failure")
    monkeypatch.setattr(store, "_save_run", fail_save)
    with pytest.raises(OSError, match="run disk failure"):
        await svc.write_prompts_after_layout(shot.id)
    saved = load_shot(project.id, shot.id)
    assert saved.camera_motion == shot.camera_motion
    assert saved.prompt_sections == shot.prompt_sections
    assert store.load_run(project.id, run.run_id).current_fingerprint == run.current_fingerprint


@pytest.mark.asyncio
async def test_managed_reviewer_sees_original_plan_and_execution_authority(managed_tail):
    _, shot, _ = managed_tail
    svc, provider = writer([candidate(), verdict()])
    await svc.write_prompts_after_layout(shot.id)
    audit = json.loads(provider.text[2][1])
    assert audit["original_shot"]["camera_motion"] == shot.camera_motion
    assert audit["managed_execution"]["refinable_fields"] == [
        "camera_angle", "camera_motion", "composition", "shot_type"]
    assert audit["managed_execution"]["request_origin"] == "coordinator"


@pytest.mark.asyncio
async def test_explicit_constraint_conflict_never_publishes_candidate(managed_tail):
    project, shot, run = managed_tail
    from app.agents.director.brief import remember_directing_request
    remember_directing_request(project.id, "Keep the camera completely fixed; no camera movement.")
    # User direction is part of the reviewed plan, not a concurrent edit.
    updated = store._fingerprint(project.id)
    store._save_run(run.model_copy(update={"plan_fingerprint": updated, "current_fingerprint": updated}))
    blocked = {**verdict(False), "blocking_question": "Fixed camera conflicts with the proposed push-in. Keep the cut?"}
    svc, provider = writer([candidate(), blocked])
    with pytest.raises(ValueError, match="decision"):
        await svc.write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).camera_motion == shot.camera_motion
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
    assert "completely fixed" in json.loads(provider.text[2][1])["directing_requests"][0]


@pytest.mark.asyncio
async def test_canonical_submit_can_refresh_camera_and_bind_its_new_version(managed_tail, monkeypatch):
    from app.api import projects as api
    from app.agents.director.tool_handlers.video import start_h3_video
    from app.core.jobs.store import load_job
    project, shot, run = managed_tail
    svc, _ = writer([candidate(), verdict()])
    async def no_gpu(job, *, images):
        assert images  # Keep real reference collection, prompt refresh and Job persistence.
        return job
    monkeypatch.setattr(api, "start_pipeline_job", no_gpu)
    result = await start_h3_video(project.id, shot.id, svc=svc)
    saved = store.load_run(project.id, run.run_id)
    assert saved.state == "active"
    assert saved.current_job_id == result["job_id"]
    assert saved.current_fingerprint == store._fingerprint(project.id)
    assert load_job(result["job_id"]).status.value == "queued"


@pytest.mark.asyncio
async def test_canonical_submit_does_not_absorb_external_change_after_refinement(managed_tail, monkeypatch):
    from app.api import projects as api
    from app.agents.director.tool_handlers.video import start_h3_video
    from app.core.jobs.store import load_job
    from app.core.projects.store import load_project
    project, shot, run = managed_tail
    svc, _ = writer([candidate(), verdict()])
    jobs = []
    async def external_edit(job, *, images):
        jobs.append(job.id)
        current_project = load_project(project.id)
        save_project(current_project.model_copy(update={"script_text": "Changed externally during submission."}))
        return job
    monkeypatch.setattr(api, "start_pipeline_job", external_edit)
    with pytest.raises(ValueError, match="changed"):
        await start_h3_video(project.id, shot.id, svc=svc)
    assert load_job(jobs[0]).status.value == "cancelled"
    assert store.load_run(project.id, run.run_id).state == "paused"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["kind", "shot_id", "event_id", "before_fingerprint", "after_fingerprint"])
async def test_bind_requires_exact_same_event_refinement_receipt(managed_tail, field):
    project, shot, run = managed_tail
    svc, _ = writer([candidate(), verdict()])
    await svc.write_prompts_after_layout(shot.id)
    saved = store.load_run(project.id, run.run_id)
    history = [*saved.recovery_history[:-1], {**saved.recovery_history[-1], field: "unrelated"}]
    store._save_run(saved.model_copy(update={"recovery_history": history}))
    with pytest.raises(ValueError, match="changed"):
        store.bind_job(project.id, run.run_id, shot.id, "job_unbound",
                       expected_event_id=run.pending_event_id,
                       expected_fingerprint=run.current_fingerprint,
                       allow_prompt_refinement=True)
    assert store.load_run(project.id, run.run_id).current_job_id is None
