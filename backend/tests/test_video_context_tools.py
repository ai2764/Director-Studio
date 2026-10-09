"""Director tool, writer, and fingerprint behavior for H3 video context.

These tests do not start Comfy or an LLM. Natural-language cases script the
model's tool calls and assert the saved trajectory.
"""
import base64
import json

import pytest

from app.agents.director.service import DirectorService
from app.agents.director.tool_handlers.video import handle_video_tool
from app.agents.director.tool_schema import director_tool_schemas, offered_tool_names
from app.config import settings
from app.core.jobs import list_jobs
from app.core.jobs.store import create_job, job_dir, load_job, save_job
from app.core.managed_runs.store import _fingerprint, _legacy_fingerprint, _project_fingerprint
from app.core.projects.models import ShotVideoContext
from app.core.projects.store import create_project, load_shot, save_shot
from app.core.projects.video_context import configure_video_context
from app.core.schemas import OutputSlot
from test_director_dialogue_attribution import Orchestrator, authored_shot, writer_sections
from test_video_context_sources import _board, _succeed


async def _call(name, project_id, args, message):
    payloads, actions, notes = [], [], []
    handled = await handle_video_tool(
        name=name,
        args=args,
        project_id=project_id,
        svc=object(),
        actions=actions,
        notes=notes,
        result_payloads=payloads,
        user_feedback=message,
    )
    return handled, payloads, actions, notes


@pytest.mark.asyncio
async def test_pending_continuation_blocks_writer_before_model_work(monkeypatch, tmp_path):
    project, _first, second = _board(monkeypatch, tmp_path)
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    before = load_shot(project.id, second.id).model_dump(mode="json")
    service = DirectorService(plan_provider=None, orchestrator=object())
    with pytest.raises(ValueError, match="Video continuation is not ready"):
        await service.write_prompts_after_layout(second.id)
    assert load_shot(project.id, second.id).model_dump(mode="json") == before
    assert list_jobs(project_id=project.id) == []


@pytest.mark.asyncio
async def test_unreadable_source_blocks_agent_status_and_writer(monkeypatch, tmp_path):
    from app.core.projects.video_context import VideoContextError, video_context_status
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    def unreadable(_path):
        raise VideoContextError("Source video cannot be decoded")
    monkeypatch.setattr("app.core.projects.video_context.probe_video", unreadable)
    saved = load_shot(project.id, second.id)
    assert video_context_status(saved)["ready"] is False
    from app.agents.director.tool_handlers.media import handle_media_tool
    payloads = []
    handled = await handle_media_tool(
        name="get_status", args={"shot_id": second.id}, project_id=project.id,
        project=project, shots=[load_shot(project.id, first.id), saved],
        runtime=None, actions=[], notes=[], touched=set(), result_payloads=payloads,
        images=None, user_feedback="",
    )
    assert handled is True
    status = payloads[-1]["video_context_status"]
    assert status["state"] == "blocked"
    before = saved.model_dump(mode="json")
    with pytest.raises(ValueError, match="Source video cannot be decoded"):
        await DirectorService(plan_provider=None, orchestrator=object()).write_prompts_after_layout(second.id)
    assert load_shot(project.id, second.id).model_dump(mode="json") == before
    assert len(list_jobs(project_id=project.id)) == 1


def test_configure_tool_is_offered_for_ordinary_and_lyric_messages():
    project = create_project("Offer", "Two shots")
    for message in ("第二镜接着上一镜往前推", "这两段歌词如何衔接", "hello"):
        names = offered_tool_names(director_tool_schemas(project, current_message=message))
        assert "configure_video_context" in names
    schema = next(
        tool["function"] for tool in director_tool_schemas(project)
        if tool["function"]["name"] == "configure_video_context"
    )
    assert schema["parameters"]["additionalProperties"] is False
    assert "path" not in schema["parameters"]["properties"]
    assert "filename" not in schema["parameters"]["properties"]
    assert "source_shot_id" in schema["parameters"]["properties"]
    assert "does not start generation" in schema["description"].lower()


@pytest.mark.asyncio
async def test_configure_saves_previous_shot_without_starting(monkeypatch, tmp_path):
    import app.agents.director.tool_handlers.video as video_tools

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id, b"source-video")
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))

    async def explode(*args, **kwargs):
        raise AssertionError("configure must not start a video")

    monkeypatch.setattr(video_tools, "start_h3_video", explode)
    handled, payloads, actions, notes = await _call(
        "configure_video_context", project.id,
        {"shot_id": second.id, "mode": "previous_shot", "path": r"C:\secret.mp4",
         "url": "http://example.invalid/a.mp4", "filename": "theme-song.mp4"},
        "第二镜接着上一镜往前推",
    )
    saved = load_shot(project.id, second.id)
    assert handled is True
    assert payloads[-1]["ok"] is True
    assert actions == [f"configure_video_context:{second.id}"]
    assert payloads[-1]["actions"] == actions
    assert payloads[-1]["source_job_id"] == job.id
    assert saved.video_context.mode == "previous_shot"
    assert saved.video_context.source_shot_id == first.id
    assert saved.video_context.source_job_id is None
    assert saved.video_context.upload_id is None
    assert "theme-song" not in json.dumps(payloads[-1])
    assert any("No video job was started" in note for note in notes)
    assert {item.id for item in list_jobs(project_id=project.id)} == {job.id}

    again, again_payloads, _, _ = await _call(
        "configure_video_context", project.id,
        {"shot_id": second.id, "mode": "previous_shot"},
        "第二镜接着上一镜往前推",
    )
    assert again is True and again_payloads[-1]["ok"] is True
    assert load_shot(project.id, second.id).video_context == saved.video_context


@pytest.mark.asyncio
async def test_configure_off_clears_a_saved_source(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    handled, payloads, actions, _notes = await _call(
        "configure_video_context", project.id,
        {"shot_id": second.id, "mode": "off"},
        "不要衔接，独立生成",
    )
    saved = load_shot(project.id, second.id)
    assert handled is True and payloads[-1]["ok"] is True
    assert actions == [f"configure_video_context:{second.id}"]
    assert saved.video_context.mode == "off"
    assert saved.video_context.source_shot_id is None
    assert saved.video_context.source_job_id is None
    assert saved.video_context.upload_id is None


@pytest.mark.asyncio
async def test_configure_rejects_unsupported_runtime_without_saving(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    def unsupported(config):
        raise ValueError("Only a single Ref2AV Motion Context variation is certified")
    monkeypatch.setattr("app.core.projects.video_context._runtime_options", unsupported)
    _, payloads, actions, _ = await _call(
        "configure_video_context", project.id,
        {"shot_id": second.id, "mode": "previous_shot"}, "Continue the second shot",
    )
    assert payloads[-1]["ok"] is False
    assert payloads[-1]["blocked_reasons"]
    assert actions == []
    assert load_shot(project.id, second.id).video_context is None


@pytest.mark.asyncio
async def test_pending_source_saves_a_plan_without_claiming_readiness(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    before = load_shot(project.id, second.id)
    handled, payloads, actions, _notes = await _call(
        "configure_video_context", project.id,
        {"shot_id": second.id, "mode": "previous_shot"},
        "继续刚才的动作",
    )
    assert handled is True
    assert payloads[-1]["ok"] is True
    assert payloads[-1]["state"] == "waiting"
    assert payloads[-1]["ready"] is False
    assert payloads[-1]["source_job_id"] is None
    assert payloads[-1]["waiting_reasons"]
    assert payloads[-1]["actions"] == [f"configure_video_context:{second.id}"]
    assert actions == payloads[-1]["actions"]
    assert payloads[-1]["blocked_reasons"] == []
    saved = load_shot(project.id, second.id)
    assert saved.video_context.source_shot_id == first.id
    assert saved.model_copy(update={"video_context": before.video_context}) == before
    assert any("Waiting for the source video" in note for note in _notes)
    assert list_jobs(project_id=project.id) == []


@pytest.mark.asyncio
async def test_disabled_flag_does_not_save(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    monkeypatch.setattr(settings, "video_context_enabled", False)
    handled, payloads, actions, _notes = await _call(
        "configure_video_context", project.id,
        {"shot_id": second.id, "mode": "previous_shot"},
        "第二镜接着上一镜往前推",
    )
    assert handled is True and payloads[-1]["ok"] is False
    assert "disabled" in payloads[-1]["blocked_reasons"][0]
    assert actions == []
    assert load_shot(project.id, second.id).video_context is None


@pytest.mark.asyncio
async def test_start_returns_the_created_job_and_a_missing_id_is_not_invented(monkeypatch, tmp_path):
    import app.agents.director.tool_handlers.video as video_tools
    async def authorize(*args):
        return True
    monkeypatch.setattr(video_tools, "_authorize_one_off_video", authorize)
    from app.api import projects as projects_api

    project, _first, second = _board(monkeypatch, tmp_path)
    created = []

    async def fake_submit(shot_id, svc, options):
        job = create_job(
            pipeline_id="h3_ref2va", asset_kind="productions", name="started",
            project_id=project.id, params={"shot_id": shot_id},
        )
        created.append(job.id)
        saved = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(saved)
        return saved

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", fake_submit)
    handled, payloads, actions, _notes = await _call(
        "start_h3_video", project.id,
        {"shot_id": second.id, "resolution_preset": "landscape-480"},
        "Generate Shot 2's video with local H3 now",
    )
    assert handled is True and payloads[-1]["job_id"] == created[0]
    assert actions == [f"start_h3_video:{second.id}:{created[0]}"]

    async def submit_without_id(shot_id, svc, options):
        return load_shot(project.id, shot_id).model_copy(update={"h3_job_id": None})

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", submit_without_id)
    before = {job.id for job in list_jobs(project_id=project.id)}
    with pytest.raises(ValueError, match="no Job ID"):
        await _call(
            "start_h3_video", project.id,
            {"shot_id": second.id, "resolution_preset": "landscape-480"},
            "Generate Shot 2's video with local H3 now",
        )
    assert {job.id for job in list_jobs(project_id=project.id)} == before


def test_active_context_changes_the_current_fingerprint_only(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    before = _fingerprint(project.id)
    legacy = _legacy_fingerprint(project.id)
    version_one = _project_fingerprint(project.id, legacy=False, version=1)
    save_shot(load_shot(project.id, second.id).model_copy(
        update={"video_context": ShotVideoContext(mode="off")},
    ))
    assert _fingerprint(project.id) == before
    configure_video_context(
        project.id, second.id, ShotVideoContext(mode="previous_shot", context_frames=22),
    )
    assert _fingerprint(project.id) != before
    assert _legacy_fingerprint(project.id) == legacy
    assert _project_fingerprint(project.id, legacy=False, version=1) == version_one


def test_prompt_signature_ignores_off_until_the_key_exists(monkeypatch, tmp_path):
    from app.core.projects.video_context import (
        video_context_prompt_is_stale,
        video_context_prompt_signature,
    )

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    untouched = load_shot(project.id, second.id)
    assert video_context_prompt_is_stale(untouched) is False
    configure_video_context(
        project.id, second.id, ShotVideoContext(mode="previous_shot", context_frames=22),
    )
    active = load_shot(project.id, second.id)
    assert video_context_prompt_signature(active) != video_context_prompt_signature(untouched)
    assert video_context_prompt_is_stale(active) is True
    stamped = active.model_copy(update={"meta": {
        **active.meta,
        "prompt_video_context_signature": video_context_prompt_signature(active),
    }})
    assert video_context_prompt_is_stale(stamped) is False
    changed = stamped.model_copy(update={"video_context": ShotVideoContext(mode="off")})
    assert video_context_prompt_is_stale(changed) is True


@pytest.mark.asyncio
async def test_writer_stores_the_video_context_signature(authored_shot):
    from app.core.projects.video_context import (
        video_context_prompt_is_stale,
        video_context_prompt_signature,
    )

    project, shot = authored_shot

    class Provider:
        async def complete(self, system, user, **kwargs):
            return json.dumps({"prompt_sections": writer_sections(), "dialogue_uses": [
                {"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]})

    updated = await DirectorService(
        plan_provider=Provider(), orchestrator=Orchestrator(),
    ).write_prompts_after_layout(shot.id)
    assert updated.meta["prompt_video_context_signature"] == video_context_prompt_signature(updated)
    assert video_context_prompt_is_stale(updated) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source_change", ["rerun", "bytes"])
async def test_tail_writer_rejects_source_changes_during_review(monkeypatch, tmp_path, source_change):
    from app.core.jobs.store import save_output_file
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    target = load_shot(project.id, second.id)
    async def draft(provider, project, shot, *args, **kwargs):
        if source_change == "rerun":
            newer = _succeed(project.id, first.id, b"new-source")
            save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": newer.id}))
        else:
            save_output_file(job.id, "video", "video.mp4", b"changed-source", project_id=project.id)
        return shot
    monkeypatch.setattr("app.agents.director.tail_prompt_review.draft_and_review", draft)
    monkeypatch.setattr("app.agents.director.material_review.capture_references", lambda *args: ([], [], "unchanged-pictures"))
    monkeypatch.setattr("app.agents.director.reference_facts.certify_reference_prompt", lambda *args: {})
    def publish(candidate, *, check_current):
        check_current()
        save_shot(candidate)
    monkeypatch.setattr("app.core.managed_runs.prompt_commit.save_reviewed_tail_prompt", publish)
    svc = DirectorService(plan_provider=object(), orchestrator=Orchestrator())
    with pytest.raises(ValueError, match="Video context source changed"):
        await svc._write_tail_prompt(target, project, target.model_dump(mode="json"), "")
    assert "prompt_video_context_signature" not in load_shot(project.id, target.id).meta


def test_writer_observation_uses_the_real_tail_and_hides_names(monkeypatch, tmp_path):
    from app.agents.director.brief import shot_execution_intent
    from app.agents.director.writer_context import video_context_writer_view

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id, b"source-video")
    original = job_dir(job.id, project_id=project.id) / "outputs" / "video.mp4"
    renamed = original.with_name("theme-song.mp4")
    original.rename(renamed)
    loaded = load_job(job.id)
    loaded.outputs["video"] = OutputSlot(key="video", label="video", filename="theme-song.mp4")
    save_job(loaded)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id, "title": "Chorus"}))
    configure_video_context(
        project.id, second.id, ShotVideoContext(mode="previous_shot", context_frames=22),
    )
    shot = load_shot(project.id, second.id)
    seen = []

    def fake_png(path):
        seen.append(path)
        return b"\x89PNG\r\n\x1a\ntail-frame"

    monkeypatch.setattr("app.agents.director.writer_context._png_from_video", fake_png)
    view = video_context_writer_view(shot)
    public = {key: value for key, value in view.items() if key != "tail_frame_png"}
    assert seen == [renamed]
    assert public == {
        "mode": "previous_shot",
        "source_shot_id": first.id,
        "source_job_id": job.id,
        "context_frames": 22,
        "carry_audio": False,
        "role": "observation_only",
        "picture_slots": [],
        "audio_slots": [],
    }
    assert view["tail_frame_png"].startswith(b"\x89PNG")
    assert "theme-song" not in json.dumps(public)
    assert "Chorus" not in json.dumps(public)
    intent = shot_execution_intent(project, shot, "")
    encoded = json.dumps(intent, ensure_ascii=False)
    assert intent["video_context_observation"] == public
    assert "tail_frame_png" not in encoded
    assert "theme-song" not in encoded


@pytest.mark.asyncio
async def test_writer_completion_attaches_the_tail_frame_without_a_filename():
    from app.agents.director.writer_context import complete_writer_prompt

    png = b"\x89PNG\r\n\x1a\ntail-frame"
    observation = {
        "mode": "previous_shot",
        "role": "observation_only",
        "picture_slots": [],
        "audio_slots": [],
        "tail_frame_png": png,
    }

    class Provider:
        def __init__(self):
            self.images = None
            self.user = None

        async def complete(self, system, user, **kwargs):
            self.user = user
            return "text"

        async def complete_with_images(self, system, user, *, images, **kwargs):
            self.images = images
            self.user = user
            return "vision"

    provider = Provider()
    user = json.dumps({"video_context_observation": {"role": "observation_only"}})
    assert await complete_writer_prompt(provider, "system", user, observation) == "vision"
    assert provider.images == [base64.b64encode(png).decode("ascii")]
    assert "theme-song" not in provider.user
    assert ".mp4" not in provider.user
    text_only = Provider()

    async def missing(*args, **kwargs):
        raise AssertionError("text providers keep the existing complete call")

    text_only.complete_with_images = None
    assert await complete_writer_prompt(text_only, "system", user, observation) == "text"
    assert json.loads(text_only.user)["video_context_observation"]["tail_frame_status"] == "not_attached"


@pytest.mark.asyncio
async def test_material_review_request_carries_observation_metadata(monkeypatch, tmp_path):
    from app.agents.director import tail_prompt_review

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id, b"source-video")
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    configure_video_context(
        project.id, second.id, ShotVideoContext(mode="previous_shot", context_frames=5),
    )
    shot = load_shot(project.id, second.id)
    monkeypatch.setattr(
        "app.agents.director.writer_context._png_from_video",
        lambda path: b"\x89PNG\r\n\x1a\ntail-frame",
    )

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(tail_prompt_review, "observe_references_cached", empty)
    monkeypatch.setattr(tail_prompt_review, "prepare_dialogue", empty)
    captured = []

    class Provider:
        async def complete_bounded(self, system, user, **kwargs):
            captured.append(user)
            raise RuntimeError("captured")

        async def complete(self, system, user, **kwargs):
            return await self.complete_bounded(system, user)

    with pytest.raises(RuntimeError, match="captured"):
        await tail_prompt_review.draft_and_review(
            Provider(), project, shot, [], [], "sig", lambda: None, lambda *args, **kwargs: None,
        )
    body = json.loads(captured[0])
    observation = body["video_context_observation"]
    assert observation["role"] == "observation_only"
    assert observation["context_frames"] == 5
    assert observation["picture_slots"] == []
    assert observation["audio_slots"] == []
    assert "tail_frame_png" not in observation
    assert ".mp4" not in captured[0]


def test_status_reports_the_resolved_source_and_a_later_block(monkeypatch, tmp_path):
    from app.agents.director.chat_context import project_context_blob
    from app.agents.director.chat_orchestrator import _status_summary
    from app.core.projects.video_context import video_context_status

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    shots = [load_shot(project.id, first.id), load_shot(project.id, second.id)]
    summary = _status_summary(project, shots)
    assert "video context: previous_shot" in summary
    assert job.id in summary
    assert ".mp4" not in summary
    status = video_context_status(shots[1])
    assert status["source_job_id"] == job.id
    assert status["blocked_reasons"] == []
    state = json.loads(project_context_blob(project, shots, message=""))
    assert state["shots"][1]["video_context_status"]["source_job_id"] == job.id

    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": None}))
    blocked = video_context_status(load_shot(project.id, second.id))
    assert any("no H3 job" in reason for reason in blocked["blocked_reasons"])


@pytest.mark.asyncio
async def test_get_status_includes_video_context_block(monkeypatch, tmp_path):
    from app.agents.director.tool_handlers.media import handle_media_tool

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    configure_video_context(
        project.id, second.id, ShotVideoContext(mode="previous_shot", context_frames=39),
    )
    payloads = []
    handled = await handle_media_tool(
        name="get_status", args={"shot_id": second.id}, project_id=project.id,
        project=project, shots=[load_shot(project.id, first.id), load_shot(project.id, second.id)],
        runtime=None, actions=[], notes=[], touched=set(), result_payloads=payloads,
        images=None, user_feedback="",
    )
    assert handled is True
    assert payloads[-1]["video_context_status"] == {
        "mode": "previous_shot",
        "state": "ready",
        "ready": True,
        "source_job_id": job.id,
        "context_frames": 39,
        "carry_audio": False,
        "blocked_reasons": [],
    }


def test_empty_board_configure_does_not_inject_planning():
    from app.agents.director.chat_orchestrator import sanitize_tools_for_pipeline

    project = create_project("Empty", "A short script")
    requested = {"name": "configure_video_context", "args": {"shot_id": "sht_missing", "mode": "off"}}
    safe, _notes = sanitize_tools_for_pipeline([requested], project=project, shots=[])
    assert safe == [requested]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "arguments", "mode"),
    [
        ("第二镜接着上一镜往前推", {"mode": "previous_shot"}, "previous_shot"),
        ("继续刚才的动作", {"mode": "previous_shot"}, "previous_shot"),
        ("不要衔接，独立生成", {"mode": "off"}, "off"),
    ],
)
async def test_scripted_phrases_follow_the_tool_trajectory(monkeypatch, tmp_path, message, arguments, mode):
    from app.agents.director.chat import handle_chat

    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": job.id}))
    calls = 0

    async def chat_fn(system, user, **kwargs):
        nonlocal calls
        calls += 1
        from pathlib import Path
        skill = Path("app/agents/director/DIRECTOR_SKILL.md").read_text(encoding="utf-8")
        assert "configure_video_context" in skill
        assert "Configuring continuation is not generation" in skill
        offered = {tool["function"]["name"] for tool in kwargs["tools"]}
        assert "configure_video_context" in offered
        if calls == 1:
            tool_calls = [{
                "name": "configure_video_context",
                "arguments": {"shot_id": second.id, **arguments},
            }]
            if mode == "off":
                tool_calls.append({
                    "name": "start_h3_video",
                    "arguments": {"shot_id": second.id, "resolution_preset": "landscape-480"},
                })
            return {"content": "", "tool_calls": tool_calls}
        return {"content": "Done.", "tool_calls": []}

    result = await handle_chat(
        project_id=project.id, message=message,
        svc=DirectorService(plan_provider=None, orchestrator=object()), chat_fn=chat_fn,
    )
    saved = load_shot(project.id, second.id)
    assert calls >= 1
    assert f"configure_video_context:{second.id}" in result.actions
    assert saved.video_context.mode == mode
    assert not any(action.startswith("start_h3_video:") for action in result.actions)
    if mode == "previous_shot":
        assert saved.video_context.source_shot_id == first.id
    else:
        assert saved.video_context.source_shot_id is None
        assert {item.id for item in list_jobs(project_id=project.id)} == {job.id}


@pytest.mark.asyncio
async def test_lyric_question_does_not_change_the_shot(monkeypatch, tmp_path):
    from app.agents.director.chat import handle_chat

    project, _first, second = _board(monkeypatch, tmp_path)
    calls = 0

    async def chat_fn(system, user, **kwargs):
        nonlocal calls
        calls += 1
        return {"content": "这两句可以在副歌处接上。", "tool_calls": []}

    result = await handle_chat(
        project_id=project.id, message="这两段歌词如何衔接",
        svc=DirectorService(plan_provider=None, orchestrator=object()), chat_fn=chat_fn,
    )
    assert calls == 1
    assert not any(str(action).startswith("configure_video_context:") for action in result.actions)
    assert load_shot(project.id, second.id).video_context is None


@pytest.mark.asyncio
async def test_harness_managed_turn_offers_configure(monkeypatch, tmp_path):
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.managed_runs.context import ManagedTurnScope, managed_turn_scope

    project, first, second = _board(monkeypatch, tmp_path)
    token = managed_turn_scope.set(ManagedTurnScope(
        project_id=project.id, run_id="mrun_context", event_id="evt", shot_id=second.id,
    ))
    try:
        turn = BackendTurn(project.id, "继续刚才的动作", object(), None)
        names = {tool["function"]["name"] for tool in turn.context()["tools"]}
    finally:
        managed_turn_scope.reset(token)
    assert "configure_video_context" in names
    assert "save_storyboard" not in names
    assert first.id and second.id


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_target", ["same", "other_shot", "other_project"])
async def test_managed_configure_only_changes_the_scoped_shot(monkeypatch, tmp_path, scope_target):
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.managed_runs.context import ManagedTurnScope, managed_turn_scope
    from app.core.projects.models import ShotVideoContext

    project, first, second = _board(monkeypatch, tmp_path)
    save_shot(second.model_copy(update={"video_context": ShotVideoContext(mode="off", context_frames=39)}))
    before = load_shot(project.id, second.id).model_dump()
    token = managed_turn_scope.set(ManagedTurnScope(
        project_id="another_project" if scope_target == "other_project" else project.id,
        run_id="mrun_context", event_id="evt",
        shot_id=first.id if scope_target == "other_shot" else second.id,
    ))
    try:
        turn = BackendTurn(project.id, "Disable continuation", object(), None)
        result = await turn.tool({"name": "configure_video_context",
            "arguments": {"shot_id": second.id, "mode": "off"}, "call_id": "configure"})
    finally:
        managed_turn_scope.reset(token)
    assert result["ok"] is (scope_target == "same")
    if scope_target == "same":
        assert load_shot(project.id, second.id).video_context.context_frames is None
    else:
        assert load_shot(project.id, second.id).model_dump() == before
        assert not turn.actions
