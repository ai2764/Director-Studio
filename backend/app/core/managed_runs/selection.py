"""Build one selected managed-run execution batch from an immutable plan."""

from __future__ import annotations

from dataclasses import dataclass

from ..media.clip_generations import (
    ClipGenerationError,
    list_shot_h3_generations,
    resolve_source_clip,
)
from .models import ManagedRun


@dataclass(frozen=True)
class ExecutionBatch:
    selected_shot_ids: list[str]
    pending_shot_ids: list[str]
    skipped_shots: dict[str, str]
    tail_source_job_ids: dict[str, str]


def latest_successful_video_job_id(
    project_id: str,
    shot_id: str,
    preferred_job_id: str | None = None,
) -> str | None:
    """Return a usable successful H3 Job, preferring the run's recorded Job."""
    ordered = list_shot_h3_generations(project_id, shot_id)
    by_id = {job.id: job for job in ordered}
    candidates = []
    if preferred_job_id and preferred_job_id in by_id:
        candidates.append(by_id[preferred_job_id])
    candidates.extend(
        job for job in reversed(ordered) if job.id != preferred_job_id
    )
    for job in candidates:
        try:
            resolve_source_clip(
                project_id=project_id,
                source_shot_id=shot_id,
                source_version=None,
                source_job_id=job.id,
                output_kind=None,
            )
        except ClipGenerationError:
            continue
        return job.id
    return None


def build_execution_batch(
    project_id: str,
    run: ManagedRun,
    requested_shot_ids: list[str],
) -> ExecutionBatch:
    """Normalize selection and skip only targets with unavailable tail sources."""
    plan_ids = [step.shot_id for step in run.steps]
    if len(requested_shot_ids) != len(set(requested_shot_ids)):
        raise ValueError("selected Shot IDs contain a duplicate")
    unknown = sorted(set(requested_shot_ids) - set(plan_ids))
    if unknown:
        raise ValueError(
            "selected Shot IDs are outside this plan: " + ", ".join(unknown)
        )

    requested = set(requested_shot_ids)
    selected = [shot_id for shot_id in plan_ids if shot_id in requested]
    available_jobs = {
        shot_id: job_id
        for shot_id in plan_ids
        if (
            job_id := latest_successful_video_job_id(
                project_id,
                shot_id,
                run.completed_job_ids.get(shot_id),
            )
        )
    }
    producible = set(available_jobs)
    pending: list[str] = []
    skipped: dict[str, str] = {}
    tail_jobs: dict[str, str] = {}
    for step in run.steps:
        if step.shot_id not in requested:
            continue
        source = step.tail_from_shot_id
        if source and source not in producible:
            skipped[step.shot_id] = (
                f"Planned tail source {source} has no successful video and "
                "is not runnable earlier in this selection"
            )
            continue
        if source and source not in requested:
            tail_jobs[step.shot_id] = available_jobs[source]
        pending.append(step.shot_id)
        producible.add(step.shot_id)
    return ExecutionBatch(selected, pending, skipped, tail_jobs)
