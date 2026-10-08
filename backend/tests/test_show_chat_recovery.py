"""Failures observed in show's numbered-shot edits and continuation replies."""
import json

import pytest

from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.service import DirectorService, _script_hash
from app.core.jobs.store import create_job, job_dir, save_job
from app.core.projects.models import Shot, ShotVideoContext
from app.core.projects.store import create_project, list_shots, save_project, save_shot
from app.core.schemas import JobStatus, OutputSlot
from test_harness_grounding_contract import Orchestrator, draft


def runway_board():
    project = create_project("show", "Walk forward, hold a pose, turn, then walk away.")
    shots = [Shot(id=f"sht_runway{i}", project_id=project.id, scene_id="runway",
                  title=title, script_beat=beat, duration_s=duration)
             for i, (title, beat, duration) in enumerate([
                 ("Walk in", "Walk forward.", 8), ("Pose", "Hold a pose.", 4),
                 ("Turn", "Turn to face away.", 3), ("Walk away", "Walk away.", 8)], 1)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [s.id for s in shots]}))
    return project, shots


def source_video(project, source):
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="Walk in",
                     project_id=project.id,
                     params={"shot_id": source.id, "width": 480, "height": 864})
    job.status = JobStatus.succeeded
    job.outputs = {"video": OutputSlot(key="video", label="Video", filename="video.mp4")}
    save_job(job)
    (job_dir(job.id) / "outputs/video.mp4").write_bytes(b"source artifact")
    save_shot(source.model_copy(update={"h3_job_id": job.id}))
    return job


def test_reply_can_cite_previous_video_exposed_in_current_project_context(tmp_projects_dir, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "video_context_enabled", True)
    project, shots = runway_board()
    job = source_video(project, shots[0])
    save_shot(shots[1].model_copy(update={"video_context": ShotVideoContext(
        mode="previous_shot", source_shot_id=shots[0].id)}))
    turn = BackendTurn(project.id, "shot2包含shot3就行不用去掉shot3", None, None)
    turn.context()
    reply = f"Shot 2 now includes the turn; continuing from {job.id}."
    assert turn.finish({"reply": reply}).reply == reply


def test_free_text_job_mention_is_not_a_current_state_receipt(tmp_projects_dir):
    job_project = create_project("show", "")
    job = create_job(pipeline_id="qwen21_layout", asset_kind="layouts", name="Old layout",
                     project_id=job_project.id)
    save_project(job_project.model_copy(update={"script_text": f"Consider {job.id}."}))
    turn = BackendTurn(job_project.id, "Tell me about it", None, None,
                       history=[{"role": "assistant", "content": f"Queued {job.id}."}])
    turn.context()
    assert job.id not in turn.finish({"reply": f"Queued {job.id}."}).reply


def test_other_project_job_still_cannot_be_cited(tmp_projects_dir):
    project, shots = runway_board()
    other, other_shots = runway_board()
    job = source_video(other, other_shots[0])
    # A corrupt persisted pointer must not override project ownership checks.
    save_shot(shots[1].model_copy(update={"video_context": ShotVideoContext(
        mode="previous_shot", source_shot_id=shots[0].id, source_job_id=job.id)}))
    turn = BackendTurn(project.id, "shot2", None, None)
    turn.context()
    assert job.id not in turn.finish({"reply": f"Continuing from {job.id}."}).reply


@pytest.mark.asyncio
async def test_merge_review_receives_original_numbering_and_action_evidence(tmp_projects_dir):
    project, original = runway_board()
    merged = [dict(draft(), shot_id=original[0].id, title="Walk in", script_beat="Walk forward."),
              dict(draft(), shot_id=original[1].id, title="Pose and turn", duration_s=7,
                   script_beat="Hold a pose, then turn to face away."),
              dict(draft(), shot_id=original[3].id, title="Walk away", script_beat="Walk away.")]

    class Reviewer:
        async def complete(self, system, user, **kwargs):
            marker = "SAVED STORYBOARD BEFORE THIS REVISION (original numbering):\n"
            if marker not in user:
                return '{"valid":false,"issues":["Original shot identities unavailable."]}'
            before, _ = json.JSONDecoder().raw_decode(user.split(marker, 1)[1])
            assert [(s["index"], s["id"], s["script_beat"]) for s in before] == [
                (1, "sht_runway1", "Walk forward."), (2, "sht_runway2", "Hold a pose."),
                (3, "sht_runway3", "Turn to face away."), (4, "sht_runway4", "Walk away.")]
            return '{"valid":true,"issues":[],"warnings":[]}'

    svc = DirectorService(plan_provider=Reviewer(), orchestrator=Orchestrator())
    saved = await svc.save_storyboard(project.id, merged, _script_hash(project.script_text),
                                     user_feedback="shot2和shot3合成一个")
    assert [s.title for s in saved] == ["Walk in", "Pose and turn", "Walk away"]
    assert saved[1].duration_s == 7
    assert saved[2].id == "sht_runway4"


class Responses:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, system, user, **kwargs):
        self.calls.append(user)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_malformed_verdict_is_retried_without_rewriting_candidate(tmp_projects_dir):
    project = create_project("show", "A seed rests.")
    provider = Responses('{"valid":true,"issues":["unterminated',
                         '{"valid":true,"issues":[],"warnings":["Motion may drift."]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    saved = await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    assert len(saved) == 1
    assert len(provider.calls) == 2
    assert provider.calls[1].startswith(provider.calls[0])


@pytest.mark.asyncio
async def test_exhausted_format_recovery_is_not_a_creative_rejection(tmp_projects_dir):
    project, original = runway_board()
    provider = Responses('{"valid":', '{"valid":', '{"valid":true,"issues":[]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    turn = BackendTurn(project.id, "Merge shots", svc, None)
    failed = await turn.tool({"name": "save_storyboard", "call_id": "merge",
                             "arguments": {"expected_script_hash": _script_hash(project.script_text),
                                           "shots": [draft()]}})
    assert len(provider.calls) == 2
    assert failed["code"] == "STORYBOARD_REVIEW_INVALID"
    assert failed["retryable"] is False
    assert not failed.get("issues")
    assert list_shots(project.id) == original
    assert turn.finish({"reply": "Saved."}).failure_code == "STORYBOARD_REVIEW_INVALID"
    assert not turn.context()["tools"]


@pytest.mark.asyncio
async def test_valid_rejection_is_not_retried(tmp_projects_dir):
    project = create_project("show", "A seed rests.")
    provider = Responses('{"valid":false,"issues":["The seed is missing."]}',
                         '{"valid":true,"issues":[]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(ValueError, match="The seed is missing"):
        await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    assert len(provider.calls) == 1
    assert not list_shots(project.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("Transport timed out"), RuntimeError("Unavailable")])
async def test_review_transport_failure_is_not_retried(tmp_projects_dir, failure):
    project = create_project("show", "A seed rests.")
    provider = Responses(failure, '{"valid":true,"issues":[]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(type(failure), match=str(failure)):
        await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    assert len(provider.calls) == 1
    assert not list_shots(project.id)


@pytest.mark.asyncio
async def test_local_reviewer_uses_structured_bounded_output(tmp_projects_dir):
    from jsonschema import Draft202012Validator
    project = create_project("show", "A seed rests.")

    class BoundedReviewer:
        async def complete_bounded(self, system, user, *, max_tokens, schema, guides):
            assert 512 <= max_tokens <= 4096
            validator = Draft202012Validator(schema)
            assert list(validator.iter_errors({"valid": "true", "issues": []}))
            assert list(validator.iter_errors({"valid": True, "issues": "none"}))
            verdict = {"valid": True, "issues": [], "warnings": []}
            validator.validate(verdict)
            return json.dumps(verdict)

        async def complete(self, *args, **kwargs):
            raise AssertionError("Unbounded inference bypasses the structured review contract")

    svc = DirectorService(plan_provider=BoundedReviewer(), orchestrator=Orchestrator())
    saved = await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_format_recovery_preserves_real_content_rejection(tmp_projects_dir):
    project = create_project("show", "A seed rests.")
    provider = Responses('{"valid":', '{"valid":false,"issues":["The seed is missing."]}',
                         '{"valid":true,"issues":[]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(ValueError, match="The seed is missing"):
        await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    assert len(provider.calls) == 2
    assert not list_shots(project.id)


@pytest.mark.asyncio
async def test_context_overflow_is_not_format_recovery(tmp_projects_dir):
    from app.core.prompt_errors import PromptContextOverflow
    project = create_project("show", "A seed rests.")
    provider = Responses(PromptContextOverflow("Too much input"), '{"valid":true,"issues":[]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(PromptContextOverflow):
        await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    assert len(provider.calls) == 1
    assert not list_shots(project.id)


@pytest.mark.asyncio
async def test_format_retry_cannot_overwrite_a_concurrent_storyboard_edit(tmp_projects_dir):
    project, original = runway_board()

    class EditingReviewer(Responses):
        async def complete(self, *args, **kwargs):
            if self.calls:
                save_shot(original[0].model_copy(update={"script_beat": "User changed this shot."}))
            return await super().complete(*args, **kwargs)

    provider = EditingReviewer('{"valid":', '{"valid":true,"issues":[]}')
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(ValueError, match="changed during candidate validation"):
        await svc.save_storyboard(project.id, [draft()], _script_hash(project.script_text))
    current = list_shots(project.id)
    assert len(current) == 4
    assert current[0].script_beat == "User changed this shot."
    assert current[1:] == original[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_real_adapter_truncation_enters_bounded_format_recovery(tmp_projects_dir, recovers):
    from types import SimpleNamespace
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.agents.director.storyboard_review import review_storyboard
    responses = [
        {"content": '{"valid":', "finish_reason": "length"},
        {"content": '{"valid":true,"issues":[]}', "finish_reason": "stop"} if recovers else
        {"content": '{"valid":', "finish_reason": "max_tokens"},
    ]

    class Client:
        async def chat_response(self, *args, **kwargs):
            return responses.pop(0)

    adapter = DirectorLLMPlanProvider(provider=SimpleNamespace(client=Client()), model="local-test")
    if recovers:
        verdict = await review_storyboard(adapter, "Review this candidate.")
        assert verdict.valid
    else:
        with pytest.raises(ValueError) as error:
            await review_storyboard(adapter, "Review this candidate.")
        assert getattr(error.value, "code", None) == "STORYBOARD_REVIEW_INVALID"
    assert not responses
