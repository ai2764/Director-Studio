"""Project script, coverage, storyboard, and planning tools."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from typing import Any

from ....core.projects.models import AssetCoverageReview, Project, Shot
from ....core.projects.store import project_dir, save_project
from ..intent import normalize_text
from ..planner import (
    AppendShotSubmission,
    ShotRefsPatchSubmission,
    ShotRevisionSubmission,
    ShotSceneRefSelection,
    StoryboardSubmission,
)
from ..service import _script_hash

logger = logging.getLogger("director_studio.director.tool_handlers.project")

_STORYBOARD_REPLACEMENT_WARNING = (
    "保存新的 storyboard 会清除当前全部 {shot_count} 个 shots，并用新计划全量重写。"
    "当前没有修改任何 shot。若要继续，请在下一条消息中明确回复："
    "确认清除并重写全部 shots"
)


def _storyboard_replacement_path(project_id: str):
    return project_dir(project_id) / "agent" / "pending_storyboard_replacement.json"


def _load_storyboard_replacement(project_id: str) -> dict[str, Any] | None:
    path = _storyboard_replacement_path(project_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save_storyboard_replacement(project_id: str, proposal: dict[str, Any]) -> None:
    path = _storyboard_replacement_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(proposal, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _storyboard_state_hash(shots: list[Shot]) -> str:
    payload = [shot.model_dump(mode="json") for shot in shots]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _confirms_storyboard_replacement(message: str) -> bool:
    text = normalize_text(message)
    return bool(
        re.fullmatch(
            r"(?:我)?(?:确认|同意)(?:清除|删除)(?:并)?(?:重写|替换)"
            r"(?:全部|所有)\s*(?:shots?|镜头)[。.!！\s]*|"
            r"(?:i\s+)?(?:confirm|agree\s+to)\s+(?:clear|delete|remove)\s+"
            r"all\s+shots\s+and\s+(?:rewrite|replace)\s+(?:the\s+)?"
            r"(?:storyboard|shots)[。.!！\s]*",
            text,
            flags=re.IGNORECASE,
        )
    )


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

    if name == "append_shot":
        shot = svc.append_shot(project_id, AppendShotSubmission.model_validate(args))
        actions.append("append_shot")
        if result_payloads is not None:
            result_payloads.append(
                {
                    "ok": True,
                    "shot": storyboard_snapshot([shot])["shots"][0],
                    "concludes_turn": True,
                    "reply": "Appended 1 new shot at the end.",
                }
            )
        notes.append(f"Appended one Shot ({shot.id}) at the end; all existing Shots and production state were preserved.")
        return True

    if name == "confirm_storyboard_replacement":
        pending = _load_storyboard_replacement(project_id)
        if pending is None or pending.get("state") != "pending":
            raise ValueError("No pending storyboard replacement exists for this project")
        if (
            not _confirms_storyboard_replacement(user_feedback)
            or normalize_text(user_feedback)
            == normalize_text(str(pending.get("request_message") or ""))
        ):
            raise ValueError(
                "请在后续消息中明确回复“确认清除并重写全部 shots”；"
                "ok、继续或附带修改要求都不会授权全量替换。"
            )
        current_shots = refresh_shots()
        if _storyboard_state_hash(current_shots) != pending.get("storyboard_state_hash"):
            pending["state"] = "stale"
            _save_storyboard_replacement(project_id, pending)
            raise ValueError(
                "The storyboard changed after the replacement warning. "
                "Review the current shots and propose the replacement again."
            )
        submission = StoryboardSubmission.model_validate(pending["submission"])
        confirmed_feedback = (
            f"{pending.get('request_message', '')}\n\n"
            f"Explicit destructive confirmation: {user_feedback}"
        ).strip()
        persisted = await svc.save_storyboard(
            project_id,
            submission.shots,
            submission.expected_script_hash,
            user_feedback=confirmed_feedback,
            requested_minimum_duration_s=float(
                pending.get("requested_minimum_duration_s") or 0.0
            ),
        )
        pending["state"] = "confirmed"
        _save_storyboard_replacement(project_id, pending)
        actions.append("save_storyboard")
        if result_payloads is not None:
            result_payloads.append({"storyboard": storyboard_snapshot(persisted)})
        notes.append(
            f"Saved the confirmed complete storyboard replacement: {len(persisted)} "
            f"shot{'s' if len(persisted) != 1 else ''}."
        )
        return True

    if name == "save_storyboard":
        submission = StoryboardSubmission.model_validate(args)
        current_shots = refresh_shots()
        if current_shots:
            proposal = {
                "id": f"sbrep_{uuid.uuid4().hex[:12]}",
                "submission": submission.model_dump(mode="json"),
                "request_message": user_feedback,
                "requested_minimum_duration_s": requested_minimum_duration_s,
                "storyboard_state_hash": _storyboard_state_hash(current_shots),
                "shot_count": len(current_shots),
                "state": "pending",
            }
            _save_storyboard_replacement(project_id, proposal)
            warning = _STORYBOARD_REPLACEMENT_WARNING.format(
                shot_count=len(current_shots)
            )
            actions.append(f"propose_storyboard_replacement:{proposal['id']}")
            if result_payloads is not None:
                result_payloads.append(
                    {
                        "ok": True,
                        "confirmation_required": True,
                        "proposal_id": proposal["id"],
                        "concludes_turn": True,
                        "reply": warning,
                    }
                )
            notes.append(warning)
            return True
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
            # Report the saved target, not a full board per edit (quadratic in
            # batch size). The complete storyboard remains in the project store.
            revised = next(shot for shot in persisted if shot.id == revision.shot_id)
            result_payloads.append(
                {"ok": True, "shot": storyboard_snapshot([revised])["shots"][0]}
            )
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
            actions.append("plan")
            titles = ", ".join(shot.title for shot in shots[:6])
            extra = f": {titles}" if titles else ""
            notes.append(
                f"Shot planning complete: {len(shots)} "
                f"shot{'s' if len(shots) != 1 else ''}{extra}. "
                "The previous shot plan was removed."
            )
        return True

    return False
