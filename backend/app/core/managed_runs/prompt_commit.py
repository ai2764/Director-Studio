"""Publish reviewed camera refinements and their managed execution version together."""
from __future__ import annotations

from . import store
from .context import managed_turn_scope
from ..projects.store import load_shot, save_shot, save_shot_if_current


CAMERA_REFINEMENT_FIELDS = frozenset({"shot_type", "camera_angle", "camera_motion", "composition"})
_DERIVED_FIELDS = frozenset({"prompt_sections", "meta", "status", "blocked_reasons", "h3_job_id"})


def save_reviewed_tail_prompt(candidate, *, check_current):
    """Compare-and-publish under the existing process-local project lock.

    Only a coordinator-issued current step can adopt a reviewed camera refinement.
    Story, sources, refs, duration and other authored fields require a new decision.
    Files are individually atomic; failed run writes roll back the shot. A process
    crash between writes remains fail-closed through the fingerprint check.
    """
    scope = managed_turn_scope.get()
    if scope is None:
        save_shot_if_current(candidate, check_current=check_current)
        return
    with store._project_lock(candidate.project_id):
        check_current()
        if scope.project_id != candidate.project_id or scope.shot_id != candidate.id:
            raise ValueError("Managed prompt target changed before publication")
        run = store.load_run(scope.project_id, scope.run_id)
        step = store.current_step(run) if run is not None else None
        if (run is None or run.state != "active" or run.current_job_id
                or not scope.event_id or run.pending_event_id != scope.event_id
                or step is None or step.shot_id != candidate.id):
            raise ValueError("Managed run changed or stopped before prompt publication")
        if store._fingerprint(scope.project_id) != run.current_fingerprint:
            raise ValueError("Shot brief or references changed during managed prompt review")
        current = load_shot(candidate.project_id, candidate.id)
        if current is None:
            raise ValueError("Managed shot changed before prompt publication")
        before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
        changed = {key for key in before if before[key] != after[key]}
        unauthorized = changed - CAMERA_REFINEMENT_FIELDS - _DERIVED_FIELDS
        if unauthorized:
            raise ValueError("Managed prompt revision needs your decision before changing authored fields: "
                             + ", ".join(sorted(unauthorized)))
        camera_changes = changed & CAMERA_REFINEMENT_FIELDS
        review = candidate.meta.get("material_review") or {}
        if camera_changes and (review.get("prompt_review") or {}).get("valid") is not True:
            raise ValueError("Managed camera refinement needs an accepted continuity review before publication")
        save_shot(candidate)
        if not camera_changes:
            return
        try:
            fingerprint = store._fingerprint(candidate.project_id)
            receipt = {
                "kind": "prompt_refinement", "shot_id": candidate.id, "event_id": scope.event_id,
                "before_fingerprint": run.current_fingerprint, "after_fingerprint": fingerprint,
                "reviewed_plan_fingerprint": run.plan_fingerprint,
                "changes": {key: {"before": before[key], "after": after[key]}
                            for key in sorted(camera_changes)},
                "reason": (review.get("decision") or {}).get("reason", ""),
            }
            store._save_run(run.model_copy(update={
                "current_fingerprint": fingerprint, "plan_fingerprint": fingerprint,
                "recovery_history": [*run.recovery_history, receipt],
            }))
        except BaseException:
            save_shot(current)
            raise
