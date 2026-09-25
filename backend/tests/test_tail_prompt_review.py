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
    assert "original_shot" not in audit
    assert "references" not in audit
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
async def test_tail_prompt_normalizes_unambiguous_english_dialogue_tag(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = shot.model_copy(update={"dialogue": ["Sure."]})
    save_shot(shot)
    malformed = candidate()
    malformed["prompt_sections"]["detailed_description"] = (
        "0-2 seconds: The dancer says <d>English Sure.</d> as the camera lowers. "
        "2-6 seconds: She completes the floor move."
    )
    provider = Provider([malformed, verdict()])

    updated = await DirectorService(
        plan_provider=provider,
        orchestrator=Orchestrator(),
    ).write_prompts_after_layout(shot.id)

    assert "<d>[English] Sure.</d>" in updated.prompt_sections.detailed_description
    assert load_shot(project.id, shot.id) == updated
    assert len(provider.text) == 2


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
async def test_user_edit_during_audit_is_not_overwritten(tail_handoff_shot):
    project, shot = tail_handoff_shot
    provider = Provider([candidate(), verdict()])
    def edit(count):
        if count == 2:
            current = load_shot(project.id, shot.id)
            save_shot(current.model_copy(update={"camera_motion": "User chose a static medium shot"}))
    provider.on_call = edit
    with pytest.raises(ValueError, match="changed"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).camera_motion == "User chose a static medium shot"


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
