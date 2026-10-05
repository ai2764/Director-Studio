"""Verify the actual writer request, not generated prose or model intent keywords."""
import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.director.writer_context import complete_writer_prompt


MARKER = "\nVideo continuation input (backend-owned observation):\n"
PNG = b"\x89PNG\r\n\x1a\nsource-tail"


def observation(mode="previous_shot", **updates):
    return {
        "mode": mode, "source_shot_id": "sht_source", "source_job_id": "job_source",
        "context_frames": 39, "carry_audio": False, "role": "observation_only",
        "picture_slots": [], "audio_slots": [], "tail_frame_png": PNG,
        **updates,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["previous_shot", "external_upload"])
async def test_writer_labels_actual_tail_as_video_observation_without_creating_reference_slots(mode):
    provider = SimpleNamespace(complete_with_images=AsyncMock(return_value="draft"))
    data = observation(mode, filename="private-song.mp4", local_path="C:/private/video.mp4")
    await complete_writer_prompt(provider, "system", "saved shot and Audio 1", data)
    system, user = provider.complete_with_images.call_args.args
    assert MARKER in user
    packet = json.loads(user.split(MARKER, 1)[1])
    assert packet["mode"] == mode
    assert packet["conditioning"] == "finished_video_motion_context"
    assert packet["tail_frame_image_index"] == 1
    assert packet["tail_frame_role"] == "source_video_ending_observation"
    assert packet["context_frames"] == 39
    assert packet["carry_audio"] is False
    assert packet["picture_slots"] == packet["audio_slots"] == []
    assert "private-song" not in user and "C:/private" not in user
    assert "saved shot and Audio 1" in user
    assert "camera path" in system and "carry_audio=false" in system
    assert provider.complete_with_images.call_args.kwargs["images"] == [base64.b64encode(PNG).decode()]
    assert data["tail_frame_png"] == PNG  # Request projection does not edit source state.


@pytest.mark.asyncio
async def test_text_only_writer_marks_the_tail_unseen_and_keeps_runtime_metadata():
    provider = SimpleNamespace(complete=AsyncMock(return_value="draft"))
    await complete_writer_prompt(provider, "system", "saved shot", observation(carry_audio=True))
    system, user = provider.complete.call_args.args
    assert MARKER in user
    packet = json.loads(user.split(MARKER, 1)[1])
    assert packet["tail_frame_image_index"] is None
    assert packet["tail_frame_status"] == "not_attached"
    assert packet["carry_audio"] is True
    assert "Do not invent" in system


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, observation("off")])
async def test_off_writer_keeps_ordinary_request_and_does_not_attach_old_tail(data):
    provider = SimpleNamespace(complete=AsyncMock(return_value="ordinary"),
                               complete_with_images=AsyncMock(return_value="wrong"))
    assert await complete_writer_prompt(provider, "system", "saved shot", data) == "ordinary"
    assert provider.complete.call_args.args == ("system", "saved shot")
    provider.complete_with_images.assert_not_called()


@pytest.mark.asyncio
async def test_bounded_tail_writer_attaches_video_observation_and_preserves_schema_and_limit():
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.agents.director.tail_prompt_review import complete_bounded
    client = SimpleNamespace(chat_response=AsyncMock(return_value={"content": "{}"}))
    provider = DirectorLLMPlanProvider(provider=SimpleNamespace(client=client), model="local-vision")
    schema = {"type": "object"}
    result = await complete_bounded(provider, "review", "candidate", max_tokens=1024,
                                    schema=schema, observation=observation())
    assert result == "{}"
    call = client.chat_response.call_args.kwargs
    assert call["require_vision"] is True
    assert call["format"] == schema
    assert call["options"]["num_predict"] == 1024
    assert call["messages"][0]["images"] == [base64.b64encode(PNG).decode()]
    assert MARKER in call["messages"][0]["content"]


def test_old_active_prompt_stamp_is_invalidated_but_off_stamp_is_unchanged(monkeypatch, tmp_path):
    from app.core.projects import video_context as module
    from app.core.projects.models import ShotVideoContext
    from app.core.projects.store import load_shot, save_shot
    from test_video_context_sources import _board, _succeed
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    module.configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    shot = load_shot(project.id, second.id)
    legacy = shot.video_context.model_dump(mode="json")
    legacy["resolved_source"] = {"job_id": job.id, "output_key": "video",
                                  "sha256": hashlib.sha256(b"source-video").hexdigest()}
    legacy["runtime"] = module._runtime_options(shot.video_context)
    stamp = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()[:16]
    assert module.video_context_prompt_is_stale(shot.model_copy(update={
        "meta": {"prompt_video_context_signature": stamp}}))
    off_stamp = hashlib.sha256(b'{"mode":"off"}').hexdigest()[:16]
    assert not module.video_context_prompt_is_stale(second.model_copy(update={
        "meta": {"prompt_video_context_signature": off_stamp}}))


@pytest.mark.asyncio
async def test_active_video_without_a_tail_layout_uses_reviewed_handoff_writer(monkeypatch, tmp_path):
    from app.agents.director.service import DirectorService
    from app.core.projects.models import ShotVideoContext
    from app.core.projects.store import load_shot, save_shot
    from app.core.projects.video_context import configure_video_context
    from test_video_context_sources import _board, _succeed
    from test_director_material_review import Orchestrator
    project, first, second = _board(monkeypatch, tmp_path)
    source = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": source.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    calls = []

    class Service(DirectorService):
        async def _write_tail_prompt(self, shot, *args, **kwargs):
            calls.append(shot)
            return shot

    result = await Service(plan_provider=object(), orchestrator=Orchestrator()).write_prompts_after_layout(second.id)
    assert calls and calls[0].video_context.mode == "previous_shot"
    assert not result.layout_refs  # Motion Context does not create a Picture/temporary Layout.
    assert result.refs == load_shot(project.id, second.id).refs
