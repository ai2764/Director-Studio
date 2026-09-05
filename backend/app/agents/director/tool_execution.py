"""Execution of validated Director native-tool calls."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ...config import settings
from ...core.library.store import load_asset
from ...core.projects.layouts import (
    GptLayoutBrief,
    LayoutBrief,
    LayoutReviewStatus,
    LayoutSourceRef,
    mirror_legacy_layout_fields,
    sync_selected_layout_refs,
)
from ...core.projects.models import (
    AssetCoverageReview,
    AssetCoverageReviewSubmission,
    Project,
    RefRole,
    Shot,
    ShotStatus,
)
from ...core.projects.store import (
    list_shots,
    load_project,
    load_shot,
    save_project,
    save_shot,
)
from ...core.projects.transitions import (
    apply_transition,
    review_layout_reference,
    select_layout_reference,
)
from ...core.schemas import JobStatus
from .intent import (
    actor_acceptance_intent,
    normalize_text,
    resolve_shot,
    validate_gpt_generation_prompt,
)
from .planner import ShotRefsPatchSubmission, StoryboardSubmission
from .service import DirectorService
from .tool_schema import IMAGE_TOOLS, STORYBOARD_TOOLS
from .tool_handlers.actor import handle_actor_tool
from .tool_handlers.layout import handle_layout_tool
from .tool_handlers.library import handle_library_tool
from .tool_handlers.media import handle_media_tool
from .tool_handlers.casting import handle_casting_tool
from .tool_handlers.project import handle_project_tool

logger = logging.getLogger("director_studio.director.tool_execution")
ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]
_SCRIPT_LOCKED_MESSAGE = (
    "The screenplay is locked and cannot be changed during storyboard work."
)


@dataclass(frozen=True)
class ToolExecutionRuntime:
    create_job: Callable[..., Any]
    load_job: Callable[..., Any]
    start_pipeline_job: Callable[..., Awaitable[Any]]
    await_pipeline_job: Callable[..., Awaitable[Any]]
    get_pipeline: Callable[..., Any]
    emit: Callable[..., Awaitable[None]]
    mark_layout_review: Callable[..., None]
    approve_layout_with_prompt: Callable[..., Awaitable[Any]]
    explicit_layout_queue_note: Callable[..., str]
    extracted_tail_frame_image: Callable[..., Any]
    status_summary: Callable[..., str]
    storyboard_snapshot: Callable[..., dict[str, Any]]
    storyboard_budget_factory: Callable[[], Any]
    gpt_tool_error: type[ValueError]
    chat_image_factory: Callable[..., Any]


async def execute_tools(
    *,
    runtime: ToolExecutionRuntime,
    project_id: str,
    tools: list[dict[str, Any]],
    svc: DirectorService,
    actions: list[str],
    on_progress: ProgressFn | None = None,
    result_payloads: list[dict[str, Any]] | None = None,
    user_feedback: str = "",
    requested_minimum_duration_s: float = 0.0,
    storyboard_budget: Any | None = None,
    images: list[Any] | None = None,
    user_uploads: list[dict[str, Any]] | None = None,
) -> tuple[list[str], set[str]]:
    """Execute tool list; return (note lines for LLM/user, shot ids touched for images)."""
    notes: list[str] = []
    touched: set[str] = set()
    prompt_written_shot_ids: set[str] = set()
    budget = storyboard_budget or runtime.storyboard_budget_factory()
    storyboard_save_failed = False

    def refresh_shots() -> list[Shot]:
        return list_shots(project_id)

    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        shots = refresh_shots()
        project = load_project(project_id)
        if project is None:
            notes.append("Project not found")
            break

        if storyboard_save_failed and name in IMAGE_TOOLS:
            error = (
                "Storyboard save failed. Layout and prompt work is blocked until "
                "save_storyboard succeeds."
            )
            notes.append(f"{name} blocked: {error}")
            if result_payloads is not None:
                result_payloads.append({"ok": False, "blocked": True, "error": error})
            continue

        if name in STORYBOARD_TOOLS:
            blocked = budget.admit()
            if blocked is not None:
                storyboard_save_failed = True
                notes.append(f"save_storyboard failed: {blocked['error']}")
                if result_payloads is not None:
                    result_payloads.append(blocked)
                continue

        await runtime.emit(on_progress, "tool", f"Executing tool: {name} {json.dumps(args, ensure_ascii=False)[:200]}")
        await runtime.emit(on_progress, "status", f"Executing: {name}…")

        try:
            if await handle_library_tool(
                name=name,
                args=args,
                project_id=project_id,
                actions=actions,
                notes=notes,
                result_payloads=result_payloads,
                user_uploads=user_uploads,
            ):
                continue
            if await handle_actor_tool(
                name=name,
                args=args,
                project_id=project_id,
                user_feedback=user_feedback,
                runtime=runtime,
                actions=actions,
                notes=notes,
                result_payloads=result_payloads,
                images=images,
            ):
                continue
            handled_project_tool = await handle_project_tool(
                name=name,
                args=args,
                project_id=project_id,
                project=project,
                svc=svc,
                actions=actions,
                notes=notes,
                result_payloads=result_payloads,
                user_feedback=user_feedback,
                requested_minimum_duration_s=requested_minimum_duration_s,
                refresh_shots=refresh_shots,
                storyboard_snapshot=runtime.storyboard_snapshot,
                script_locked_message=_SCRIPT_LOCKED_MESSAGE,
            )
            if handled_project_tool:
                if name in STORYBOARD_TOOLS and "save_storyboard" in actions:
                    storyboard_save_failed = False
                continue
            if await handle_layout_tool(
                name=name,
                args=args,
                project_id=project_id,
                shots=shots,
                svc=svc,
                runtime=runtime,
                actions=actions,
                notes=notes,
                touched=touched,
                prompt_written_shot_ids=prompt_written_shot_ids,
                result_payloads=result_payloads,
                images=images,
                user_feedback=user_feedback,
                on_progress=on_progress,
                refresh_shots=refresh_shots,
            ):
                continue
            if await handle_media_tool(
                name=name,
                args=args,
                project_id=project_id,
                project=project,
                shots=shots,
                runtime=runtime,
                actions=actions,
                notes=notes,
                touched=touched,
                result_payloads=result_payloads,
                images=images,
            ):
                continue
            if await handle_casting_tool(
                name=name,
                args=args,
                project_id=project_id,
                project_script=project.script_text or "",
                shots=shots,
                actions=actions,
                notes=notes,
                touched=touched,
            ):
                continue
            notes.append(f"Unknown tool: {name}")
        except Exception as e:
            logger.exception("tool %s failed", name)
            notes.append(f"{name} failed: {e}")
            failure: dict[str, Any] = {"ok": False, "error": str(e)}
            if name == "queue_gpt_ref_frame":
                failure.update(
                    {
                        "provider": "gpt",
                        "stage": str(getattr(e, "stage", "generation")),
                    }
                )
            issues = getattr(e, "issues", None)
            if isinstance(issues, list):
                failure["issues"] = [str(issue) for issue in issues]
            if name in STORYBOARD_TOOLS and budget.exhausted:
                failure.update(budget.blocked_result(error=str(e)))
                notes[-1] = f"{name} failed: {failure['error']}"
            if name in STORYBOARD_TOOLS:
                storyboard_save_failed = True
            if result_payloads is not None:
                result_payloads.append(failure)

    return notes, touched
