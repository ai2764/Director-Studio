"""Exercise storage and bounded review; only model inference is scripted."""
import json

import pytest
from test_director_material_review import Orchestrator

from app.agents.director.service import DirectorService
from app.core.projects.models import ShotStatus
from app.core.projects.store import load_shot, save_shot


@pytest.fixture
def tail_handoff_shot(tmp_path, monkeypatch):
    from test_director_material_review import tail_handoff_shot as fixture
    return fixture.__wrapped__(tmp_path, monkeypatch)


def candidate(motion="Pull back and lower from the inherited medium view; then hold."):
    return {
        "shot_patch": {"camera_motion": motion},
        "reason": "Make the inherited framing compatible with the final low view.",
        "blocking_question": None,
        "prompt_sections": {
            "subject_definitions": "<Picture 1> defines the dancer and studio.",
            "summary": "The dancer sinks as the camera pulls back and lowers.",
            "retention_analysis": "Preserve the visible stance and studio geography.",
            "detailed_description": "0-2 seconds: From the inherited waist-up view, the camera pulls back and lowers as she bends. 2-6 seconds: She kneels and holds the pose.",
            "overall_soundscape": "Studio room tone.", "non_diegetic_music": "No music.",
        },
    }


def verdict(valid=True):
    return {"tail_opening": "Eye-level waist-up standing view.",
            "candidate_opening": "Eye-level waist-up standing view." if valid else "Locked low wide view.",
            "camera_path": "Camera pulls back and lowers." if valid else "No move.",
            "field_checks": {key: {"compatible": True, "evidence": "Fixture field agrees with the evidence."} for key in ("script_beat", "shot_type", "camera_angle", "camera_motion", "composition", "subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music")},
            "checks": {key: {"compatible": valid if key == "opening_alignment" else True,
                             "evidence": "Inherited and candidate view agree." if valid else "Opening lacks a camera transition."}
                       for key in ("opening_alignment", "transition_path", "reference_roles", "section_consistency")},
            "valid": valid, "issues": [] if valid else [
        "Opening is a low wide view although the inherited frame is waist-up; specify a camera transition."
    ], "blocking_question": None}


class Provider:
    model = "test-vision-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.visual = []
        self.text = []
        self.on_call = None

    async def complete_with_images(self, system, user, *, images, guides=()):
        self.visual.append(user)
        return json.dumps({"readable": True, "description": "Eye-level waist-up frontal view; dancer standing, arms down.", "concerns": []})

    async def complete(self, system, user, *, guides=()):
        self.text.append((system, user))
        if self.on_call:
            self.on_call(len(self.text))
        return json.dumps(self.responses.pop(0))


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_review", [False, True])
async def test_tail_writer_reports_live_phases_and_failures(tail_handoff_shot, fail_review):
    project, shot = tail_handoff_shot
    events = []
    async def progress(event): events.append(event)
    class LiveProvider(Provider):
        async def complete_with_images(self, *args, **kwargs):
            assert events[-1]["phase"] == "reference_observation" and events[-1]["state"] == "started"
            return await super().complete_with_images(*args, **kwargs)
        async def complete(self, system, user, **kwargs):
            phase = "tail_draft" if not self.text else "prompt_review"
            assert events[-1]["phase"] == phase and events[-1]["state"] == "started"
            if fail_review and self.text:
                raise RuntimeError("fixture review unavailable")
            return await super().complete(system, user, **kwargs)
    service = DirectorService(plan_provider=LiveProvider([candidate(), verdict()]), orchestrator=Orchestrator())
    if fail_review:
        with pytest.raises(RuntimeError, match="review unavailable"):
            await service.write_prompts_after_layout(shot.id, on_progress=progress)
    else:
        await service.write_prompts_after_layout(shot.id, on_progress=progress)
    states = {(e.get("phase"), e.get("state")) for e in events}
    assert ("tail_draft", "completed") in states
    assert ("prompt_review", "failed" if fail_review else "completed") in states


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["draft_json", "review_json", "continuity"])
async def test_tail_validation_failure_is_visible_after_model_returns(tail_handoff_shot, fault):
    _project, shot = tail_handoff_shot
    if fault == "draft_json": responses = [{"invalid": "draft"}, {"invalid": "draft"}]
    elif fault == "review_json": responses = [candidate(), {"invalid": "review"}, candidate(), {"invalid": "review"}]
    else: responses = [candidate(), verdict(False), candidate(), verdict(False)]
    events = []
    async def progress(event): events.append(event)
    with pytest.raises(ValueError):
        await DirectorService(plan_provider=Provider(responses), orchestrator=Orchestrator()).write_prompts_after_layout(
            shot.id, on_progress=progress)
    phase = "tail_draft" if fault == "draft_json" else "prompt_review"
    assert any(e.get("phase") == phase and e.get("state") == "failed" for e in events)


@pytest.mark.asyncio
async def test_tail_draft_and_independent_audit_receive_labelled_video_image(tail_handoff_shot, monkeypatch):
    import base64
    from app.agents.director import writer_context
    project, shot = tail_handoff_shot
    png = b"\x89PNG\r\n\x1a\nsource-ending"
    monkeypatch.setattr(writer_context, "video_context_writer_view", lambda _shot: {
        "mode": "previous_shot", "context_frames": 22, "carry_audio": False,
        "role": "observation_only", "tail_frame_png": png,
    })

    class VisualBoundedProvider(Provider):
        async def complete_bounded_with_images(self, system, user, *, images, max_tokens,
                                               guides=(), schema=None):
            self.text.append((system, user, images, max_tokens, schema))
            return json.dumps(self.responses.pop(0))

    provider = VisualBoundedProvider([{
        "framing": "Waist-up", "viewpoint": "Eye-level", "pose": "Standing, frontal",
        "visible_state": "Face and upper body visible", "not_visible": ["feet", "lower legs"]
    }, candidate(), verdict()])
    await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(provider.text) == 3
    draft_packet = json.loads(provider.text[1][1])
    audit_packet = json.loads(provider.text[2][1])
    assert draft_packet["source_ending_observation"] == audit_packet["source_ending_observation"]
    assert audit_packet["source_ending_observation"]["not_visible"] == ["feet", "lower legs"]
    neutral_packet = json.loads(provider.text[0][1])
    assert "candidate_prompt" not in neutral_packet and "references" not in neutral_packet
    for system, user, images, limit, schema in provider.text:
        packet = json.loads(user)["video_context_observation"]
        assert packet["tail_frame_image_index"] == 1
        assert packet["tail_frame_role"] == "source_video_ending_observation"
        assert packet["picture_slots"] == packet["audio_slots"] == []
        assert images == [base64.b64encode(png).decode()]
        assert schema and limit in {1024, 6144, 4096}
        assert "camera path" in system
    assert load_shot(project.id, shot.id).refs == shot.refs


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_video_only_handoff_cannot_publish_a_rejected_opening(tail_handoff_shot, monkeypatch, valid):
    from app.agents.director import writer_context
    from app.config import settings
    from app.core.projects.models import ShotVideoContext, RefRole
    from app.core.projects.store import save_project
    from app.core.projects.video_context import configure_video_context
    from app.core.jobs import list_jobs
    from test_video_context_sources import _media, _succeed
    project, shot = tail_handoff_shot
    monkeypatch.setattr(settings, "video_context_enabled", True)
    monkeypatch.setattr("app.core.projects.video_context.probe_video", lambda _: _media())
    first = shot.model_copy(update={"id": "sht_source", "refs": [], "layout_refs": []})
    source_job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": source_job.id}))
    shot = shot.model_copy(update={"layout_refs": [], "refs": [
        ref.model_copy(update={"role": RefRole.other}) for ref in shot.refs]})
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [first.id, shot.id]}))
    configure_video_context(project.id, shot.id, ShotVideoContext(mode="previous_shot"))
    monkeypatch.setattr(writer_context, "video_context_writer_view", lambda _shot: {
        "mode": "previous_shot", "context_frames": 22, "carry_audio": False,
        "tail_frame_png": b"\x89PNG\r\n\x1a\nsource-ending",
    })

    class VisualBoundedProvider(Provider):
        async def complete_bounded_with_images(self, system, user, **kwargs):
            self.text.append((system, user))
            return json.dumps(self.responses.pop(0))

    provider = VisualBoundedProvider([{
        "framing": "Waist-up", "viewpoint": "Eye-level", "pose": "Standing, frontal",
        "visible_state": "Face and upper body visible", "not_visible": ["feet", "lower legs"]
    }, *([candidate(), verdict(valid)] * (1 if valid else 2))])
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    before_jobs = {job.id for job in list_jobs(project_id=project.id)}
    if valid:
        result = await svc.write_prompts_after_layout(shot.id)
        assert result.meta["material_review"]["prompt_review"]["valid"]
    else:
        with pytest.raises(ValueError, match="continuity review"):
            await svc.write_prompts_after_layout(shot.id)
        assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
    assert not load_shot(project.id, shot.id).layout_refs
    assert load_shot(project.id, shot.id).refs == shot.refs
    assert {job.id for job in list_jobs(project_id=project.id)} == before_jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["keep", "drop_optional", "needs_decision"])
async def test_managed_tail_checks_actual_evidence_before_drafting(tail_handoff_shot, action):
    from app.agents.director.brief import remember_directing_request
    from app.agents.director.tail_prompt_review import CreativeQuestion
    from app.core.h3.errors import PromptFailureError

    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"layout_refs": [
        layout.model_copy(update={"feedback_source": "managed_run"}) for layout in shot.layout_refs]})
    save_shot(shot)
    remember_directing_request(project.id, "Use handheld VCR photography throughout.")
    provider = Provider([{"action": action, "reason": "Actual tail is a studio; next beat requires a daylight pier."},
                         candidate(), verdict()])
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    events = []
    async def progress(event): events.append(event)
    if action == "keep":
        await svc.write_prompts_after_layout(shot.id, on_progress=progress)
        assert len(provider.text) == 3
    else:
        with pytest.raises(CreativeQuestion if action == "needs_decision" else PromptFailureError) as error:
            await svc.write_prompts_after_layout(shot.id, on_progress=progress)
        assert sum(e.get("phase") == "tail_compatibility" and e.get("state") == "failed" for e in events) == 1
        if action == "drop_optional":
            assert error.value.failure_kind == "tail_incompatible"
        assert len(provider.text) == 1  # No rejected draft/repair loop for an incompatible source.
        stored = load_shot(project.id, shot.id)
        assert stored.prompt_sections == shot.prompt_sections
        assert stored.layout_refs == shot.layout_refs
    preflight = json.loads(provider.text[0][1])
    assert preflight["directing_requests"] == ["Use handheld VCR photography throughout."]
    assert preflight["references"][0]["description"].startswith("Eye-level waist-up")
    assert "original_shot" in preflight
    assert len(provider.visual) == 1


@pytest.mark.asyncio
async def test_conflicting_reviews_cannot_silently_drop_required_continuity(tail_handoff_shot):
    from app.agents.director.brief import remember_directing_request
    from app.agents.director.tail_prompt_review import CreativeQuestion
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"layout_refs": [
        layout.model_copy(update={"feedback_source": "managed_run"}) for layout in shot.layout_refs]})
    save_shot(shot)
    remember_directing_request(project.id, "Visibly continue the previous shot without a hard cut.")
    conflict = {**verdict(False), "failure_kind": "reference_conflict"}
    provider = Provider([{"action": "keep", "reason": "The carried camera view can continue."},
                         candidate(), conflict, candidate(), conflict])
    with pytest.raises(CreativeQuestion, match="decision"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(provider.text) == 3
    assert load_shot(project.id, shot.id).layout_refs == shot.layout_refs


@pytest.mark.asyncio
@pytest.mark.parametrize("has_contract_error", [True, False])
async def test_failure_kind_survives_tail_review(tail_handoff_shot, has_contract_error):
    _, shot = tail_handoff_shot
    rejected = candidate()
    if has_contract_error:
        rejected["prompt_sections"]["subject_definitions"] = "No Picture binding."
    review = {**verdict(False), "failure_kind": "reference_conflict"}
    provider = Provider([rejected, review, rejected, review])
    with pytest.raises(ValueError) as error:
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert getattr(error.value, "failure_kind", None) == ("contract" if has_contract_error else "tail_incompatible")


@pytest.mark.asyncio
async def test_request_and_candidate_reach_reviewer_before_atomic_save(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"camera_motion": "locked-off low angle", "h3_job_id": "old", "status": ShotStatus.succeeded})
    save_shot(shot)
    provider = Provider([candidate(), verdict()])
    def assert_not_saved(_):
        assert load_shot(project.id, shot.id).camera_motion == "locked-off low angle"
    provider.on_call = assert_not_saved
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(
        shot.id, revision_request="尾帧没用于过渡，请修改开场和运镜")
    assert all("尾帧没用于过渡" in user for _, user in provider.text)
    assert "Pull back and lower" in provider.text[1][1]
    audit = json.loads(provider.text[1][1])
    assert "tail_observations" in audit
    assert audit["original_shot"]["script_beat"] == shot.script_beat
    assert audit["references"][0]["description"].startswith("Eye-level waist-up")
    assert updated.camera_motion.startswith("Pull back")
    assert updated.prompt_sections.detailed_description.startswith("0-2 seconds")
    assert updated.h3_job_id is None
    assert "old" in updated.meta["superseded_h3_job_ids"]
    assert updated.refs == shot.refs
    assert updated.meta["prompt_revision_request"] == "尾帧没用于过渡，请修改开场和运镜"
    assert len(provider.text) == 2


@pytest.mark.asyncio
async def test_semantic_repair_is_rechecked_and_only_accepted_candidate_saved(tail_handoff_shot):
    project, shot = tail_handoff_shot
    provider = Provider([candidate("locked-off low angle"), verdict(False), candidate(), verdict()])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(provider.text) == 4
    assert "Opening is a low wide view" in provider.text[2][1]
    assert updated.camera_motion.startswith("Pull back")
    assert load_shot(project.id, shot.id) == updated


@pytest.mark.asyncio
async def test_one_repair_receives_both_binding_and_continuity_errors(tail_handoff_shot):
    _, shot = tail_handoff_shot
    invalid = candidate("locked-off low angle")
    invalid["prompt_sections"]["subject_definitions"] = "The dancer in the studio."
    provider = Provider([invalid, verdict(False), candidate(), verdict()])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "Picture 1" in provider.text[2][1]
    assert "Opening is a low wide view" in provider.text[2][1]
    assert len(provider.text) == 4
    assert updated.camera_motion.startswith("Pull back")


@pytest.mark.asyncio
async def test_tail_repair_explicitly_requests_corrections_and_lists_picture_contract(tail_handoff_shot):
    _, shot = tail_handoff_shot
    invalid = candidate()
    invalid["prompt_sections"]["subject_definitions"] = "The dancer in the studio."
    provider = Provider([invalid, verdict(), candidate(), verdict()])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)

    initial_system, initial_user = provider.text[0]
    repair_system, repair_user = provider.text[2]
    assert json.loads(initial_user)["required_picture_bindings"] == ["<Picture 1>"]
    assert "Return corrected fields in the same JSON envelope" in repair_system
    assert "A rejected candidate is a draft" in repair_system
    assert "Repair the listed defects" in repair_system
    assert "Return corrected fields in the same JSON envelope" not in initial_system
    assert json.loads(repair_user)["repair"]["rejected_candidate"]
    assert "<Picture 1>" in updated.prompt_sections.subject_definitions


@pytest.mark.asyncio
async def test_structured_picture_bindings_compile_before_validation_and_review(tail_handoff_shot):
    _, shot = tail_handoff_shot
    draft = candidate()
    draft["prompt_sections"]["subject_definitions"] = "The dancer in the studio."
    draft["picture_bindings"] = {"<Picture 1>": "defines the dancer's identity and the studio."}
    provider = Provider([draft, verdict()])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "<Picture 1> defines the dancer's identity and the studio." in updated.prompt_sections.subject_definitions
    reviewed = json.loads(provider.text[1][1])["candidate_prompt"]
    assert reviewed["subject_definitions"] == updated.prompt_sections.subject_definitions
    assert len(provider.text) == 2


def test_candidate_schema_requires_one_contribution_per_actual_picture():
    from app.agents.director.tail_prompt_review import candidate_schema
    schema = candidate_schema(required_picture_indices=[1, 2, 3, 4])
    assert "picture_bindings" in schema["required"]
    binding_schema = schema["properties"]["picture_bindings"]
    assert binding_schema["required"] == [f"<Picture {i}>" for i in range(1, 5)]
    assert binding_schema["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bindings", [{}, {"<Picture 2>": "defines an unsubmitted actor."}])
async def test_structured_bindings_cannot_omit_or_invent_picture_slots(tail_handoff_shot, bindings):
    project, shot = tail_handoff_shot
    draft = candidate()
    draft["picture_bindings"] = bindings
    provider = Provider([draft, draft])
    with pytest.raises(ValueError, match="picture_bindings must cover exactly"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    stored = load_shot(project.id, shot.id)
    assert stored.prompt_sections == shot.prompt_sections
    assert stored.refs == shot.refs


def test_accepted_review_cannot_hide_conflicting_viewpoint_motion():
    from app.agents.director.tail_prompt_review import PromptVerdict
    review = verdict()
    review["viewpoint_motion"] = {
        "camera_attached_to": "viewer", "viewer_path": "The viewer is led to the sofa.",
        "camera_path": "The camera remains fixed in place.", "compatible": False,
        "evidence": "The same POV body cannot walk while its camera stays fixed.",
    }
    with pytest.raises(ValueError, match="viewpoint motion"):
        PromptVerdict.model_validate(review)


@pytest.mark.asyncio
async def test_motion_claim_extraction_blocks_false_positive_full_review(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"script_beat": "The viewer is led across the room.",
        "camera_angle": "First-person POV, camera is the viewer."})
    save_shot(shot)
    bad = candidate("The camera stays fixed while the viewer is led across the room.")
    good = candidate("The POV camera follows the viewer across the room.")
    review = verdict()
    review["viewpoint_motion"] = {"camera_attached_to": "viewer", "viewer_path": "none",
        "camera_path": "fixed", "compatible": True, "evidence": "A mistaken positive full review."}
    observation = dict(camera_attachment="viewer", viewer_position_change="moving",
        camera_position_change="stationary", viewer_quote="viewer is led across the room",
        camera_quote="camera stays fixed")
    moving = {**observation, "camera_position_change": "moving", "camera_quote": "camera follows the viewer"}
    provider = Provider([bad, review, observation, good, review, moving])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "follows the viewer" in updated.camera_motion
    assert "viewpoint owner moves" in provider.text[3][1]
    assert len(provider.text) == 6


@pytest.mark.asyncio
async def test_motion_review_honors_explicit_action_revision_without_candidate_evidence(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"script_beat": "The viewer waits beside the display.",
        "camera_angle": "First-person POV, camera is the viewer."})
    save_shot(shot)
    revision = "Change the action: the viewer walks across the room; the POV camera moves with the viewer."
    draft = candidate("The POV camera moves across the room with the viewer.")
    draft["shot_patch"]["script_beat"] = "The viewer walks across the room."
    draft["prompt_sections"]["summary"] = "The viewer walks across the room."
    draft["prompt_sections"]["detailed_description"] = "From the inherited view, the viewer walks across the room with the attached POV camera."
    review = verdict()
    review["viewpoint_motion"] = {"camera_attached_to": "viewer", "viewer_path": "moving",
        "camera_path": "moving", "compatible": True, "evidence": "The revised action and attached camera travel together."}

    class ActionProvider(Provider):
        async def complete(self, system, user, **kwargs):
            packet = json.loads(user)
            if "requested_action" in packet:
                self.text.append((system, user))
                source = packet["requested_action"]
                revised = revision in source
                # Candidate additions cannot become independent request evidence.
                assert draft["shot_patch"]["script_beat"] not in source
                return json.dumps(dict(camera_attachment="viewer",
                    viewer_position_change="moving" if revised else "stationary",
                    camera_position_change="moving",
                    viewer_quote="viewer walks across the room" if revised else "viewer waits beside the display",
                    camera_quote="camera moves across the room"))
            return await super().complete(system, user, **kwargs)

    provider = ActionProvider([draft, review, draft, review])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(
        shot.id, revision_request=revision)
    assert len(provider.text) == 3
    motion = updated.meta["material_review"]["motion_claim_review"]
    assert motion["viewer_position_change"] == "moving"
    assert motion["viewer_quote"] in revision
    assert load_shot(project.id, shot.id) == updated


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["automatic", "managed", "camera_only"])
async def test_motion_review_cannot_replace_authored_action_without_user_revision(tail_handoff_shot, origin):
    from app.core.managed_runs.context import ManagedTurnScope, managed_turn_scope

    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"script_beat": "The viewer waits beside the display.",
        "camera_angle": "First-person POV, camera is the viewer."})
    save_shot(shot)
    revision = ("Change the action: the viewer walks across the room." if origin == "managed" else
                "Keep the viewer waiting; change only the camera framing." if origin == "camera_only" else "")
    draft = candidate("The POV camera moves across the room with the viewer.")
    draft["shot_patch"]["script_beat"] = "The viewer walks across the room."
    review = verdict()
    review["viewpoint_motion"] = {"camera_attached_to": "viewer", "viewer_path": "moving",
        "camera_path": "moving", "compatible": True, "evidence": "A mistaken positive broad review."}

    class AuthoredActionProvider(Provider):
        async def complete(self, system, user, **kwargs):
            packet = json.loads(user)
            if "requested_action" in packet:
                self.text.append((system, user))
                source = packet["requested_action"]
                assert shot.script_beat in source
                assert draft["shot_patch"]["script_beat"] not in source
                if origin == "camera_only":
                    assert revision in source
                    assert "camera-only or unrelated revision does not change" in system
                else:
                    assert source == shot.script_beat
                return json.dumps(dict(camera_attachment="viewer", viewer_position_change="stationary",
                    camera_position_change="moving", viewer_quote="viewer waits beside the display",
                    camera_quote="camera moves across the room"))
            return await super().complete(system, user, **kwargs)

    provider = AuthoredActionProvider([draft, review, draft, review])
    token = managed_turn_scope.set(ManagedTurnScope(project.id, "run_fixture", "event_fixture", shot.id)) if origin == "managed" else None
    try:
        with pytest.raises(ValueError, match="viewpoint owner moves differently"):
            await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(
                shot.id, revision_request=revision)
    finally:
        if token is not None:
            managed_turn_scope.reset(token)
    stored = load_shot(project.id, shot.id)
    assert stored.script_beat == shot.script_beat
    assert stored.prompt_sections == shot.prompt_sections


@pytest.mark.asyncio
async def test_motion_quote_recovery_rechecks_evidence_without_rewriting_prompt(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"script_beat": "The viewer waits beside the display.",
        "camera_angle": "First-person POV from the visitor."})
    save_shot(shot)
    draft = candidate("The camera remains stationary.")
    review = verdict()
    review["viewpoint_motion"] = {"camera_attached_to": "viewer", "viewer_path": "stationary",
        "camera_path": "stationary", "compatible": True, "evidence": "Both positions are fixed."}
    wrong = dict(camera_attachment="viewer", viewer_position_change="stationary",
        camera_position_change="stationary", viewer_quote=shot.camera_angle,
        camera_quote="camera remains stationary")
    corrected = {**wrong, "viewer_quote": "viewer waits beside the display"}
    provider = Provider([draft, review, wrong, corrected])

    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)

    assert len(provider.text) == 4
    recovery = json.loads(provider.text[3][1])
    assert recovery["requested_action"] == shot.script_beat
    assert "viewer_quote" in recovery["review_repair"]["error"]
    assert updated.meta["material_review"]["motion_claim_review"] == corrected
    assert load_shot(project.id, shot.id) == updated


@pytest.mark.asyncio
async def test_exhausted_motion_quote_recovery_is_review_failure_not_prompt_repair(tail_handoff_shot):
    from app.config import settings
    project, shot = tail_handoff_shot
    review = verdict()
    review["viewpoint_motion"] = {"camera_attached_to": "external", "viewer_path": "unspecified",
        "camera_path": "moving", "compatible": True, "evidence": "External camera follows the scene."}
    wrong = dict(camera_attachment="external", viewer_position_change="unspecified",
        camera_position_change="moving", viewer_quote="An invented action.",
        camera_quote="Pull back and lower")
    provider = Provider([candidate(), review, wrong, wrong])

    with pytest.raises(ValueError) as error:
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)

    assert getattr(error.value, "code", None) == "MOTION_REVIEW_INVALID"
    assert len(provider.text) == 4
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
    assert not (settings.projects_dir / project.id / "agent" / "prompt_drafts" / f"{shot.id}.json").exists()


@pytest.mark.asyncio
async def test_ordinary_continuation_review_retains_authored_placement(tail_handoff_shot):
    _, shot = tail_handoff_shot
    shot = shot.model_copy(update={"script_beat": "A visitor sits beside the display."})
    save_shot(shot)
    provider = Provider([candidate(), verdict()])

    await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)

    audit = json.loads(provider.text[1][1])
    assert audit["original_shot"]["script_beat"] == shot.script_beat


@pytest.mark.asyncio
async def test_tail_prompt_reports_malformed_legacy_language_tag(tail_handoff_shot):
    project, shot = tail_handoff_shot
    from app.core.projects.dialogue import apply_dialogue_update
    from test_director_dialogue_attribution import line_payload
    shot = apply_dialogue_update(shot, {"dialogue_lines": [line_payload(text="Sure.")]})
    save_shot(shot)
    malformed = candidate()
    malformed["dialogue_uses"] = [{"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]
    malformed["prompt_sections"]["detailed_description"] = (
        "0-2 seconds: The dancer says <d>English Sure.</d> as the camera lowers. "
        "2-6 seconds: She completes the floor move."
    )
    provider = Provider([malformed, verdict(), malformed, verdict()])

    with pytest.raises(ValueError, match="dialogue_block_invalid.*detailed_description.*English Sure"):
        await DirectorService(
            plan_provider=provider,
            orchestrator=Orchestrator(),
        ).write_prompts_after_layout(shot.id)

    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
    assert len(provider.text) == 4
    repair = json.loads(provider.text[2][1])["repair"]
    assert repair["issues"][0]["actual"] == "<d>English Sure.</d>"


@pytest.mark.asyncio
async def test_tail_writer_repairs_attribution_without_editing_dialogue(tail_handoff_shot):
    from app.core.projects.dialogue import apply_dialogue_update
    from test_director_dialogue_attribution import line_payload
    project, shot = tail_handoff_shot
    shot = apply_dialogue_update(shot, {"dialogue_lines": [line_payload(text="Sure.")]})
    save_shot(shot)
    good = candidate()
    good["prompt_sections"]["detailed_description"] += " She says <d>[English] Sure.</d>"
    good["dialogue_uses"] = [{"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]
    bad = {**good, "dialogue_uses": [{"line_ids": ["l1"], "speaker_id": "other", "block_indexes": [0]}]}
    provider = Provider([bad, verdict(), good, verdict()])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert updated.dialogue_lines == shot.dialogue_lines
    assert "(S1) <d>" in updated.prompt_sections.detailed_description
    assert "S1 is Visitor" in updated.prompt_sections.subject_definitions
    assert "dialogue_speaker_mismatch" in provider.text[2][1]
    assert updated.meta["prompt_dialogue_contract"]["lines"][0]["speaker_id"] == "char_1"


@pytest.mark.asyncio
async def test_automatic_tail_rewrite_does_not_reuse_completed_revision_request(tail_handoff_shot):
    project, shot = tail_handoff_shot
    stale_request = "Replace the handheld VCR shot with a steady profile dolly."
    shot = shot.model_copy(update={"meta": {
        **shot.meta,
        "prompt_revision_request": stale_request,
        "prompt_revision_requests": [stale_request],
        "material_review_pending": False,
    }})
    save_shot(shot)
    provider = Provider([candidate(), verdict()])

    updated = await DirectorService(
        plan_provider=provider,
        orchestrator=Orchestrator(),
    ).write_prompts_after_layout(shot.id)

    assert all(stale_request not in user for _, user in provider.text)
    assert "prompt_revision_request" not in updated.meta
    assert "prompt_revision_requests" not in updated.meta
    assert len(provider.text) == 2


@pytest.mark.asyncio
async def test_exhausted_review_retains_draft_and_reuses_observations_after_restart(tail_handoff_shot):
    project, shot = tail_handoff_shot
    provider = Provider([candidate(), verdict(False), candidate(), verdict(False)])
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(ValueError, match="Opening is a low wide view"):
        await svc.write_prompts_after_layout(shot.id, revision_request="Keep continuity")
    stored = load_shot(project.id, shot.id)
    assert stored.prompt_sections == shot.prompt_sections
    assert stored.camera_motion == shot.camera_motion
    assert stored.meta["material_review_pending"]
    assert len(provider.text) == 4
    retried = Provider([candidate(), verdict()])
    await DirectorService(plan_provider=retried, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert not retried.visual
    assert "Keep continuity" in retried.text[0][1]
    assert json.loads(retried.text[0][1])["repair"]["rejected_candidate"]


@pytest.mark.asyncio
async def test_transport_timeout_stops_without_repair_and_keeps_diagnostics(tail_handoff_shot, monkeypatch):
    import asyncio

    from test_active_llm_routing import RecordingProvider

    from app.agents.director import llm_plan_provider as module
    from app.config import settings

    project, shot = tail_handoff_shot
    requests = []

    class SilentClient:
        async def chat_response(self, model, **kwargs):
            requests.append(kwargs)
            await asyncio.sleep(0.1)
            return {"content": "late", "finish_reason": "stop"}

    monkeypatch.setattr(module, "PROMPT_CALL_TIMEOUT_SEC", 0.01)
    provider = Provider([])
    provider.complete_bounded = module.DirectorLLMPlanProvider(
        provider=RecordingProvider(SilentClient())).complete_bounded
    with pytest.raises(TimeoutError, match="timed out"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    stored = load_shot(project.id, shot.id)
    assert stored.prompt_sections == shot.prompt_sections
    assert stored.camera_motion == shot.camera_motion
    assert stored.meta["material_review_pending"]
    assert len(requests) == 1
    diagnostics = list((settings.projects_dir / project.id / "agent" / "prompt_failures").glob("*.json"))
    assert len(diagnostics) == 1
    attempts = json.loads(diagnostics[0].read_text(encoding="utf-8"))["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["stage"] == "transport"
    assert "timed out" in attempts[0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated_stage", ["draft", "prompt_review", "motion_review"])
async def test_output_truncation_preserves_shot_and_does_not_restart_writer(tail_handoff_shot, truncated_stage):
    from app.core.prompt_errors import PromptOutputTruncated
    from app.config import settings
    project, shot = tail_handoff_shot

    class TruncatedProvider(Provider):
        async def complete_bounded(self, system, user, **kwargs):
            self.text.append((system, user))
            if truncated_stage != "draft" and len(self.text) == 1:
                return json.dumps(candidate())
            if truncated_stage == "motion_review" and len(self.text) == 2:
                review = verdict()
                review["viewpoint_motion"] = {"camera_attached_to": "external",
                    "viewer_path": "The performer bends.", "camera_path": "Pull back and lower.",
                    "compatible": True, "evidence": "Independent camera follows the authored action."}
                return json.dumps(review)
            raise PromptOutputTruncated("Output reached its requested limit")

    provider = TruncatedProvider([])
    with pytest.raises(PromptOutputTruncated):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(provider.text) == {"draft": 1, "prompt_review": 2, "motion_review": 3}[truncated_stage]
    stored = load_shot(project.id, shot.id)
    assert stored.prompt_sections == shot.prompt_sections
    assert stored.camera_motion == shot.camera_motion
    assert stored.script_beat == shot.script_beat
    diagnostics = list((settings.projects_dir / project.id / "agent" / "prompt_failures").glob("*.json"))
    assert len(diagnostics) == 1
    attempts = json.loads(diagnostics[0].read_text(encoding="utf-8"))["attempts"]
    assert attempts[-1]["stage"] == "output_budget"
    assert attempts[-1]["call_stage"] == truncated_stage
    assert not list((settings.projects_dir / project.id / "agent" / "prompt_drafts").glob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["shot", "directing_request"])
async def test_user_edit_during_audit_is_not_overwritten(tail_handoff_shot, change):
    from app.agents.director.brief import remember_directing_request
    project, shot = tail_handoff_shot
    provider = Provider([candidate(), verdict()])
    def edit(count):
        if count == 2:
            if change == "directing_request":
                remember_directing_request(project.id, "User chose a static medium shot")
            else:
                current = load_shot(project.id, shot.id)
                save_shot(current.model_copy(update={"camera_motion": "User chose a static medium shot"}))
    provider.on_call = edit
    with pytest.raises(ValueError, match="changed"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).camera_motion == (
        shot.camera_motion if change == "directing_request" else "User chose a static medium shot")


@pytest.mark.asyncio
async def test_reviewer_question_preserves_user_constraints_without_retry(tail_handoff_shot):
    project, shot = tail_handoff_shot
    provider = Provider([candidate(), {**verdict(False), "issues": [], "blocking_question": "Keep the fixed low camera or continue the previous framing?"}])
    with pytest.raises(ValueError, match="Keep the fixed low camera"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id, revision_request="Do not move the camera")
    assert len(provider.text) == 2
    assert load_shot(project.id, shot.id).camera_motion == shot.camera_motion


@pytest.mark.asyncio
async def test_harness_write_forwards_the_actual_user_correction(tail_handoff_shot):
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.service import _build_context
    project, shot = tail_handoff_shot
    save_agent_context(project.id, _build_context(project, [shot], phase="planned"))
    provider = Provider([candidate(), verdict()])
    request = "The last rewrite ignored the tail framing. Correct the opening camera."
    turn = BackendTurn(project.id, request, DirectorService(plan_provider=provider, orchestrator=Orchestrator()), None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"call_id": "rewrite", "name": "write_prompt", "arguments": {"shot_id": shot.id}})
    assert result["ok"], result
    assert all(request in user for _, user in provider.text)
    assert load_shot(project.id, shot.id).meta["prompt_revision_request"] == request


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["model", "image"])
async def test_visual_cache_invalidates_on_model_or_image_change(tail_handoff_shot, change):
    from PIL import Image

    from app.config import settings
    _, shot = tail_handoff_shot
    first = Provider([candidate(), verdict()])
    await DirectorService(plan_provider=first, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    second = Provider([candidate(), verdict()])
    if change == "model":
        second.model = "test-vision-v2"
    else:
        Image.new("RGB", (480, 640), "blue").save(settings.library_root / "layouts" / "lay_previous_tail" / "layout.png")
    await DirectorService(plan_provider=second, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(second.visual) == 1


@pytest.mark.asyncio
async def test_illegal_candidate_patch_cannot_change_references(tail_handoff_shot):
    project, shot = tail_handoff_shot
    invalid = candidate()
    invalid["shot_patch"]["refs"] = []
    provider = Provider([invalid, invalid])
    with pytest.raises(ValueError):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(provider.text) == 2  # invalid candidates do not reach the reviewer
    assert load_shot(project.id, shot.id).refs == shot.refs
