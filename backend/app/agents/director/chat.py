"""Compatibility facade for the modular Director chat implementation."""

from __future__ import annotations

from typing import Any

from ...core.jobs import create_job, load_job, start_pipeline_job
from ...core.jobs.runner import await_pipeline_job
from ...pipelines.registry import get_pipeline
from .chat_context import (
    gpt_generation_context_blob as _gpt_generation_context_blob,
    project_context_blob as _project_context_blob,
)
from .intent import (
    actor_acceptance_intent as _actor_acceptance_intent,
    actor_design_intent as _actor_design_intent,
    detect_intent,
    explicit_gpt_image_intent as _explicit_gpt_image_intent,
    is_script_query as _is_script_query,
    looks_like_script as _looks_like_script,
    normalize_text as _norm,
    resolve_shot as _resolve_shot,
    shot_ref as _shot_ref,
    validate_gpt_generation_prompt as _validate_gpt_generation_prompt,
)
from .service import DirectorService
from .tool_schema import (
    ACTOR_ACCEPT_TOOL,
    ACTOR_DESIGN_TOOL,
    DIRECTOR_TOOL_SCHEMAS,
    GPT_REF_FRAME_TOOL,
    IMAGE_TOOLS as _IMAGE_TOOLS,
    PLAN_TOOLS as _PLAN_TOOLS,
    SCRIPT_TOOLS as _SCRIPT_TOOLS,
    STORYBOARD_TOOLS as _STORYBOARD_TOOLS,
    director_chat_guides as _director_chat_guides,
    director_tool_schemas as _director_tool_schemas,
    offered_tool_names as _offered_tool_names,
)
from .chat_orchestrator import (
    DIRECTOR_CHAT_SYSTEM,
    ChatFn,
    ChatImage,
    ChatResult,
    GptToolError,
    ProgressFn,
    _StoryboardSubmissionBudget,
    _approve_layout_with_prompt,
    _execute_intent,
    _explicit_layout_queue_note,
    _extracted_tail_frame_image,
    _filter_tools_to_offered_schemas,
    _layout_images,
    _mark_layout_review,
    _native_reply,
    _parse_tools_from_llm,
    _prompt_nonempty,
    _requested_minimum_duration_s,
    _status_summary,
    _storyboard_snapshot,
    _tool_name,
    _emit,
    orchestrate_chat,
    sanitize_tools_for_pipeline,
    split_thinking,
)


async def _run_tools(
    *,
    project_id: str,
    tools: list[dict[str, Any]],
    svc: DirectorService,
    actions: list[str],
    on_progress: ProgressFn | None = None,
    result_payloads: list[dict[str, Any]] | None = None,
    user_feedback: str = "",
    requested_minimum_duration_s: float = 0.0,
    storyboard_budget: _StoryboardSubmissionBudget | None = None,
    images: list[ChatImage] | None = None,
    user_uploads: list[dict[str, Any]] | None = None,
) -> tuple[list[str], set[str]]:
    """Inject the facade's patchable infrastructure into the tool executor."""
    from .tool_execution import ToolExecutionRuntime, execute_tools

    runtime = ToolExecutionRuntime(
        create_job=create_job,
        load_job=load_job,
        start_pipeline_job=start_pipeline_job,
        await_pipeline_job=await_pipeline_job,
        get_pipeline=get_pipeline,
        emit=_emit,
        mark_layout_review=_mark_layout_review,
        approve_layout_with_prompt=_approve_layout_with_prompt,
        explicit_layout_queue_note=_explicit_layout_queue_note,
        extracted_tail_frame_image=_extracted_tail_frame_image,
        status_summary=_status_summary,
        storyboard_snapshot=_storyboard_snapshot,
        storyboard_budget_factory=_StoryboardSubmissionBudget,
        gpt_tool_error=GptToolError,
        chat_image_factory=ChatImage,
    )
    return await execute_tools(
        runtime=runtime,
        project_id=project_id,
        tools=tools,
        svc=svc,
        actions=actions,
        on_progress=on_progress,
        result_payloads=result_payloads,
        user_feedback=user_feedback,
        requested_minimum_duration_s=requested_minimum_duration_s,
        storyboard_budget=storyboard_budget,
        images=images,
        user_uploads=user_uploads,
    )


async def handle_chat(
    *,
    project_id: str,
    message: str,
    svc: DirectorService,
    chat_fn: ChatFn | None = None,
    history: list[dict[str, str]] | None = None,
    on_progress: ProgressFn | None = None,
    user_images_b64: list[str] | None = None,
    user_image_captions: list[str] | None = None,
) -> ChatResult:
    """Preserve the public chat entry point while delegating orchestration."""
    return await orchestrate_chat(
        project_id=project_id,
        message=message,
        svc=svc,
        chat_fn=chat_fn,
        history=history,
        on_progress=on_progress,
        user_images_b64=user_images_b64,
        user_image_captions=user_image_captions,
        run_tools=_run_tools,
    )
