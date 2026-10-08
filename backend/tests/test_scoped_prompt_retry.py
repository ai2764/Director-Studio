import json
import pytest

from app.agents.director.service import DirectorService
from app.core.projects.store import load_shot, save_shot
from test_director_material_review import material_shot, Orchestrator, Provider
from test_director_material_review import tail_handoff_shot


@pytest.mark.asyncio
async def test_retry_rejects_changed_saved_lyric_timing_before_inference(material_shot, monkeypatch):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    from test_director_material_review import music_review_inputs
    project, shot, document = music_review_inputs(material_shot, monkeypatch)
    receipt = record_prompt_failure(shot, "Sing softly", ValueError("invalid block"))
    document.segments[0].start_s = 0.5
    calls = []
    class Service:
        async def write_prompts_after_layout(self, *args, **kwargs):
            calls.append(args)
            return shot
    with pytest.raises(ValueError, match="changed"):
        await run_prompt_retry(project.id, receipt, Service())
    assert calls == []


@pytest.mark.asyncio
async def test_context_overflow_does_not_offer_or_execute_identical_retry(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, pending_prompt_retry, run_prompt_retry
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "Sing softly", RuntimeError(
        "request (79647 tokens) exceeds the available context size (65536 tokens)"))
    assert pending_prompt_retry(project.id) is None
    with pytest.raises(ValueError, match="blocked"):
        await run_prompt_retry(project.id, receipt, None)


@pytest.mark.asyncio
async def test_tail_writer_does_not_repair_a_context_overflow(tail_handoff_shot, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.agents.director import tail_prompt_review as writer
    from app.core.prompt_errors import PromptContextOverflow
    project, shot = tail_handoff_shot
    monkeypatch.setattr(writer, "observe_references_cached", AsyncMock(return_value=[]))
    monkeypatch.setattr(writer, "prepare_dialogue", AsyncMock(return_value=None))
    call = AsyncMock(side_effect=PromptContextOverflow("too many tokens"))
    monkeypatch.setattr(writer, "complete_bounded", call)
    with pytest.raises(PromptContextOverflow):
        await writer.draft_and_review(SimpleNamespace(model="local"), project, shot,
            [], [], "signature", lambda: None, lambda *args: None)
    assert call.await_count == 1


@pytest.mark.asyncio
async def test_retry_rejects_changed_inputs_before_inference(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "Keep the performance", ValueError("invalid block"))
    from app.core.projects.dialogue import apply_dialogue_update
    save_shot(apply_dialogue_update(shot, {"dialogue": ["Different words"]}))
    with pytest.raises(ValueError, match="changed"):
        await run_prompt_retry(project.id, receipt, None)
    assert load_shot(project.id, shot.id).dialogue == ["Different words"]


@pytest.mark.asyncio
async def test_retry_cannot_publish_an_authored_brief_change(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    project, shot, neighbor, _ = material_shot
    receipt = record_prompt_failure(shot, "Keep the performance", ValueError("invalid block"))
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch, brief="A different story beat"), orchestrator=orch)
    await run_prompt_retry(project.id, receipt, svc)
    current = load_shot(project.id, shot.id)
    assert current.script_beat == shot.script_beat
    assert current.prompt_sections == shot.prompt_sections
    assert current.dialogue == shot.dialogue
    assert load_shot(project.id, neighbor.id) == neighbor


@pytest.mark.asyncio
async def test_stream_retry_uses_capability_not_chat_agent(material_shot, monkeypatch):
    from app.agents.director.prompt_retry import record_prompt_failure
    from app.api import projects as api
    import app.agents.director.chat as chat
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "Keep the performance", ValueError("invalid block"))
    async def forbidden(*args, **kwargs):
        raise AssertionError("A retry must not expose arbitrary agent tools")
    monkeypatch.setattr(chat, "handle_chat", forbidden)
    monkeypatch.setattr(api, "_make_chat_fn", forbidden)
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    response = await api.project_chat_stream_endpoint(project.id,
        api.ChatBody(message="Retry prompt", prompt_retry=receipt), svc=svc)
    events = [json.loads(chunk.removeprefix("data: ")) async for chunk in response.body_iterator]
    assert not [event for event in events if event["type"] == "error"]
    assert events[0]["type"] == "status"
    assert shot.id in events[0]["text"]
    assert any(e.get("phase") == "reference_observation" for e in events)
    assert events[-1]["data"]["actions"] == [f"write_prompt:{shot.id}"]
    current = load_shot(project.id, shot.id)
    assert current.meta["material_review_pending"] is False
    assert current.script_beat == shot.script_beat


@pytest.mark.asyncio
async def test_retry_replay_does_not_call_provider_again(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "Keep the performance", ValueError("invalid block"))
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    saved = await run_prompt_retry(project.id, receipt, svc)
    replay = await run_prompt_retry(project.id, receipt, None)
    assert replay == saved


@pytest.mark.asyncio
async def test_retry_rejects_a_new_saved_prompt(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "", ValueError("invalid"))
    save_shot(shot.model_copy(update={"prompt_sections": shot.prompt_sections.model_copy(update={"summary": "User revision"})}))
    with pytest.raises(ValueError, match="changed"):
        await run_prompt_retry(project.id, receipt, None)


@pytest.mark.asyncio
async def test_retry_rejects_reference_file_replacement(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    from PIL import Image
    project, shot, _, files = material_shot
    receipt = record_prompt_failure(shot, "", ValueError("invalid"))
    Image.new("RGB", (100, 100), "blue").save(files[0])
    with pytest.raises(ValueError, match="changed"):
        await run_prompt_retry(project.id, receipt, None)


@pytest.mark.asyncio
async def test_retry_detects_project_change_during_inference(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    from app.core.projects.store import load_project, save_project
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "", ValueError("invalid"))
    def mutate(stage, count):
        if stage == "prompt":
            current = load_project(project.id)
            save_project(current.model_copy(update={"name": "New project direction"}))
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch, mutate=mutate), orchestrator=orch)
    with pytest.raises(ValueError, match="inputs changed"):
        await run_prompt_retry(project.id, receipt, svc)
    assert load_project(project.id).name == "New project direction"
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections


@pytest.mark.asyncio
async def test_retry_cancel_releases_same_operation_for_safe_retry(material_shot):
    import asyncio
    from app.agents.director.prompt_retry import record_prompt_failure, pending_prompt_retry, run_prompt_retry
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "", ValueError("invalid"))
    class CancelledService:
        async def write_prompts_after_layout(self, *args, **kwargs):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await run_prompt_retry(project.id, receipt, CancelledService())
    assert pending_prompt_retry(project.id) == receipt
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_ordinary_writer_compiles_source_reference(material_shot):
    from test_director_material_review import sections
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    class SpeechReferenceProvider(Provider):
        async def complete(self, system, user, **kwargs):
            if "reference review decision" in system.lower():
                return await super().complete(system, user, **kwargs)
            payload = sections()
            payload["detailed_description"] = "Camera closes in. She pauses... then says {{speech:l1}}"
            return json.dumps({"prompt_sections": payload})
    saved = await DirectorService(plan_provider=SpeechReferenceProvider(orch), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert "Camera closes in. She pauses..." in saved.prompt_sections.detailed_description
    assert "<d>[English] Hello.</d>" in saved.prompt_sections.detailed_description
    assert "{{speech" not in saved.prompt_sections.detailed_description
    assert saved.dialogue == shot.dialogue


def test_repeated_repair_has_no_retry_button(material_shot):
    from app.agents.director.prompt_repair import PromptRepairNoProgress
    from app.agents.director.prompt_retry import record_prompt_failure, pending_prompt_retry
    project, shot, _, _ = material_shot
    record_prompt_failure(shot, "", PromptRepairNoProgress("same candidate, same validation issues"))
    assert pending_prompt_retry(project.id) is None


def test_no_progress_ignores_json_formatting_but_allows_changed_candidate():
    from app.agents.director.prompt_repair import require_repair_progress, PromptRepairNoProgress
    previous = {"rejected_candidate": '{"summary":"old"}', "error": "missing speech"}
    with pytest.raises(PromptRepairNoProgress, match="same candidate"):
        require_repair_progress(previous, '{ "summary": "old" }', ValueError("missing speech"))
    require_repair_progress(previous, '{"summary":"new"}', ValueError("missing speech"))
    require_repair_progress(previous, '{"summary":"old"}', ValueError("different error"))


@pytest.mark.asyncio
async def test_failed_scoped_retry_returns_structured_assistant_result(material_shot):
    from app.agents.director.prompt_retry import record_prompt_failure
    from app.api.projects import _run_scoped_prompt_retry, _chat_result_to_response
    from app.agents.director.prompt_retry import PromptRetryRequest
    from app.core.projects.chat_history import append_chat_message, load_chat_history
    project, shot, _, _ = material_shot
    receipt = record_prompt_failure(shot, "", ValueError("invalid"))
    class FailedService:
        async def write_prompts_after_layout(self, *args, **kwargs):
            # Ordinary validators and schema extraction can raise untyped ValueError.
            raise ValueError("Missing Picture binding")
    result = await _run_scoped_prompt_retry(project.id, PromptRetryRequest(**receipt), FailedService())
    response = _chat_result_to_response(result)
    assert result.failure_code == "PROMPT_GENERATION_FAILED"
    assert response.prompt_retry.model_dump() == receipt
    append_chat_message(project.id, role="assistant", content=response.reply, prompt_retry=response.prompt_retry.model_dump())
    assert load_chat_history(project.id)[-1].prompt_retry == receipt


@pytest.mark.asyncio
async def test_retry_rejects_layout_sync_before_any_authored_save(tail_handoff_shot):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry
    project, shot = tail_handoff_shot
    receipt = record_prompt_failure(shot, "", ValueError("invalid"))
    with pytest.raises(ValueError, match="prompt_retry_scope"):
        await run_prompt_retry(project.id, receipt, DirectorService(plan_provider=None))
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_tail_scoped_retry_drops_cached_authored_patch(tail_handoff_shot):
    from test_tail_prompt_review import Provider as TailProvider, candidate, verdict
    from app.agents.director.prompt_retry import pending_prompt_retry, run_prompt_retry
    project, shot = tail_handoff_shot
    first = candidate("Orbit")
    first["prompt_sections"]["subject_definitions"] = "No picture binding"
    second = json.loads(json.dumps(first))
    second["prompt_sections"]["summary"] = "A changed draft, still missing its Picture binding"
    provider = TailProvider([first, verdict(), second, verdict()])
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    with pytest.raises(ValueError):
        await svc.write_prompts_after_layout(shot.id)
    current = load_shot(project.id, shot.id)
    receipt = pending_prompt_retry(project.id)
    assert receipt is not None
    provider.responses = [{"shot_patch": {}, "prompt_sections": {
        "subject_definitions": "<Picture 1> defines the dancer and studio."
    }}, verdict()]
    saved = await run_prompt_retry(project.id, receipt, svc)
    assert saved.camera_motion == current.camera_motion
    assert saved.script_beat == current.script_beat
    assert saved.refs == current.refs
    assert saved.prompt_sections.summary == second["prompt_sections"]["summary"]
    assert load_shot(project.id, shot.id) == saved
