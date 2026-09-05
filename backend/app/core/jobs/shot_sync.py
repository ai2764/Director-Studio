"""Sync project shots when pipeline jobs reach a terminal status.

C1: ref_frame succeeded → library layout (pending_review) + shot needs_review
C2: h3_ref2va terminal → shot status succeeded/failed when h3_job_id matches
"""

from __future__ import annotations

import logging

from ...pipelines.registry import get_pipeline
from ..projects.layouts import (
    LayoutReference,
    LayoutReviewStatus,
    mirror_legacy_layout_fields,
    sync_selected_layout_refs,
)
from ..projects.models import Shot, ShotStatus
from ..projects.store import list_projects, list_shots, load_shot, save_shot
from ..projects.transitions import ensure_layout_ref
from ..schemas import JobRecord, JobStatus

logger = logging.getLogger("director_studio.jobs.shot_sync")

_TERMINAL = frozenset(
    {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}
)

_H3_STATUS_MAP = {
    JobStatus.succeeded: ShotStatus.succeeded,
    JobStatus.failed: ShotStatus.failed,
    # No ShotStatus.cancelled — treat cancel as failed so the shot can re-run.
    JobStatus.cancelled: ShotStatus.failed,
}


def on_pipeline_job_terminal(job: JobRecord) -> None:
    """Hook after a pipeline job reaches succeeded/failed/cancelled."""
    if job.status not in _TERMINAL:
        return
    if job.pipeline_id in {"ref_frame", "gpt_ref_frame"}:
        _sync_ref_frame(job)
    elif job.pipeline_id == "h3_ref2va":
        _sync_h3_ref2va(job)


def _sync_ref_frame(job: JobRecord) -> None:
    """Sync a ref_frame terminal result onto its exact Layout reference."""
    shot, target = _find_ref_frame_layout(job)
    if shot is None:
        logger.warning(
            "ref_frame job %s terminal (%s) but no related shot found (params=%s)",
            job.id,
            job.status.value,
            {k: (job.params or {}).get(k) for k in ("shot_id", "project_id")},
        )
        return

    if (
        target is None
        and shot.ref_frame_job_id
        and shot.ref_frame_job_id != job.id
    ):
        logger.info(
            "skip ref_frame sync for job %s: shot %s bound to %s",
            job.id,
            shot.id,
            shot.ref_frame_job_id,
        )
        return

    if job.status != JobStatus.succeeded:
        if target is None:
            logger.info(
                "ref_frame job %s terminal (%s) has no Layout to mark",
                job.id,
                job.status.value,
            )
            return
        job_error = job.error or (
            "Layout generation was cancelled."
            if job.status == JobStatus.cancelled
            else "Layout generation failed."
        )
        if target.job_status == job.status and target.job_error == job_error:
            return
        updated_ref = target.model_copy(
            update={
                "job_status": job.status,
                "job_error": job_error,
                "selected_for_h3": False,
            }
        )
        save_shot(
            mirror_legacy_layout_fields(
                shot.model_copy(
                    update={
                        "layout_refs": [
                            updated_ref if item.id == target.id else item
                            for item in shot.layout_refs
                        ]
                    }
                )
            )
        )
        logger.info(
            "ref_frame job %s (%s) → shot %s Layout %s terminal",
            job.id,
            job.status.value,
            shot.id,
            target.id,
        )
        return

    # Idempotent: already promoted for this job with a layout asset.
    if target is not None and (
        target.asset_id
        and target.job_id == job.id
        and target.job_status == JobStatus.succeeded
        and target.review_status == LayoutReviewStatus.pending_review
    ):
        return
    if target is None and (
        shot.status == ShotStatus.needs_review
        and shot.layout_asset_id
        and shot.ref_frame_job_id == job.id
        and shot.layout_review_status == "pending_review"
    ):
        return

    try:
        asset_id = job.library_asset_id
        if not asset_id:
            pipe = get_pipeline(job.pipeline_id)
            asset = pipe.save_to_library(
                job,
                name=job.name or shot.title or "Layout",
                notes=job.notes or shot.script_beat or None,
            )
            asset_id = asset.id
    except Exception as exc:
        logger.exception(
            "save_to_library failed for ref_frame job %s; "
            "marking shot %s Layout terminal",
            job.id,
            shot.id,
        )
        if target is not None:
            updated_ref = target.model_copy(
                update={
                    "job_status": JobStatus.failed,
                    "job_error": (
                        "Failed to save generated Layout to Asset Library: "
                        f"{exc}"
                    ),
                    "selected_for_h3": False,
                }
            )
            save_shot(
                mirror_legacy_layout_fields(
                    shot.model_copy(
                        update={
                            "layout_refs": [
                                updated_ref if item.id == target.id else item
                                for item in shot.layout_refs
                            ]
                        }
                    )
                )
            )
        return

    if target is not None:
        append_to_active = target.activation_mode == "append"
        updated_ref = target.model_copy(
            update={
                "asset_id": asset_id,
                "job_status": JobStatus.succeeded,
                "job_error": "",
                "review_status": LayoutReviewStatus.pending_review,
                "selected_for_h3": True,
            }
        )
        working = shot.model_copy(
            update={
                "layout_refs": [
                    updated_ref
                    if item.id == target.id
                    else (
                        item
                        if append_to_active
                        else item.model_copy(update={"selected_for_h3": False})
                    )
                    for item in shot.layout_refs
                ],
                "status": ShotStatus.needs_review,
            }
        )
        working = mirror_legacy_layout_fields(
            working,
            compatibility_primary_layout_id=target.id,
        )
    else:
        # Pre-LayoutReference jobs retain the original compatibility update.
        working = shot.model_copy(
            update={
                "layout_asset_id": asset_id,
                "layout_review_status": "pending_review",
                "status": ShotStatus.needs_review,
                "ref_frame_job_id": job.id,
            }
        )
    try:
        if target is not None:
            updated = sync_selected_layout_refs(working)
        else:
            refs = ensure_layout_ref(working) if working.layout_asset_id else working.refs
            updated = working.model_copy(update={"refs": refs})
    except ValueError:
        logger.exception(
            "could not bind ref_frame job %s as a layout ref for shot %s",
            job.id,
            shot.id,
        )
        return
    save_shot(updated)
    logger.info(
        "ref_frame job %s → shot %s needs_review layout=%s",
        job.id,
        shot.id,
        asset_id,
    )


def _find_ref_frame_layout(
    job: JobRecord,
) -> tuple[Shot | None, LayoutReference | None]:
    """Resolve the exact LayoutReference, with a fallback for pre-migration jobs."""
    params = job.params or {}
    shot_id = params.get("shot_id")
    project_id = params.get("project_id")
    layout_ref_id = params.get("layout_ref_id")

    candidates: list[Shot] = []
    if isinstance(project_id, str) and isinstance(shot_id, str) and project_id and shot_id:
        shot = load_shot(project_id, shot_id)
        if shot is not None:
            candidates.append(shot)
    if not candidates:
        for project in list_projects():
            candidates.extend(list_shots(project.id))

    for shot in candidates:
        target = None
        if isinstance(layout_ref_id, str) and layout_ref_id:
            target = next(
                (item for item in shot.layout_refs if item.id == layout_ref_id),
                None,
            )
            if target is not None and target.job_id not in {None, job.id}:
                continue
        if target is None:
            target = next(
                (item for item in shot.layout_refs if item.job_id == job.id),
                None,
            )
        if target is not None:
            return shot, target

        # Old jobs had only the shot-level binding. Params may identify an
        # unbound shot during very early runner persistence.
        if not layout_ref_id and shot.ref_frame_job_id in {None, job.id}:
            return shot, None
    return None, None


def _sync_h3_ref2va(job: JobRecord) -> None:
    """Map h3_ref2va terminal job status onto the related shot."""
    shot = _find_related_shot(job, job_id_field="h3_job_id")
    if shot is None:
        logger.warning(
            "h3_ref2va job %s terminal (%s) but no related shot found",
            job.id,
            job.status.value,
        )
        return

    if shot.h3_job_id and shot.h3_job_id != job.id:
        logger.info(
            "skip h3 sync for job %s: shot %s bound to %s",
            job.id,
            shot.id,
            shot.h3_job_id,
        )
        return

    new_status = _H3_STATUS_MAP.get(job.status)
    if new_status is None:
        return

    if shot.status == new_status and shot.h3_job_id == job.id:
        return

    updated = shot.model_copy(
        update={
            "status": new_status,
            "h3_job_id": job.id,
        }
    )
    save_shot(updated)
    logger.info(
        "h3_ref2va job %s (%s) → shot %s %s",
        job.id,
        job.status.value,
        shot.id,
        new_status.value,
    )


def _find_related_shot(job: JobRecord, *, job_id_field: str) -> Shot | None:
    """Resolve shot from job.params (shot_id/project_id) or scan by job id field."""
    params = job.params or {}
    shot_id = params.get("shot_id")
    project_id = params.get("project_id")

    if isinstance(project_id, str) and isinstance(shot_id, str) and project_id and shot_id:
        shot = load_shot(project_id, shot_id)
        if shot is not None:
            bound = getattr(shot, job_id_field, None)
            # Prefer params when unbound or bound to this job; refuse other jobs.
            if bound is None or bound == job.id:
                return shot
            return None

    # Fallback: scan all projects for matching job id on the shot.
    for project in list_projects():
        for shot in list_shots(project.id):
            if getattr(shot, job_id_field, None) == job.id:
                return shot
    return None
