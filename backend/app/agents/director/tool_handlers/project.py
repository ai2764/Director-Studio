"""Project script, coverage, storyboard, and planning tools."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ....core.projects.models import AssetCoverageReview, Project, Shot
from ....core.projects.store import save_project
from ..planner import (
    ShotRefsPatchSubmission,
    ShotRevisionSubmission,
    ShotSceneRefSelection,
    StoryboardSubmission,
)
from ..service import _script_hash

logger = logging.getLogger("director_studio.director.tool_handlers.project")


async def handle_project_tool(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    project: Project,
    svc: Any,
    actions: list[str],
    notes: list[str],
    result_payloads: list[dict[str, Any]] | None,
    user_feedback: str,
    requested_minimum_duration_s: float,
    refresh_shots: Callable[[], list[Shot]],
    storyboard_snapshot: Callable[[list[Shot]], dict[str, Any]],
    script_locked_message: str,
) -> bool:
    """Handle Project/Storyboard tools and return whether the name was recognized."""
    if name == "set_script":
        if project.script_locked:
            notes.append(f"set_script rejected: {script_locked_message}")
            return True
        script = str(args.get("script") or args.get("script_text") or "").strip()
        if not script:
            notes.append("set_script: missing script")
            return True
        save_project(project.model_copy(update={"script_text": script}))
        actions.append("set_script")
        notes.append(
            f"Saved the script ({len(script)} characters). "
            "Review asset coverage or explicitly skip it before storyboarding; "
            "do not jump directly to composition."
        )
        return True

    if name == "review_asset_coverage":
        from ....core.projects.models import AssetCoverageReviewSubmission

        submission = AssetCoverageReviewSubmission.model_validate(args)
        current_hash = _script_hash(project.script_text or "")
        if submission.expected_script_hash != current_hash:
            raise ValueError(
                "The script changed since this asset coverage review was authored; "
                f"expected {submission.expected_script_hash}, current {current_hash}."
            )
        review = AssetCoverageReview(
            script_hash=current_hash,
            status=submission.status,
            recommendations=submission.recommendations,
            notes=submission.notes,
        )
        save_project(project.model_copy(update={"asset_coverage_review": review}))
        actions.append("review_asset_coverage")
        if result_payloads is not None:
            result_payloads.append(
                {"asset_coverage_review": review.model_dump(mode="json")}
            )
        notes.append(
            f"Saved the {review.status} asset coverage review with "
            f"{len(review.recommendations)} recommendation"
            f"{'s' if len(review.recommendations) != 1 else ''}; "
            "it does not block storyboard authoring."
        )
        return True

    if name == "save_storyboard":
        submission = StoryboardSubmission.model_validate(args)
        persisted = await svc.save_storyboard(
            project_id,
            submission.shots,
            submission.expected_script_hash,
            user_feedback=user_feedback,
            requested_minimum_duration_s=requested_minimum_duration_s,
        )
        actions.append("save_storyboard")
        if result_payloads is not None:
            result_payloads.append({"storyboard": storyboard_snapshot(persisted)})
        notes.append(
            f"Saved the complete ordered storyboard: {len(persisted)} "
            f"shot{'s' if len(persisted) != 1 else ''}."
        )
        return True

    if name == "patch_shot_refs":
        submission = ShotRefsPatchSubmission.model_validate(args)
        persisted = svc.patch_shot_refs(project_id, submission.updates)
        actions.append("patch_shot_refs")
        if result_payloads is not None:
            result_payloads.append({"storyboard": storyboard_snapshot(persisted)})
        notes.append(
            f"Updated Picture bindings on {len(submission.updates)} "
            f"shot{'s' if len(submission.updates) != 1 else ''}; "
            "all story fields were preserved."
        )
        return True

    if name == "revise_shot":
        revision = ShotRevisionSubmission.model_validate(args)
        persisted = svc.revise_shot(project_id, revision)
        actions.append("revise_shot")
        if result_payloads is not None:
            result_payloads.append({"storyboard": storyboard_snapshot(persisted)})
        notes.append(
            f"Revised exactly one Shot ({revision.shot_id}); neighboring Shots, "
            "references, and Layouts were preserved. Its stale prompt and active "
            "H3 link were cleared for regeneration."
        )
        return True

    if name == "set_shot_scene_ref":
        selection = ShotSceneRefSelection.model_validate(args)
        persisted = svc.set_shot_scene_ref(
            project_id,
            shot_id=selection.shot_id,
            scene_asset_id=selection.scene_asset_id,
            file_key=selection.file_key,
        )
        actions.append("set_shot_scene_ref")
        if result_payloads is not None:
            result_payloads.append({"storyboard": storyboard_snapshot(persisted)})
        notes.append(
            f"Updated only the scene Picture binding on shot {selection.shot_id} "
            f"to {selection.scene_asset_id}:{selection.file_key}; all other refs "
            "and story fields were preserved. Existing Layouts and H3 prompts "
            "were left unchanged and may still reflect the previous scene."
        )
        return True

    if name in {"plan_shots", "plan"}:
        if not (project.script_text or "").strip():
            notes.append("Cannot plan shots: the project has no script")
            return True
        actions.append("plan")
        try:
            await svc.plan_project(project_id)
        except Exception as exc:
            logger.exception("plan_shots tool failed")
            notes.append(f"Shot planning failed: {exc}")
            return True
        shots = refresh_shots()
        if not shots:
            notes.append(
                "Shot planning failed: no shots were generated. "
                "Retry or inspect the Ollama output."
            )
        else:
            titles = ", ".join(shot.title for shot in shots[:6])
            extra = f": {titles}" if titles else ""
            notes.append(
                f"Shot planning complete: {len(shots)} "
                f"shot{'s' if len(shots) != 1 else ''}{extra}. "
                "The previous shot plan was removed."
            )
        return True

    return False
