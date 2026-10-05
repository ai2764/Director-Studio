"""Upload and submit wiring for video context."""
import json

import pytest
from fastapi import HTTPException

from app.config import settings
from app.core.jobs.store import create_job, load_job, save_job, save_output_file
from app.core.projects.models import Shot, ShotVideoContext
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from app.core.projects.video_context import MediaInfo, VideoContextError, upload_video_context
from app.core.schemas import JobStatus, OutputSlot


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(settings, "video_context_enabled", True)
    project = create_project("uploads", "script")
    return project


def _media(**updates) -> MediaInfo:
    payload = dict(width=864, height=480, fps=24.0, duration_s=2.0, has_audio=False, has_video=True)
    payload.update(updates)
    return MediaInfo(**payload)


def test_disabled_endpoints_reject_writes(monkeypatch):
    monkeypatch.setattr(settings, "video_context_enabled", False)
    from app.api.projects import post_video_context_upload, put_shot_video_context

    with pytest.raises(HTTPException) as saved:
        put_shot_video_context("sht_missing", ShotVideoContext())
    assert saved.value.status_code == 403
    assert saved.value.detail == "Video context is disabled"


@pytest.mark.asyncio
async def test_minimax_rejects_active_video_context_before_a_job(monkeypatch, tmp_path):
    from app.api import projects as api
    from test_video_context_sources import _board, _succeed
    from app.core.projects.models import ShotVideoContext
    from app.core.projects.video_context import configure_video_context
    from app.core.jobs.store import list_jobs
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    monkeypatch.setattr(settings, "h3_minimax_api_key", "fixture-key")
    class Service:
        async def write_prompts_after_layout(self, *args, **kwargs):
            raise AssertionError("unsupported provider must fail before writing")
    before = {job.id for job in list_jobs(project_id=project.id)}
    with pytest.raises(HTTPException) as error:
        await api.submit_shot_endpoint(second.id, svc=Service(), options=api.H3SubmitOptions(h3_provider="minimax"))
    assert error.value.status_code == 400
    assert "requires the local H3 provider" in error.value.detail
    assert {job.id for job in list_jobs(project_id=project.id)} == before


def test_upload_records_media_and_rejects_bad_files(monkeypatch, tmp_path):
    project = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video",
        lambda _path: _media(has_audio=True),
    )
    saved = upload_video_context(project.id, r"..\..\outside.mp4", b"with-audio")
    assert saved["filename"].startswith("vup_")
    assert saved["filename"].endswith(".mp4")
    assert saved["media"]["has_audio"] is True
    assert "path" not in saved
    assert str(tmp_path) not in json.dumps(saved)
    directory = tmp_path / "projects" / project.id / "video_context_uploads"
    assert list(directory.glob("*.mp4"))[0].name == saved["filename"]
    assert not (tmp_path / "outside.mp4").exists()

    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video",
        lambda _path: _media(has_audio=False),
    )
    silent = upload_video_context(project.id, "silent.mov", b"no-audio")
    assert silent["media"]["has_audio"] is False

    with pytest.raises(VideoContextError, match="Unsupported"):
        upload_video_context(project.id, "clip.txt", b"text")
    with pytest.raises(VideoContextError, match="200 MiB"):
        monkeypatch.setattr("app.core.projects.video_context.MAX_UPLOAD_BYTES", 4)
        upload_video_context(project.id, "big.mp4", b"12345")
    monkeypatch.setattr("app.core.projects.video_context.MAX_UPLOAD_BYTES", 200 * 1024 * 1024)
    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video",
        lambda _path: _media(duration_s=61),
    )
    with pytest.raises(VideoContextError, match="60 seconds"):
        upload_video_context(project.id, "long.webm", b"too-long")
    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video",
        lambda _path: (_ for _ in ()).throw(VideoContextError("Upload has no video stream")),
    )
    with pytest.raises(VideoContextError, match="no video"):
        upload_video_context(project.id, "empty.mp4", b"not-video")
    records = list(directory.glob("*.json"))
    assert {item.stem for item in records} == {saved["upload_id"], silent["upload_id"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("source_size", [(864, 480), (480, 864)])
async def test_submit_puts_context_bytes_on_the_job_and_stops_when_unreadable(
    monkeypatch, tmp_path, source_size
):
    project = _isolate(monkeypatch, tmp_path)
    first = Shot(
        id="sht_a", project_id=project.id, scene_id="scene", title="A",
        script_beat="beat", duration_s=4,
    )
    second = Shot(
        id="sht_b", project_id=project.id, scene_id="scene", title="B",
        script_beat="beat", duration_s=4,
    )
    save_shot(first)
    save_shot(second)
    save_project(project.model_copy(update={"shot_ids": [first.id, second.id]}))
    job = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions", name="source",
        project_id=project.id, params={"shot_id": first.id},
    )
    path = save_output_file(job.id, "video", "video.mp4", b"submit-source", project_id=project.id)
    job.status = JobStatus.succeeded
    job.outputs["video"] = OutputSlot(key="video", label="video", filename=path.name)
    save_job(job)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video",
        lambda _path: _media(width=source_size[0], height=source_size[1]),
    )
    from app.core.projects.video_context import configure_video_context, video_context_prompt_signature

    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    current = load_shot(project.id, second.id)
    save_shot(current.model_copy(update={"meta": {
        **current.meta,
        "prompt_video_context_signature": video_context_prompt_signature(current),
    }}))

    from app.api import projects as api
    from app.core.jobs.store import save_input_file

    async def _start(started, images=None):
        for kind, (filename, data) in (images or {}).items():
            save_input_file(started.id, kind, filename, data, project_id=started.project_id)
        return started

    monkeypatch.setattr(api, "start_pipeline_job", _start)
    monkeypatch.setattr(api, "assert_h3_submittable", lambda _shot: None)
    monkeypatch.setattr(api, "validate_h3_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "validate_editorial_music_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_collect_h3_images", lambda _shot: {"picture": ("a.png", b"png")})
    monkeypatch.setattr(
        "app.core.projects.transitions.assert_h3_submittable", lambda _shot: None
    )
    monkeypatch.setattr(
        "app.agents.director.dialogue_preflight.dialogue_contract_current",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.agents.director.dialogue_preflight.require_current_dialogue_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.agents.director.reference_facts.reference_contract_current",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.agents.director.reference_facts.require_current_reference_contract",
        lambda *_args, **_kwargs: None,
    )

    class _Service:
        async def write_prompts_after_layout(self, _shot_id):
            raise AssertionError("prompt refresh is not part of this submit")

    submitted = await api.submit_shot_endpoint(second.id, svc=_Service(), options=None)
    stored = load_job(submitted.h3_job_id)
    assert (stored.params["width"], stored.params["height"]) == source_size
    assert stored.params["video_context_source"]["sha256"]
    copied = (
        tmp_path / "projects" / project.id / "jobs" / stored.id / "inputs" / "context_video.mp4"
    ).read_bytes()
    assert copied == b"submit-source"
    assert "context_video" not in stored.params["image_keys"]

    from app.core.projects.models import ShotStatus

    blocked = load_shot(project.id, second.id).model_copy(
        update={"status": ShotStatus.draft, "h3_job_id": None}
    )
    save_shot(blocked)
    path.unlink()
    with pytest.raises(HTTPException) as error:
        await api.submit_shot_endpoint(second.id, svc=_Service(), options=None)
    assert error.value.status_code == 400
    assert load_shot(project.id, second.id).video_context.source_shot_id == first.id
