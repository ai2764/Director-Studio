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
    user_message_id: str,
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
        from ..brief import remember_directing_request
        remember_directing_request(project_id, user_feedback)
        actions.append("set_script")
        existing = refresh_shots()
        notes.append(
            f"Saved the script ({len(script)} characters). "
            + ("Existing shots, dialogue, references and production state were preserved. "
               "Saving script evidence does not request a storyboard replacement. "
               "Use the existing shot IDs for any requested local follow-up."
               if existing else "Review asset coverage or explicitly skip it before storyboarding; do not jump directly to composition.")
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
        if pending is None:
            raise ValueError("No pending storyboard replacement exists for this project")
        if args.get("proposal_id") != pending["id"]:
            raise ValueError("Storyboard proposal changed; confirm the currently displayed proposal")
        if not pending.get("request_message_id") or "changes" not in pending:
            raise ValueError("Legacy proposal needs a fresh validated preview and confirmation; existing shots were preserved")
        if pending.get("state") == "confirmed":
            if result_payloads is not None:
                result_payloads.append({"ok": True, "already_applied": True,
                    "proposal_id": pending["id"], "storyboard": pending.get("result")})
            notes.append("This storyboard proposal was already applied; no changes were repeated.")
            return True
        if pending.get("state") not in {"pending", "failed"}:
            raise ValueError("Storyboard proposal is stale or executing; refresh its status")
        if (
            not _confirms_storyboard_replacement(user_feedback)
            or user_message_id == pending.get("request_message_id")
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
        from ....core.managed_runs.store import _project_lock
        with _project_lock(project_id):
            latest = _load_storyboard_replacement(project_id)
            if latest != pending:
                raise ValueError("Storyboard proposal changed before confirmation")
            pending.update(state="executing", confirmation_message_id=user_message_id)
            _save_storyboard_replacement(project_id, pending)
        try:
            persisted = await svc.save_storyboard(
                project_id, submission.shots, submission.expected_script_hash,
                user_feedback=confirmed_feedback,
                requested_minimum_duration_s=float(pending.get("requested_minimum_duration_s") or 0.0),
            )
        except BaseException as exc:
            # Cancellation is not an Exception. Release the claim only when the
            # authored board is unchanged; an uncertain write requires review.
            unchanged = _storyboard_state_hash(refresh_shots()) == pending["storyboard_state_hash"]
            pending.update(state="failed" if unchanged else "stale", error=str(exc) or type(exc).__name__)
            _save_storyboard_replacement(project_id, pending)
            raise
        pending["state"] = "confirmed"
        pending["result"] = storyboard_snapshot(persisted)
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
        from ..brief import remember_directing_request
        current_shots = refresh_shots()
        if current_shots:
            preview = await svc.preview_storyboard(project_id, submission.shots,
                submission.expected_script_hash, user_feedback=user_feedback,
                requested_minimum_duration_s=requested_minimum_duration_s)
            by_id = {shot.id: shot for shot in preview}
            removed_dialogue = [{"shot_id": shot.id, "lines": shot.dialogue}
                for shot in current_shots if shot.dialogue and
                (shot.id not in by_id or any(line not in by_id[shot.id].dialogue for line in shot.dialogue))]
            changes = {"removed_shot_ids": [s.id for s in current_shots if s.id not in by_id],
                       "removed_dialogue": removed_dialogue}
            proposal = {
                "id": f"sbrep_{uuid.uuid4().hex[:12]}",
                "submission": submission.model_dump(mode="json", exclude_unset=True),
                "request_message": user_feedback,
                "request_message_id": user_message_id,
                "changes": changes,
                "requested_minimum_duration_s": requested_minimum_duration_s,
                "storyboard_state_hash": _storyboard_state_hash(current_shots),
                "shot_count": len(current_shots),
                "state": "pending",
            }
            from ....core.managed_runs.store import _project_lock
            with _project_lock(project_id):
                previous = _load_storyboard_replacement(project_id)
                if previous and previous.get("state") == "executing":
                    raise ValueError("Storyboard replacement is executing; wait for its outcome")
                if (previous and previous.get("state") == "pending"
                    and previous.get("request_message_id") and "changes" in previous
                    and previous["submission"] == proposal["submission"]
                    and previous["storyboard_state_hash"] == proposal["storyboard_state_hash"]):
                    proposal = previous
                _save_storyboard_replacement(project_id, proposal)
            remember_directing_request(project_id, user_feedback)
            warning = _STORYBOARD_REPLACEMENT_WARNING.format(
                shot_count=len(current_shots)
            )
            if removed_dialogue:
                warning += "\n将删除或替换的原对白：" + json.dumps(removed_dialogue, ensure_ascii=False)
            actions.append(f"propose_storyboard_replacement:{proposal['id']}")
            if result_payloads is not None:
                result_payloads.append(
                    {
                        "ok": True,
                        "confirmation_required": True,
                        "proposal_id": proposal["id"],
                        "changes": changes,
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
        from ..brief import remember_directing_request
        remember_directing_request(project_id, user_feedback)
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
        from ..brief import duration_budget
        revision = ShotRevisionSubmission.model_validate(args)
        persisted = svc.revise_shot(project_id, revision)
        actions.append("revise_shot")
        if result_payloads is not None:
            # Report the saved target, not a full board per edit (quadratic in
            # batch size). The complete storyboard remains in the project store.
            revised = next(shot for shot in persisted if shot.id == revision.shot_id)
            result_payloads.append(
                {"ok": True, "shot": storyboard_snapshot([revised])["shots"][0],
                 "duration_budget": duration_budget(project, persisted)}
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
        if refresh_shots():
            raise ValueError("Existing shots require a validated save_storyboard proposal and explicit replacement confirmation; legacy plan_shots cannot bypass it")
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
