"""Managed-run selection, dependency, and source-video resolution."""

from __future__ import annotations

import pytest

from app.core.jobs.store import (
    build_output_slots,
    create_job,
    save_job,
    save_output_file,
)
from app.core.managed_runs.models import ManagedRun, RunStep
from app.core.managed_runs.selection import (
    build_execution_batch,
    latest_successful_video_job_id,
)
from app.core.projects.models import Shot
from app.core.projects.store import create_project, save_project, save_shot
from app.core.schemas import JobStatus


def managed_run_with_steps(*shot_ids: str) -> ManagedRun:
    return ManagedRun(
        run_id="mrun_test",
        project_id="prj_test",
        steps=[RunStep(shot_id=shot_id) for shot_id in shot_ids],
        plan_fingerprint="fp",
    )


def project_with_tail_dependency():
    project = create_project("Tail selection", "Two shots")
    source = Shot(
        id="sht_source",
        project_id=project.id,
        scene_id="sc",
        title="Source",
        script_beat="Source",
        duration_s=5,
    )
    dependent = Shot(
        id="sht_dependent",
        project_id=project.id,
        scene_id="sc",
        title="Dependent",
        script_beat="Dependent",
        duration_s=5,
    )
    save_shot(source)
    save_shot(dependent)
    save_project(
        project.model_copy(update={"shot_ids": [source.id, dependent.id]})
    )
    return project, source, dependent


def save_h3_generation(
    project_id: str,
    shot_id: str,
    status: JobStatus,
    video_bytes: bytes | None,
):
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name=shot_id,
        project_id=project_id,
        params={"shot_id": shot_id},
    )
    job.status = status
    if video_bytes is not None:
        path = save_output_file(
            job.id,
            "video",
            "video.mp4",
            video_bytes,
            project_id=project_id,
        )
        job.outputs = build_output_slots(job.id, {"video": path})
    save_job(job)
    return job


def test_new_run_batch_fields_are_backward_compatible() -> None:
    run = ManagedRun(
        run_id="mrun_old",
        project_id="prj_old",
        steps=[RunStep(shot_id="sht_a")],
        plan_fingerprint="fp",
    )

    assert run.selected_shot_ids == []
    assert run.pending_shot_ids == []
    assert run.skipped_shots == {}
    assert run.tail_source_job_ids == {}


def test_selection_rejects_duplicates_and_normalizes_to_plan_order() -> None:
    run = managed_run_with_steps("sht_a", "sht_b", "sht_c")

    batch = build_execution_batch("prj", run, ["sht_c", "sht_a"])

    assert batch.selected_shot_ids == ["sht_a", "sht_c"]
    assert batch.pending_shot_ids == ["sht_a", "sht_c"]
    with pytest.raises(ValueError, match="duplicate"):
        build_execution_batch("prj", run, ["sht_a", "sht_a"])
    with pytest.raises(ValueError, match="outside this plan"):
        build_execution_batch("prj", run, ["sht_unknown"])


def test_missing_source_skips_only_dependents_and_keeps_later_independent() -> None:
    run = ManagedRun(
        run_id="mrun_dependencies",
        project_id="prj_dependencies",
        plan_fingerprint="fp",
        steps=[
            RunStep(shot_id="sht_source"),
            RunStep(
                shot_id="sht_dependent",
                tail_from_shot_id="sht_source",
                tail_reason="Continue the pose",
            ),
            RunStep(shot_id="sht_independent"),
        ],
    )

    batch = build_execution_batch(
        "prj_dependencies", run, ["sht_dependent", "sht_independent"]
    )

    assert batch.pending_shot_ids == ["sht_independent"]
    assert "sht_dependent" in batch.skipped_shots


def test_selected_source_makes_its_dependency_runnable_in_plan_order() -> None:
    run = ManagedRun(
        run_id="mrun_chain",
        project_id="prj_chain",
        plan_fingerprint="fp",
        steps=[
            RunStep(shot_id="sht_source"),
            RunStep(
                shot_id="sht_dependent",
                tail_from_shot_id="sht_source",
                tail_reason="Continue the pose",
            ),
        ],
    )

    batch = build_execution_batch(
        "prj_chain", run, ["sht_dependent", "sht_source"]
    )

    assert batch.pending_shot_ids == ["sht_source", "sht_dependent"]
    assert batch.skipped_shots == {}
    assert batch.tail_source_job_ids == {}


def test_newer_failed_job_does_not_hide_older_usable_source() -> None:
    project, source, dependent = project_with_tail_dependency()
    succeeded = save_h3_generation(
        project.id, source.id, JobStatus.succeeded, b"old"
    )
    save_h3_generation(project.id, source.id, JobStatus.failed, None)
    run = ManagedRun(
        run_id="mrun_history",
        project_id=project.id,
        plan_fingerprint="fp",
        steps=[
            RunStep(shot_id=source.id),
            RunStep(
                shot_id=dependent.id,
                tail_from_shot_id=source.id,
                tail_reason="Continue the pose",
            ),
        ],
    )

    batch = build_execution_batch(project.id, run, [dependent.id])

    assert batch.pending_shot_ids == [dependent.id]
    assert batch.tail_source_job_ids[dependent.id] == succeeded.id


def test_completed_job_is_preferred_over_a_newer_successful_generation() -> None:
    project, source, dependent = project_with_tail_dependency()
    managed = save_h3_generation(
        project.id, source.id, JobStatus.succeeded, b"managed"
    )
    save_h3_generation(project.id, source.id, JobStatus.succeeded, b"manual")
    run = ManagedRun(
        run_id="mrun_preferred",
        project_id=project.id,
        plan_fingerprint="fp",
        completed_job_ids={source.id: managed.id},
        steps=[
            RunStep(shot_id=source.id),
            RunStep(
                shot_id=dependent.id,
                tail_from_shot_id=source.id,
                tail_reason="Continue the pose",
            ),
        ],
    )

    assert latest_successful_video_job_id(
        project.id, source.id, managed.id
    ) == managed.id
    batch = build_execution_batch(project.id, run, [dependent.id])
    assert batch.tail_source_job_ids[dependent.id] == managed.id
