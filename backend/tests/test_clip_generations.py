"""Tests for H3 clip generation listing and source-clip resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.core.jobs.store import create_job, job_dir, save_job
from app.core.projects.layouts import LayoutReference
from app.core.schemas import JobStatus, OutputSlot


PROJECT_ID = "prj_clip_gen"
SHOT_A = "sht_source_a"
SHOT_B = "sht_source_b"


@pytest.fixture
def jobs_dir(tmp_projects_dir, monkeypatch):
    jobs = tmp_projects_dir.parent / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    return jobs


def _materialize(
    job,
    *,
    status: JobStatus = JobStatus.succeeded,
    created_at: str | None = None,
    outputs: dict[str, str] | None = None,
    write_files: bool = True,
) -> None:
    """Mark a job succeeded (or other status) and optionally write output files."""
    if created_at is not None:
        job.created_at = created_at
    job.status = status
    if outputs is not None:
        slots: dict[str, OutputSlot] = {}
        out_dir = job_dir(job.id, project_id=job.project_id) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, filename in outputs.items():
            path = out_dir / filename
            if write_files:
                path.write_bytes(b"fake-mp4")
            slots[key] = OutputSlot(
                key=key,
                label=key,
                path=str(path),
                filename=filename,
                url=f"/api/files/jobs/{job.id}/outputs/{filename}",
            )
        job.outputs = slots
    save_job(job)


def _h3_job(
    *,
    project_id: str,
    shot_id: str,
    name: str,
    pipeline_id: str = "h3_ref2va",
    asset_kind: str = "productions",
):
    return create_job(
        pipeline_id=pipeline_id,
        asset_kind=asset_kind,
        name=name,
        project_id=project_id,
        params={"shot_id": shot_id, "project_id": project_id},
    )


def test_layout_reference_origin_optional_and_typed():
    from app.core.projects.layouts import ClipTailFrameOrigin

    legacy = LayoutReference(id="lref_legacy", asset_id="lay_1")
    assert legacy.origin is None

    origin = ClipTailFrameOrigin(
        source_shot_id=SHOT_A,
        source_job_id="job_abc",
        source_generation=2,
        output_kind="enhanced",
        output_key="video",
        source_filename="video.mp4",
    )
    ref = LayoutReference(id="lref_new", asset_id="lay_2", origin=origin)
    assert ref.origin is not None
    assert ref.origin.kind == "clip_tail_frame"
    assert ref.origin.source_generation == 2

    restored = LayoutReference.model_validate(
        {
            "id": "lref_roundtrip",
            "asset_id": "lay_3",
            "origin": {
                "kind": "clip_tail_frame",
                "source_shot_id": SHOT_A,
                "source_job_id": "job_abc",
                "source_generation": 1,
                "output_kind": "raw",
                "output_key": "video_raw",
                "source_filename": "clip_raw.mp4",
            },
        }
    )
    assert restored.origin is not None
    assert restored.origin.output_kind == "raw"
    assert restored.origin.output_key == "video_raw"


def test_list_shot_h3_generations_orders_and_filters(jobs_dir):
    from app.core.media.clip_generations import list_shot_h3_generations

    early = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v1")
    _materialize(
        early,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    late = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v2")
    _materialize(
        late,
        created_at="2026-08-01T11:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    # Same timestamp as late but lexicographically smaller id → before late if id sorts first;
    # force created_at between early and late for clear ordering.
    mid = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v1b")
    _materialize(
        mid,
        created_at="2026-08-01T10:30:00+00:00",
        outputs={"video": "video.mp4"},
    )

    other_shot = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_B, name="other-shot")
    _materialize(
        other_shot,
        created_at="2026-08-01T12:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    other_project = _h3_job(project_id="prj_other", shot_id=SHOT_A, name="other-prj")
    _materialize(
        other_project,
        created_at="2026-08-01T12:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    other_pipeline = create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="not-h3",
        project_id=PROJECT_ID,
        params={"shot_id": SHOT_A},
    )
    _materialize(
        other_pipeline,
        created_at="2026-08-01T12:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    json_shot = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="json-shot",
        project_id=PROJECT_ID,
        params={"json_shot_id": SHOT_A},
    )
    _materialize(
        json_shot,
        created_at="2026-08-01T12:00:00+00:00",
        outputs={"video": "video.mp4"},
    )

    gens = list_shot_h3_generations(PROJECT_ID, SHOT_A)
    assert [j.id for j in gens] == [early.id, mid.id, late.id]


def test_resolve_latest_v2_numeric_and_job_id(jobs_dir):
    from app.core.media.clip_generations import resolve_source_clip

    v1 = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v1")
    _materialize(
        v1,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={"video": "enhanced_custom.mp4"},
    )
    v2 = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v2")
    _materialize(
        v2,
        created_at="2026-08-01T11:00:00+00:00",
        outputs={"video": "take2_enhanced.mp4"},
    )

    latest = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version="latest",
        source_job_id=None,
        output_kind=None,
    )
    assert latest.source_job_id == v2.id
    assert latest.source_generation == 2
    assert latest.output_kind == "enhanced"
    assert latest.output_key == "video"
    assert latest.source_filename == "take2_enhanced.mp4"
    assert latest.path == Path(
        job_dir(v2.id, project_id=PROJECT_ID) / "outputs" / "take2_enhanced.mp4"
    )
    assert latest.path.is_file()

    by_v = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version="v2",
        source_job_id=None,
        output_kind=None,
    )
    assert by_v.source_job_id == v2.id
    assert by_v.source_generation == 2

    by_num = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version="2",
        source_job_id=None,
        output_kind=None,
    )
    assert by_num.source_job_id == v2.id

    by_id = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version=None,
        source_job_id=v1.id,
        output_kind=None,
    )
    assert by_id.source_job_id == v1.id
    assert by_id.source_generation == 1
    assert by_id.source_filename == "enhanced_custom.mp4"


def test_resolve_rejects_missing_conflicting_and_out_of_range(jobs_dir):
    from app.core.media.clip_generations import ClipGenerationError, resolve_source_clip

    v1 = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v1")
    _materialize(
        v1,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    v2 = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="v2")
    _materialize(
        v2,
        created_at="2026-08-01T11:00:00+00:00",
        outputs={"video": "video.mp4"},
    )

    with pytest.raises(ClipGenerationError):
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_A,
            source_version=None,
            source_job_id=None,
            output_kind=None,
        )

    with pytest.raises(ClipGenerationError):
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_A,
            source_version="v1",
            source_job_id=v2.id,
            output_kind=None,
        )

    with pytest.raises(ClipGenerationError):
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_A,
            source_version="v9",
            source_job_id=None,
            output_kind=None,
        )


def test_latest_ambiguous_when_newer_active_or_failed(jobs_dir):
    from app.core.media.clip_generations import (
        ClipGenerationAmbiguous,
        resolve_source_clip,
    )

    succeeded = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="ok")
    _materialize(
        succeeded,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={"video": "video.mp4"},
    )
    failed_newer = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="fail")
    _materialize(
        failed_newer,
        status=JobStatus.failed,
        created_at="2026-08-01T11:00:00+00:00",
        outputs=None,
    )

    with pytest.raises(ClipGenerationAmbiguous) as exc_info:
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_A,
            source_version="latest",
            source_job_id=None,
            output_kind=None,
        )
    ambiguous = exc_info.value
    assert ambiguous.latest_succeeded_job_id == succeeded.id
    assert ambiguous.blocking_job_id == failed_newer.id
    assert ambiguous.blocking_status == JobStatus.failed

    # Explicit v1 / job id still resolves the succeeded clip.
    resolved = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version="v1",
        source_job_id=None,
        output_kind=None,
    )
    assert resolved.source_job_id == succeeded.id


def test_output_kind_preference_fallback_and_explicit(jobs_dir):
    from app.core.media.clip_generations import ClipGenerationError, resolve_source_clip

    both = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="both")
    _materialize(
        both,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={
            "video": "enhanced_take.mp4",
            "video_raw": "raw_take.mp4",
        },
    )
    prefer = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version="latest",
        source_job_id=None,
        output_kind=None,
    )
    assert prefer.output_kind == "enhanced"
    assert prefer.output_key == "video"
    assert prefer.source_filename == "enhanced_take.mp4"

    raw_only = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_B, name="raw-only")
    _materialize(
        raw_only,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={"video_raw": "only_raw.mp4"},
    )
    fallback = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_B,
        source_version="latest",
        source_job_id=None,
        output_kind=None,
    )
    assert fallback.output_kind == "raw"
    assert fallback.output_key == "video_raw"
    assert fallback.source_filename == "only_raw.mp4"

    explicit_raw = resolve_source_clip(
        project_id=PROJECT_ID,
        source_shot_id=SHOT_A,
        source_version="latest",
        source_job_id=None,
        output_kind="raw",
    )
    assert explicit_raw.output_kind == "raw"
    assert explicit_raw.source_filename == "raw_take.mp4"

    with pytest.raises(ClipGenerationError):
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_B,
            source_version="latest",
            source_job_id=None,
            output_kind="enhanced",
        )


def test_rejects_succeeded_job_with_missing_output_file(jobs_dir):
    from app.core.media.clip_generations import ClipGenerationError, resolve_source_clip

    job = _h3_job(project_id=PROJECT_ID, shot_id=SHOT_A, name="missing-file")
    _materialize(
        job,
        created_at="2026-08-01T10:00:00+00:00",
        outputs={"video": "gone.mp4"},
        write_files=False,
    )

    with pytest.raises(ClipGenerationError):
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_A,
            source_version="latest",
            source_job_id=None,
            output_kind=None,
        )

    with pytest.raises(ClipGenerationError):
        resolve_source_clip(
            project_id=PROJECT_ID,
            source_shot_id=SHOT_A,
            source_version=None,
            source_job_id=job.id,
            output_kind=None,
        )
