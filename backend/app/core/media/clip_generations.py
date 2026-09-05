"""Resolve Director H3 clip generations for tail-frame extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..jobs.store import job_dir, list_jobs
from ..schemas import JobRecord, JobStatus, OutputSlot

H3_PIPELINE_ID = "h3_ref2va"
_AMBIGUOUS_STATUSES = frozenset(
    {
        JobStatus.queued,
        JobStatus.uploading,
        JobStatus.running,
        JobStatus.cancelled,
        JobStatus.failed,
    }
)
_VERSION_RE = re.compile(r"^(?:v)?(\d+)$", re.IGNORECASE)


class ClipGenerationError(ValueError):
    """Selector or output cannot be resolved to a usable clip."""


class ClipGenerationAmbiguous(Exception):
    """`latest` is blocked by a newer active or failed generation."""

    def __init__(
        self,
        message: str,
        *,
        latest_succeeded_job_id: str,
        blocking_job_id: str,
        blocking_status: JobStatus,
    ) -> None:
        super().__init__(message)
        self.latest_succeeded_job_id = latest_succeeded_job_id
        self.blocking_job_id = blocking_job_id
        self.blocking_status = blocking_status


@dataclass(frozen=True)
class ResolvedClip:
    source_shot_id: str
    source_job_id: str
    source_generation: int
    output_kind: Literal["enhanced", "raw"]
    output_key: Literal["video", "video_raw"]
    source_filename: str
    path: Path


def list_shot_h3_generations(project_id: str, source_shot_id: str) -> list[JobRecord]:
    """Return H3 jobs for one Director shot, sorted by (created_at, id) ascending."""
    jobs = list_jobs(limit=None, pipeline_id=H3_PIPELINE_ID, project_id=project_id)
    matched = [
        job
        for job in jobs
        if (job.params or {}).get("shot_id") == source_shot_id
    ]
    matched.sort(key=lambda job: (job.created_at, job.id))
    return matched


def resolve_source_clip(
    *,
    project_id: str,
    source_shot_id: str,
    source_version: str | None,
    source_job_id: str | None,
    output_kind: Literal["enhanced", "raw"] | None,
) -> ResolvedClip:
    version = (source_version or "").strip() or None
    job_id = (source_job_id or "").strip() or None
    if version is None and job_id is None:
        raise ClipGenerationError(
            "clip selector required: supply source_version or source_job_id"
        )

    generations = list_shot_h3_generations(project_id, source_shot_id)
    if not generations:
        raise ClipGenerationError(
            f"no H3 generations found for shot {source_shot_id} in project {project_id}"
        )

    by_id = {job.id: (index, job) for index, job in enumerate(generations, start=1)}

    selected_index: int | None = None
    selected_job: JobRecord | None = None

    if version is not None:
        if version.lower() == "latest":
            selected_index, selected_job = _resolve_latest(generations)
        else:
            selected_index, selected_job = _resolve_version_number(
                generations, version
            )

    if job_id is not None:
        if job_id not in by_id:
            raise ClipGenerationError(
                f"H3 job {job_id} is not a generation of shot {source_shot_id}"
            )
        job_index, job_record = by_id[job_id]
        if selected_job is not None and selected_job.id != job_record.id:
            raise ClipGenerationError(
                f"conflicting selectors: source_version={version!r} resolves to "
                f"{selected_job.id}, source_job_id={job_id}"
            )
        selected_index = job_index
        selected_job = job_record

    assert selected_job is not None and selected_index is not None
    return _resolve_output(
        selected_job,
        source_shot_id=source_shot_id,
        source_generation=selected_index,
        output_kind=output_kind,
    )


def _resolve_version_number(
    generations: list[JobRecord], version: str
) -> tuple[int, JobRecord]:
    match = _VERSION_RE.fullmatch(version.strip())
    if match is None:
        raise ClipGenerationError(
            f"invalid source_version {version!r}; expected 'latest', 'vN', or 'N'"
        )
    index = int(match.group(1))
    if index < 1 or index > len(generations):
        raise ClipGenerationError(
            f"source_version v{index} is out of range; shot has "
            f"{len(generations)} generation(s)"
        )
    return index, generations[index - 1]


def _resolve_latest(generations: list[JobRecord]) -> tuple[int, JobRecord]:
    latest_index: int | None = None
    latest_job: JobRecord | None = None
    for index, job in enumerate(generations, start=1):
        if job.status != JobStatus.succeeded:
            continue
        try:
            _peek_usable_output(job, output_kind=None)
        except ClipGenerationError:
            continue
        latest_index = index
        latest_job = job

    if latest_job is None or latest_index is None:
        raise ClipGenerationError(
            "no succeeded H3 generation with a usable video output"
        )

    for job in generations[latest_index:]:
        if job.status in _AMBIGUOUS_STATUSES:
            raise ClipGenerationAmbiguous(
                f"latest is ambiguous: newer generation {job.id} is {job.status.value}",
                latest_succeeded_job_id=latest_job.id,
                blocking_job_id=job.id,
                blocking_status=job.status,
            )
    return latest_index, latest_job


def _peek_usable_output(
    job: JobRecord, *, output_kind: Literal["enhanced", "raw"] | None
) -> tuple[Literal["enhanced", "raw"], Literal["video", "video_raw"], str, Path]:
    kind, key = _select_output_key(job, output_kind=output_kind)
    filename, path = _materialized_output_path(job, key)
    return kind, key, filename, path


def _select_output_key(
    job: JobRecord, *, output_kind: Literal["enhanced", "raw"] | None
) -> tuple[Literal["enhanced", "raw"], Literal["video", "video_raw"]]:
    outputs = job.outputs or {}
    has_video = "video" in outputs
    has_raw = "video_raw" in outputs

    if output_kind == "enhanced":
        if not has_video:
            raise ClipGenerationError(
                f"job {job.id} has no enhanced (video) output"
            )
        return "enhanced", "video"
    if output_kind == "raw":
        if not has_raw:
            raise ClipGenerationError(
                f"job {job.id} has no raw (video_raw) output"
            )
        return "raw", "video_raw"

    if has_video:
        return "enhanced", "video"
    if has_raw:
        return "raw", "video_raw"
    raise ClipGenerationError(f"job {job.id} has neither video nor video_raw output")


def _materialized_output_path(
    job: JobRecord, key: Literal["video", "video_raw"]
) -> tuple[str, Path]:
    slot = (job.outputs or {}).get(key)
    if slot is None:
        raise ClipGenerationError(f"job {job.id} is missing output slot {key}")

    filename = _slot_filename(slot)
    if not filename:
        raise ClipGenerationError(
            f"job {job.id} output {key} has no stored filename"
        )

    out_dir = (job_dir(job.id, project_id=job.project_id) / "outputs").resolve()
    candidate = (out_dir / filename).resolve()
    try:
        candidate.relative_to(out_dir)
    except ValueError as exc:
        raise ClipGenerationError(
            f"job {job.id} output path escapes outputs directory: {filename}"
        ) from exc

    if not candidate.is_file():
        raise ClipGenerationError(
            f"job {job.id} output file missing: {filename}"
        )
    return filename, candidate


def _slot_filename(slot: OutputSlot) -> str:
    if slot.filename:
        return Path(slot.filename).name
    if slot.path:
        return Path(slot.path).name
    return ""


def _resolve_output(
    job: JobRecord,
    *,
    source_shot_id: str,
    source_generation: int,
    output_kind: Literal["enhanced", "raw"] | None,
) -> ResolvedClip:
    if job.status != JobStatus.succeeded:
        raise ClipGenerationError(
            f"job {job.id} is {job.status.value}, not succeeded"
        )
    kind, key, filename, path = _peek_usable_output(job, output_kind=output_kind)
    return ResolvedClip(
        source_shot_id=source_shot_id,
        source_job_id=job.id,
        source_generation=source_generation,
        output_kind=kind,
        output_key=key,
        source_filename=filename,
        path=path,
    )
