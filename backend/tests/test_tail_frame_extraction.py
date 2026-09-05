"""Tests for H3 tail-frame extraction into a pending Layout."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.core.jobs.store import create_job, job_dir, load_job, save_job
from app.core.library.store import list_assets, load_asset
from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
from app.core.projects.models import Shot, ShotStatus
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from app.core.schemas import JobStatus, OutputSlot


SOURCE_SHOT_ID = "sht_source_a"
TARGET_SHOT_ID = "sht_target_b"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    return {"projects": projects, "jobs": jobs, "library": library, "root": tmp_path}


def _ffmpeg_bin() -> str:
    resolved = shutil.which("ffmpeg")
    if not resolved:
        pytest.fail("ffmpeg is required for tail-frame extraction tests")
    return resolved


def _write_color_clip(path: Path, *, last_color: str = "blue") -> None:
    """Write a short two-segment clip whose last frames are a solid color."""
    ffmpeg = _ffmpeg_bin()
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=32x32:d=0.5:r=8",
        "-f",
        "lavfi",
        "-i",
        f"color=c={last_color}:s=32x32:d=0.5:r=8",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _make_shot(project_id: str, shot_id: str, title: str, **updates) -> Shot:
    shot = Shot(
        id=shot_id,
        project_id=project_id,
        scene_id="sc01",
        title=title,
        script_beat=title,
        duration_s=5.0,
        status=ShotStatus.draft,
    )
    if updates:
        shot = shot.model_copy(update=updates)
    save_shot(shot)
    return shot


def _seed_project(isolated) -> tuple[str, Shot, Shot]:
    project = create_project("Tail frame", "INT. HALL")
    source = _make_shot(project.id, SOURCE_SHOT_ID, "shot2", status=ShotStatus.succeeded)
    target = _make_shot(
        project.id,
        TARGET_SHOT_ID,
        "shot3",
        layout_refs=[
            LayoutReference(
                id="lref_existing",
                asset_id="lay_existing",
                purpose="existing composition",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            )
        ],
    )
    save_project(project.model_copy(update={"shot_ids": [source.id, target.id]}))
    return project.id, source, target


def _h3_job_with_video(
    project_id: str,
    shot_id: str,
    *,
    filename: str = "take_enhanced.mp4",
    last_color: str = "blue",
    library_asset_id: str | None = "lay_source_clip",
    write_video: bool = True,
) -> object:
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="shot2 v1",
        project_id=project_id,
        params={"shot_id": shot_id, "project_id": project_id},
    )
    out_dir = job_dir(job.id, project_id=project_id) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / filename
    if write_video:
        _write_color_clip(video_path, last_color=last_color)
    job.status = JobStatus.succeeded
    job.library_asset_id = library_asset_id
    job.outputs = {
        "video": OutputSlot(
            key="video",
            label="video",
            path=str(video_path),
            filename=filename,
            url=f"/api/files/jobs/{job.id}/outputs/{filename}",
        )
    }
    save_job(job)
    return job


def _mean_rgb(path: Path) -> tuple[float, float, float]:
    image = Image.open(path).convert("RGB")
    pixels = list(image.getdata())
    n = len(pixels)
    return (
        sum(p[0] for p in pixels) / n,
        sum(p[1] for p in pixels) / n,
        sum(p[2] for p in pixels) / n,
    )


def _layout_dirs(project_id: str) -> list[Path]:
    root = settings.projects_dir / project_id / "library" / "layouts"
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir()]


def test_extracts_final_frame_and_appends_pending_layout(isolated, monkeypatch):
    from app.core.media.tail_frame import extract_clip_tail_frame
    from app.core.vram.orchestrator import VramOrchestrator

    gpu_calls: list[object] = []

    async def _boom_acquire(self, *args, **kwargs):
        gpu_calls.append((args, kwargs))
        raise AssertionError("GPU orchestrator must not be acquired")

    monkeypatch.setattr(VramOrchestrator, "before_comfy_job", _boom_acquire)
    monkeypatch.setattr(
        "app.core.vram.get_orchestrator",
        lambda: (_ for _ in ()).throw(AssertionError("get_orchestrator")),
    )

    project_id, _source, target = _seed_project(isolated)
    job = _h3_job_with_video(project_id, SOURCE_SHOT_ID, last_color="blue")

    result = extract_clip_tail_frame(
        project_id=project_id,
        source_shot_id=SOURCE_SHOT_ID,
        target_shot_id=TARGET_SHOT_ID,
        source_version="latest",
        source_job_id=None,
        output_kind=None,
    )

    assert result["source_shot_id"] == SOURCE_SHOT_ID
    assert result["target_shot_id"] == TARGET_SHOT_ID
    assert result["source_job_id"] == job.id
    assert result["source_version"] == 1
    assert result["output_kind"] == "enhanced"
    assert result["layout_ref_id"].startswith("lref_")
    assert result["layout_asset_id"].startswith("lay_")
    assert result["image_url"] == (
        f"/api/files/library/layouts/{result['layout_asset_id']}/layout.png"
    )
    assert gpu_calls == []

    asset = load_asset("layouts", result["layout_asset_id"])
    assert asset is not None
    assert asset.kind == "layouts"
    assert asset.files.get("layout") == "layout.png"
    assert asset.pipeline_id == "h3_ref2va"
    assert asset.job_id == job.id
    assert asset.project_id == project_id
    assert asset.meta.get("review_status") == "pending_review"
    origin = asset.meta.get("origin") or {}
    assert origin.get("kind") == "clip_tail_frame"
    assert origin.get("source_shot_id") == SOURCE_SHOT_ID
    assert origin.get("source_job_id") == job.id
    assert origin.get("source_generation") == 1
    assert origin.get("output_kind") == "enhanced"
    assert origin.get("output_key") == "video"
    assert origin.get("source_filename") == "take_enhanced.mp4"
    duration = origin.get("source_duration_s")
    timestamp = origin.get("extracted_timestamp_s")
    if duration is not None:
        assert duration == pytest.approx(1.0, abs=0.2)
    if timestamp is not None:
        assert timestamp >= 0
        if duration is not None:
            assert timestamp <= duration + 0.05

    png = (
        settings.projects_dir
        / project_id
        / "library"
        / "layouts"
        / asset.id
        / "layout.png"
    )
    assert png.is_file()
    r, g, b = _mean_rgb(png)
    assert b > r and b > g, f"expected last (blue) frame, got rgb=({r:.1f},{g:.1f},{b:.1f})"

    updated = load_shot(project_id, TARGET_SHOT_ID)
    assert updated is not None
    assert [ref.id for ref in updated.layout_refs] == [
        "lref_existing",
        result["layout_ref_id"],
    ]
    new_ref = updated.layout_refs[1]
    assert new_ref.asset_id == asset.id
    assert new_ref.review_status == LayoutReviewStatus.pending_review
    assert new_ref.selected_for_h3 is False
    assert new_ref.source_refs == []
    assert new_ref.purpose == f"cross-shot visual continuity from {SOURCE_SHOT_ID}"
    assert (
        new_ref.state_description
        == f"final visible state of {SOURCE_SHOT_ID} for continuity into {TARGET_SHOT_ID}"
    )
    assert new_ref.time_hint == (
        f"transition from {SOURCE_SHOT_ID} into this shot"
    )
    assert new_ref.origin is not None
    assert new_ref.origin.kind == "clip_tail_frame"
    assert new_ref.origin.source_job_id == job.id
    assert new_ref.origin.source_generation == 1
    assert updated.status == ShotStatus.needs_review

    reloaded_job = load_job(job.id)
    assert reloaded_job is not None
    assert reloaded_job.library_asset_id == "lay_source_clip"

    layouts = list_assets("layouts", project_id=project_id)
    assert len(layouts) == 1


def test_reload_target_shot_before_save_preserves_concurrent_edits(
    isolated, monkeypatch
):
    from app.core.media import tail_frame

    project_id, _, _ = _seed_project(isolated)
    job = _h3_job_with_video(project_id, SOURCE_SHOT_ID)
    original = tail_frame._extract_last_frame

    def _extract(video, dest):
        result = original(video, dest)
        current = load_shot(project_id, TARGET_SHOT_ID)
        assert current is not None
        save_shot(current.model_copy(update={"script_beat": "concurrent edit"}))
        return result

    monkeypatch.setattr(tail_frame, "_extract_last_frame", _extract)

    tail_frame.extract_clip_tail_frame(
        project_id=project_id,
        source_shot_id=SOURCE_SHOT_ID,
        target_shot_id=TARGET_SHOT_ID,
        source_version=None,
        source_job_id=job.id,
        output_kind="enhanced",
    )
    saved = load_shot(project_id, TARGET_SHOT_ID)
    assert saved is not None
    assert saved.script_beat == "concurrent edit"
    assert any(
        ref.origin is not None and ref.origin.kind == "clip_tail_frame"
        for ref in saved.layout_refs
    )


def test_reload_rejects_if_target_h3_starts_during_extract(isolated, monkeypatch):
    from app.core.media import tail_frame

    project_id, _, _ = _seed_project(isolated)
    job = _h3_job_with_video(project_id, SOURCE_SHOT_ID)
    original = tail_frame._extract_last_frame

    def _extract(video, dest):
        result = original(video, dest)
        current = load_shot(project_id, TARGET_SHOT_ID)
        assert current is not None
        save_shot(
            current.model_copy(
                update={
                    "status": ShotStatus.running,
                    "h3_job_id": "job_live_h3",
                }
            )
        )
        return result

    monkeypatch.setattr(tail_frame, "_extract_last_frame", _extract)

    with pytest.raises(ValueError, match="active H3"):
        tail_frame.extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id=TARGET_SHOT_ID,
            source_version=None,
            source_job_id=job.id,
            output_kind="enhanced",
        )
    saved = load_shot(project_id, TARGET_SHOT_ID)
    assert saved is not None
    assert saved.status == ShotStatus.running
    assert not any(
        ref.origin is not None and ref.origin.kind == "clip_tail_frame"
        for ref in saved.layout_refs
    )
    assert _layout_dirs(project_id) == []


def test_fallback_from_tail_window_seek_to_sequential_decode(isolated, monkeypatch):
    from app.core.media import tail_frame

    project_id, _, _ = _seed_project(isolated)
    job = _h3_job_with_video(
        project_id, SOURCE_SHOT_ID, filename="vfr_like.mp4", last_color="green"
    )

    original = tail_frame._run_checked
    ffmpeg_calls = {"n": 0}

    def _checked(command, *, error):
        name = Path(command[0]).name.lower()
        if name in {"ffmpeg", "ffmpeg.exe"}:
            ffmpeg_calls["n"] += 1
            if ffmpeg_calls["n"] == 1:
                raise ValueError("simulated tail-window seek failure")
        return original(command, error=error)

    monkeypatch.setattr(tail_frame, "_run_checked", _checked)

    result = tail_frame.extract_clip_tail_frame(
        project_id=project_id,
        source_shot_id=SOURCE_SHOT_ID,
        target_shot_id=TARGET_SHOT_ID,
        source_version=None,
        source_job_id=job.id,
        output_kind="enhanced",
    )
    assert ffmpeg_calls["n"] >= 2
    png = (
        settings.projects_dir
        / project_id
        / "library"
        / "layouts"
        / result["layout_asset_id"]
        / "layout.png"
    )
    r, g, b = _mean_rgb(png)
    assert g > r and g > b, f"expected last (green) frame, got rgb=({r:.1f},{g:.1f},{b:.1f})"


def test_ffmpeg_failure_leaves_no_asset_and_does_not_save_target(isolated, monkeypatch):
    from app.core.media import tail_frame

    project_id, _, target = _seed_project(isolated)
    _h3_job_with_video(project_id, SOURCE_SHOT_ID)
    before = load_shot(project_id, TARGET_SHOT_ID)
    assert before is not None
    before_json = (
        settings.projects_dir / project_id / "shots" / f"{TARGET_SHOT_ID}.json"
    ).read_text(encoding="utf-8")

    original = tail_frame._run_checked

    def _fail_ffmpeg(command, *, error):
        name = Path(command[0]).name.lower()
        if name in {"ffmpeg", "ffmpeg.exe"}:
            raise ValueError("ffmpeg decode failed")
        return original(command, error=error)

    monkeypatch.setattr(tail_frame, "_run_checked", _fail_ffmpeg)

    with pytest.raises(ValueError, match="ffmpeg|decode|frame"):
        tail_frame.extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id=TARGET_SHOT_ID,
            source_version="latest",
            source_job_id=None,
            output_kind=None,
        )

    assert _layout_dirs(project_id) == []
    after = load_shot(project_id, TARGET_SHOT_ID)
    assert after is not None
    assert [ref.id for ref in after.layout_refs] == [ref.id for ref in target.layout_refs]
    assert after.status == ShotStatus.draft
    assert (
        settings.projects_dir / project_id / "shots" / f"{TARGET_SHOT_ID}.json"
    ).read_text(encoding="utf-8") == before_json


def test_persistence_failure_rolls_back_new_asset_dir(isolated, monkeypatch):
    from app.core.media import tail_frame

    project_id, _, _ = _seed_project(isolated)
    _h3_job_with_video(project_id, SOURCE_SHOT_ID)
    before_json = (
        settings.projects_dir / project_id / "shots" / f"{TARGET_SHOT_ID}.json"
    ).read_text(encoding="utf-8")

    def _boom_save(shot):
        raise OSError("disk full")

    monkeypatch.setattr(tail_frame, "save_shot", _boom_save)

    with pytest.raises(OSError, match="disk full"):
        tail_frame.extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id=TARGET_SHOT_ID,
            source_version="1",
            source_job_id=None,
            output_kind=None,
        )

    assert _layout_dirs(project_id) == []
    assert (
        settings.projects_dir / project_id / "shots" / f"{TARGET_SHOT_ID}.json"
    ).read_text(encoding="utf-8") == before_json


def test_rejects_same_shot_source_and_target(isolated):
    from app.core.media.tail_frame import extract_clip_tail_frame

    project_id, _, _ = _seed_project(isolated)
    with pytest.raises(ValueError, match="same shot"):
        extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id=SOURCE_SHOT_ID,
            source_version="latest",
            source_job_id=None,
            output_kind=None,
        )
    assert _layout_dirs(project_id) == []


def test_rejects_different_projects(isolated):
    from app.core.media.tail_frame import extract_clip_tail_frame

    project_id, _, _ = _seed_project(isolated)
    other = create_project("Other", "INT. OTHER")
    _make_shot(other.id, "sht_other_target", "other")
    save_project(other.model_copy(update={"shot_ids": ["sht_other_target"]}))

    with pytest.raises(ValueError, match="same project"):
        extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id="sht_other_target",
            source_version="latest",
            source_job_id=None,
            output_kind=None,
        )
    assert _layout_dirs(project_id) == []
    assert _layout_dirs(other.id) == []


@pytest.mark.parametrize(
    "status",
    [JobStatus.queued, JobStatus.uploading, JobStatus.running],
)
def test_rejects_active_target_h3(isolated, status):
    from app.core.media.tail_frame import extract_clip_tail_frame

    project_id, _, target = _seed_project(isolated)
    _h3_job_with_video(project_id, SOURCE_SHOT_ID)
    active = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="target h3",
        project_id=project_id,
        params={"shot_id": TARGET_SHOT_ID, "project_id": project_id},
    )
    active.status = status
    save_job(active)
    shot_status = (
        ShotStatus.running if status == JobStatus.running else ShotStatus.queued
    )
    if status == JobStatus.uploading:
        shot_status = ShotStatus.queued
    save_shot(
        target.model_copy(update={"h3_job_id": active.id, "status": shot_status})
    )

    with pytest.raises(ValueError, match="active H3"):
        extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id=TARGET_SHOT_ID,
            source_version="latest",
            source_job_id=None,
            output_kind=None,
        )

    reloaded = load_shot(project_id, TARGET_SHOT_ID)
    assert reloaded is not None
    assert [ref.id for ref in reloaded.layout_refs] == ["lref_existing"]
    assert _layout_dirs(project_id) == []


def test_rejects_path_traversal(isolated):
    from app.core.media.clip_generations import ClipGenerationError
    from app.core.media.tail_frame import extract_clip_tail_frame

    project_id, _, _ = _seed_project(isolated)
    outside = isolated["root"] / "escaped.mp4"
    _write_color_clip(outside, last_color="blue")
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="escaped",
        project_id=project_id,
        params={"shot_id": SOURCE_SHOT_ID, "project_id": project_id},
    )
    job.status = JobStatus.succeeded
    job.outputs = {
        "video": OutputSlot(
            key="video",
            label="video",
            path=str(outside),
            filename="../../escaped.mp4",
            url=f"/api/files/jobs/{job.id}/outputs/escaped.mp4",
        )
    }
    save_job(job)

    with pytest.raises(ClipGenerationError, match="escapes|missing|output"):
        extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=SOURCE_SHOT_ID,
            target_shot_id=TARGET_SHOT_ID,
            source_version=None,
            source_job_id=job.id,
            output_kind=None,
        )
    assert _layout_dirs(project_id) == []
    target = load_shot(project_id, TARGET_SHOT_ID)
    assert target is not None
    assert [ref.id for ref in target.layout_refs] == ["lref_existing"]

