"""Publish a reviewed planning correction without overwriting concurrent edits."""
from __future__ import annotations

from . import store
from .prompt_commit import CAMERA_REFINEMENT_FIELDS
from ..projects.models import PromptSections, ShotStatus
from ..projects.store import save_shot
from ..jobs.store import load_job
from ..schemas import JobStatus


def publish_plan(project_id, originals, candidates, steps, receipt, check_current):
    with store._project_lock(project_id):
        check_current()
        store._validate_steps(project_id, steps)
        changed = []
        for original, candidate in zip(originals, candidates, strict=True):
            before, after = original.model_dump(mode="json"), candidate.model_dump(mode="json")
            fields = {key for key in before if before[key] != after[key]}
            if fields - CAMERA_REFINEMENT_FIELDS:
                raise ValueError("Planning repair may change only reviewed camera fields")
            if not fields:
                continue
            if not receipt or receipt.get("review", {}).get("valid") is not True:
                raise ValueError("Planning refinement needs an accepted review")
            job = load_job(original.h3_job_id) if original.h3_job_id else None
            if (original.status in {ShotStatus.queued, ShotStatus.running}
                    or job and job.status in {JobStatus.queued, JobStatus.uploading, JobStatus.running}):
                raise ValueError("Cannot refine a Shot while its generation is active")
            meta = dict(original.meta)
            if original.h3_job_id:
                meta["superseded_h3_job_ids"] = list(dict.fromkeys([
                    *meta.get("superseded_h3_job_ids", []), original.h3_job_id]))
            for key in ("prompt_layout_signature", "prompt_picture_signature", "prompt_voice_signature"):
                meta[key] = ""
            # Keep actual user revision requests. Mark old prompt evidence stale.
            meta["material_review_pending"] = True
            meta["managed_planning_refinement"] = receipt
            changed.append((original, candidate.model_copy(update={
                "prompt_sections": PromptSections(), "h3_job_id": None,
                "status": ShotStatus.needs_review, "blocked_reasons": [], "meta": meta})))
        if changed and any(run.state in {"active", "stopping"} for run in store.list_runs(project_id)):
            raise ValueError("Stop the active managed run before refining its plan")
        written = []
        try:
            for original, candidate in changed:
                save_shot(candidate)
                written.append(original)
            return store.create_draft(project_id, steps, recovery_history=[receipt] if receipt else [])
        except BaseException:
            for original in reversed(written):
                save_shot(original)
            raise
