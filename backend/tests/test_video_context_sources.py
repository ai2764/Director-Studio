"""Video-context source binding. These tests do not start Comfy or an LLM."""
import hashlib
import json

import pytest

from app.config import settings
from app.core.jobs.store import create_job, load_job, save_job, save_output_file
from app.core.projects.models import Shot, ShotVideoContext
from app.core.projects.store import (
    create_project,
    load_project,
    load_shot,
    replace_project_shots,
    save_project,
    save_shot,
)
from app.core.projects.video_context import (
    MAX_DURATION_S,
    MAX_UPLOAD_BYTES,
    MediaInfo,
    VideoContextError,
    configure_video_context,
    resolve_video_context,
    submission_video_context,
)
from app.core.schemas import JobStatus, OutputSlot


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(settings, "video_context_enabled", True)


def _shot(project_id: str, shot_id: str, **updates) -> Shot:
    return Shot(
        id=shot_id,
        project_id=project_id,
        scene_id="scene",
        title=shot_id,
        script_beat="beat",
        duration_s=4,
        **updates,
    )


def _board(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    project = create_project("context", "script")
    first = _shot(project.id, "sht_a")
    second = _shot(project.id, "sht_b")
    save_shot(first)
    save_shot(second)
    save_project(project.model_copy(update={"shot_ids": [first.id, second.id]}))
    return load_project(project.id), first, second


def _media(**updates) -> MediaInfo:
    payload = dict(width=864, height=480, fps=24.0, duration_s=2.0, has_audio=True, has_video=True)
    payload.update(updates)
    return MediaInfo(**payload)


def _succeed(project_id: str, shot_id: str, data: bytes = b"source-video", *, key: str = "video"):
    job = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions", name="h3",
        project_id=project_id, params={"shot_id": shot_id},
    )
    path = save_output_file(job.id, key, f"{key}.mp4", data, project_id=project_id)
    job.status = JobStatus.succeeded
    job.outputs[key] = OutputSlot(key=key, label=key, filename=path.name)
    save_job(job)
    return load_job(job.id)


def test_limits_match_the_external_video_contract():
    assert MAX_UPLOAD_BYTES == 200 * 1024 * 1024
    assert MAX_DURATION_S == 60


def test_prompt_freshness_tracks_followed_source_versions_and_bytes(monkeypatch, tmp_path):
    from app.core.projects.video_context import video_context_prompt_signature, video_context_prompt_is_stale
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    target = load_shot(project.id, second.id)
    signature = video_context_prompt_signature(target)
    stamped = target.model_copy(update={"meta": {"prompt_video_context_signature": signature}})
    assert not video_context_prompt_is_stale(stamped)
    rerun = _succeed(project.id, first.id, b"new-video")
    save_shot(first.model_copy(update={"h3_job_id": rerun.id}))
    assert video_context_prompt_is_stale(stamped)
    current_signature = video_context_prompt_signature(stamped)
    save_output_file(rerun.id, "video", "video.mp4", b"replaced-video", project_id=project.id)
    assert video_context_prompt_signature(stamped) != current_signature
    pinned = stamped.model_copy(update={"video_context": ShotVideoContext(
        mode="previous_shot", source_shot_id=first.id, source_job_id=job.id)})
    pinned_signature = video_context_prompt_signature(pinned)
    save_shot(first.model_copy(update={"h3_job_id": None}))
    assert video_context_prompt_signature(pinned) == pinned_signature


def test_custom_window_and_audio_are_inherited_during_normalization(monkeypatch, tmp_path):
    from types import SimpleNamespace
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    from app.pipelines.h3_ref2va.video_context import attach_video_context
    from app.pipelines.h3_ref2va.workflow import load_base_prompt
    profile = SimpleNamespace(source="custom", workflow=attach_video_context(
        load_base_prompt(), uploaded_video="source.mp4", delivered_frames=56,
        context_frames=39, audio_context_frames=24, carry_audio=True))
    monkeypatch.setattr("app.workflow_profiles.h3.store.resolve_active_h3_profile", lambda: profile)
    monkeypatch.setattr("app.core.projects.video_context.probe_video", lambda _: _media(width=1728,height=960))
    calls=[]
    def transcode(data, **kwargs):
        calls.append(kwargs)
        return b"normalized"
    monkeypatch.setattr("app.pipelines.h3_ref2va.video_context._transcode_context_video", transcode)
    resolved = resolve_video_context(load_shot(project.id, second.id),width=864,height=480)
    assert resolved.provenance["context_frames"] == 39
    assert resolved.provenance["carry_audio"] is True
    assert calls[0]["keep_audio"] is True


def test_old_shot_without_the_field_stays_off(monkeypatch, tmp_path):
    project, first, _second = _board(monkeypatch, tmp_path)
    payload = first.model_dump()
    payload.pop("video_context")
    path = tmp_path / "projects" / project.id / "shots" / f"{first.id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_shot(project.id, first.id).video_context is None


def test_failed_binding_keeps_the_existing_configuration(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    saved = configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    assert saved["video_context"]["source_shot_id"] == first.id
    before = load_shot(project.id, second.id)

    save_shot(load_shot(project.id, first.id).model_copy(update={"h3_job_id": None}))
    with pytest.raises(VideoContextError, match="no H3 job"):
        configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    assert load_shot(project.id, second.id) == before


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("first", "no previous shot"),
        ("cross", "immediately previous"),
        ("skipped", "immediately previous"),
        ("deleted", "not found"),
        ("running", "running"),
        ("failed", "failed"),
        ("ambiguous", "Select one video"),
        ("missing", "missing"),
    ],
)
def test_rejected_sources(monkeypatch, tmp_path, setup, message):
    project, first, second = _board(monkeypatch, tmp_path)
    target_id = second.id
    config = ShotVideoContext(mode="previous_shot")
    if setup == "first":
        target_id = first.id
    elif setup == "cross":
        other = create_project("other", "script")
        foreign = _shot(other.id, "sht_foreign")
        save_shot(foreign)
        config = ShotVideoContext(mode="previous_shot", source_shot_id=foreign.id)
    elif setup == "skipped":
        third = _shot(project.id, "sht_c")
        save_shot(third)
        save_project(load_project(project.id).model_copy(
            update={"shot_ids": [first.id, second.id, third.id]}
        ))
        target_id = third.id
        config = ShotVideoContext(mode="previous_shot", source_shot_id=first.id)
    elif setup == "deleted":
        (tmp_path / "projects" / project.id / "shots" / f"{first.id}.json").unlink()
    else:
        job = _succeed(project.id, first.id)
        if setup == "ambiguous":
            raw = save_output_file(job.id, "video_raw", "video_raw.mp4", b"raw", project_id=project.id)
            job.outputs["video_raw"] = OutputSlot(key="video_raw", label="raw", filename=raw.name)
            save_job(job)
        elif setup == "missing":
            (tmp_path / "projects" / project.id / "jobs" / job.id / "outputs" / "video.mp4").unlink()
        elif setup in {"running", "failed"}:
            job.status = JobStatus.running if setup == "running" else JobStatus.failed
            save_job(job)
        save_shot(first.model_copy(update={"h3_job_id": job.id}))
    with pytest.raises(VideoContextError, match=message):
        configure_video_context(project.id, target_id, config)


def test_explicit_historical_job_is_used_when_the_current_job_is_running(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    historical = _succeed(project.id, first.id, b"historical")
    current = _succeed(project.id, first.id, b"replacement")
    current.status = JobStatus.running
    save_job(current)
    save_shot(first.model_copy(update={"h3_job_id": current.id}))
    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video", lambda _path: _media()
    )
    configure_video_context(
        project.id, second.id,
        ShotVideoContext(mode="previous_shot", source_job_id=historical.id),
    )
    resolved = resolve_video_context(
        load_shot(project.id, second.id), width=864, height=480
    )
    assert resolved is not None
    assert resolved.data == b"historical"
    assert resolved.provenance["source_job_id"] == historical.id
    assert resolved.provenance["sha256"] == hashlib.sha256(b"historical").hexdigest()
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath("app/core/projects/video_context.py").read_text(encoding="utf-8")
    assert "list_jobs" not in source


def test_resolve_follows_the_current_pointer_and_not_an_older_success(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    _succeed(project.id, first.id, b"older")
    current = _succeed(project.id, first.id, b"current")
    save_shot(first.model_copy(update={"h3_job_id": current.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    current.status = JobStatus.failed
    save_job(current)
    with pytest.raises(VideoContextError, match="failed"):
        resolve_video_context(load_shot(project.id, second.id), width=864, height=480)


def test_unpinned_resolution_uses_only_the_shot_pointer(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    current = _succeed(project.id, first.id, b"current-good")
    save_shot(first.model_copy(update={"h3_job_id": current.id}))
    monkeypatch.setattr("app.core.projects.video_context.probe_video", lambda _path: _media())
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    assert load_shot(project.id, second.id).video_context.source_job_id is None
    resolved = resolve_video_context(load_shot(project.id, second.id), width=864, height=480)
    assert resolved.provenance["source_job_id"] == current.id
    assert resolved.data == b"current-good"


def test_reorder_that_breaks_the_pinned_source_is_rejected(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    swapped = [
        load_shot(project.id, second.id),
        load_shot(project.id, first.id),
    ]
    with pytest.raises(VideoContextError, match="no longer the previous"):
        replace_project_shots(project.id, swapped)
    assert load_project(project.id).shot_ids == [first.id, second.id]


def test_submit_freezes_bytes_after_the_source_file_changes(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id, b"frozen-source")
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    monkeypatch.setattr("app.core.projects.video_context.probe_video", lambda _path: _media())
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    staged = submission_video_context(load_shot(project.id, second.id), width=864, height=480)
    target = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions", name="target",
        project_id=project.id, params=staged["params"],
    )
    from app.core.jobs.store import save_input_file

    save_input_file(target.id, "context_video", staged["filename"], staged["data"], project_id=project.id)
    source_path = tmp_path / "projects" / project.id / "jobs" / job.id / "outputs" / "video.mp4"
    source_path.write_bytes(b"replaced-after-submit")
    frozen = (tmp_path / "projects" / project.id / "jobs" / target.id / "inputs" / "context_video.mp4").read_bytes()
    stored = load_job(target.id)
    assert frozen == b"frozen-source"
    assert stored.params["video_context_source"]["sha256"] == hashlib.sha256(b"frozen-source").hexdigest()
    assert stored.params["context_video_key"] == "context_video"


def test_disabled_submission_does_not_attach_context(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    monkeypatch.setattr(settings, "video_context_enabled", False)
    assert submission_video_context(load_shot(project.id, second.id), width=864, height=480) is None


def test_aspect_mismatch_is_rejected_without_rewriting_configuration(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    before = load_shot(project.id, second.id)
    monkeypatch.setattr(
        "app.core.projects.video_context.probe_video",
        lambda _path: _media(width=480, height=864),
    )
    with pytest.raises(VideoContextError, match="aspect ratio"):
        resolve_video_context(before, width=864, height=480)
    assert load_shot(project.id, second.id) == before


def test_configuration_reloads_after_a_fresh_read(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    job = _succeed(project.id, first.id)
    save_shot(first.model_copy(update={"h3_job_id": job.id}))
    configure_video_context(project.id, second.id, ShotVideoContext(mode="previous_shot"))
    loaded = load_shot(project.id, second.id)
    assert loaded.video_context.mode == "previous_shot"
    assert loaded.video_context.source_shot_id == first.id
    assert loaded.video_context.source_job_id is None
