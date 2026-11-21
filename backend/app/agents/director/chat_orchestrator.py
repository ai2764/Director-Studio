"""Chat-driven Director: LLM agent + tools (default), optional exact chips.

The model sees PROJECT_STATE and decides whether to answer or emit tools
(set_script / save_storyboard / plan_shots / queue_ref_frame / …). Exact one-token chips
(「拆镜」「状态」「生成全部参考帧」) remain as optional shortcuts; natural
sentences always go through the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ...config import settings
from ...core.h3.errors import PromptFailureKind
from ...core.jobs import create_job, load_job, start_pipeline_job
from ...core.jobs.runner import await_pipeline_job
from ...core.projects.layouts import (
    GptLayoutBrief,
    LayoutBrief,
    LayoutReviewStatus,
    LayoutSourceRef,
    mirror_legacy_layout_fields,
    sync_selected_layout_refs,
)
from ...core.schemas import JobStatus
from ...core.projects.models import (
    AssetCoverageReview,
    AssetCoverageReviewSubmission,
    Project,
    RefRole,
    Shot,
    ShotStatus,
)
from ...core.projects.store import list_shots, load_project, load_shot, save_project, save_shot
from ...core.projects.transitions import (
    apply_transition,
    review_layout_reference,
    select_layout_reference,
)
from ...core.vram import GenerationActiveError
from ...core.library.store import load_asset
from ...pipelines.registry import get_pipeline
from .planner import ShotRefsPatchSubmission, StoryboardSubmission
from .service import DirectorService
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
from .chat_context import (
    gpt_generation_context_blob as _gpt_generation_context_blob,
    project_context_blob as _project_context_blob,
)

logger = logging.getLogger("director_studio.director.chat")

_SCRIPT_LOCKED_MESSAGE = (
    "The screenplay is locked and cannot be changed during storyboard work."
)
_MAX_STORYBOARD_SUBMISSIONS = 3

# chat_fn(system, user, images=optional base64 list for multimodal)
ChatFn = Callable[..., Awaitable[str | dict[str, Any]]]
# progress event: {"type": "status"|"runtime"|"think"|"token"|"tool", "text": "..."}
ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]

# Model chain-of-thought wrappers (Qwen / DeepSeek style)
_THINK_BLOCK_RE = re.compile(
    r"<think>([\s\S]*?)</(?:think|redacted_reasoning)>|"
    r"<thinking>([\s\S]*?)</thinking>|"
    r"<reasoning>([\s\S]*?)</reasoning>",
    re.IGNORECASE,
)


def split_thinking(text: str) -> tuple[str, str]:
    """Return (thinking, visible_reply) from model output."""
    raw = text or ""
    thoughts: list[str] = []

    def _collect(m: re.Match[str]) -> str:
        body = next((g for g in m.groups() if g), "") or ""
        if body.strip():
            thoughts.append(body.strip())
        return ""

    visible = _THINK_BLOCK_RE.sub(_collect, raw).strip()
    # Unclosed <think> (still streaming / truncated)
    open_m = re.search(r"<think(?:ing)?>([\s\S]*)$", visible, flags=re.I)
    if open_m and not re.search(r"</think", visible, flags=re.I):
        thoughts.append(open_m.group(1).strip())
        visible = visible[: open_m.start()].strip()
    thinking = "\n\n".join(thoughts).strip()
    return thinking, visible


async def _emit(on_progress: ProgressFn | None, type_: str, text: str) -> None:
    if not on_progress or not text:
        return
    try:
        await on_progress({"type": type_, "text": text})
    except Exception:
        logger.exception("progress callback failed")


def _mark_layout_review(asset_id: str, status: str) -> None:
    """Best-effort write review_status on layouts library asset."""
    asset = load_asset("layouts", asset_id)
    if asset is None:
        return
    meta = dict(asset.meta or {})
    meta["review_status"] = status
    asset = asset.model_copy(update={"meta": meta})
    from ...core.library.store import asset_dir

    adir = asset_dir("layouts", asset_id)
    path = adir / "asset.json"
    if path.exists():
        payload = asset.model_dump(mode="json")
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class ChatImage:
    url: str
    caption: str = ""
    shot_id: str | None = None


@dataclass
class ChatResult:
    reply: str
    failure_code: str = ""
    failure_message: str = ""
    failure_kind: PromptFailureKind = "unknown"
    actions: list[str] = field(default_factory=list)
    project: Project | None = None
    shots: list[Shot] = field(default_factory=list)
    images: list[ChatImage] = field(default_factory=list)
    thinking: str = ""
    steps: list[str] = field(default_factory=list)


class GptToolError(ValueError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(message)


@dataclass
class _StoryboardSubmissionBudget:
    """One handle_chat-wide admission gate for storyboard save submissions."""

    limit: int = _MAX_STORYBOARD_SUBMISSIONS
    submissions: int = 0
    repairing: bool = False

    @property
    def exhausted(self) -> bool:
        return self.submissions >= self.limit

    def admit(self) -> dict[str, Any] | None:
        if self.exhausted:
            return self.blocked_result()
        self.submissions += 1
        return None

    def blocked_result(self, *, error: str = "") -> dict[str, Any]:
        blocked_error = (
            f"Automatic storyboard revision budget reached after {self.limit} "
            "submissions in this chat turn. Further storyboard saves are blocked for "
            "the remainder of this chat turn. The rejected candidate was not persisted. "
            "Explain the unresolved issues and invite further discussion; a new user "
            "turn starts with a fresh automatic revision budget."
        )
        return {
            "ok": False,
            "blocked": True,
            "save_storyboard_submissions": self.submissions,
            "error": (
                f"{error.strip()} {blocked_error}".strip()
                if error.strip()
                else blocked_error
            ),
        }


def _explicit_storyboard_save_request(message: str) -> bool:
    normalized = (message or "").lower()
    return "save_storyboard" in normalized or (
        "storyboard" in normalized
        and any(word in normalized for word in ("save", "persist", "replace"))
    )


def _claims_storyboard_resubmission(content: str) -> bool:
    normalized = (content or "").strip().lower()
    if not normalized:
        return True
    return bool(
        re.search(
            r"\b(?:re-?submit(?:ting)?|retry(?:ing)?|submit(?:ting)? again|trying again)\b",
            normalized,
        )
    )


def _claims_completed_storyboard(content: str) -> bool:
    """Detect completion claims that require a successful persisted save."""
    normalized = (content or "").strip().lower()
    if not normalized:
        return False
    return bool(
        re.search(
            r"(?:\bstoryboard\b.{0,40}\b(?:saved|ready|complete(?:d)?)\b"
            r"|\b(?:planned|created|saved)\s+\d+\s+shots?\b"
            r"|(?:已规划|已保存|已创建).{0,12}\d+.{0,4}镜)",
            normalized,
        )
    )


_STORYBOARD_CONTINUATION_PROMPT = (
    "You have not submitted the storyboard yet. Call save_storyboard now with "
    "the complete corrected payload, or explain the unresolved issue without "
    "claiming that you are submitting or retrying. Do not narrate a future tool call."
)


def _status_summary(project: Project, shots: list[Shot]) -> str:
    if not shots:
        script_len = len(project.script_text or "")
        return (
            f"Project **{project.name}** has no shots yet. The script is about "
            f"{script_len} characters long. "
            f"{'Ask me to plan shots from the script.' if script_len else 'Send me the story or script first.'}"
        )
    lines = [f"Project **{project.name}** · {len(shots)} shot{'s' if len(shots) != 1 else ''}:"]
    for i, s in enumerate(shots, 1):
        layout = s.layout_review_status or "—"
        ref_bits = []
        for r in s.refs or []:
            role = r.role.value if hasattr(r.role, "value") else str(r.role)
            ref_bits.append(f"{role}:{r.asset_id[-8:]}")
        ref_s = ", ".join(ref_bits) if ref_bits else "no refs"
        lines.append(
            f"{i}. **{s.title}** — `{s.status}` · layout:{layout} · "
            f"{ref_s} · {s.duration_s}s"
        )
        if s.blocked_reasons:
            lines.append(f"   ⚠ {'; '.join(s.blocked_reasons)}")
        from ...core.projects.video_context import video_context_status
        context_status = video_context_status(s)
        if context_status["mode"] != "off":
            lines.append(
                f"   video context: {context_status['mode']} · "
                f"source {context_status['source_job_id'] or 'unresolved'} · "
                f"{context_status['context_frames']} frames"
            )
            if context_status["blocked_reasons"]:
                lines.append(f"   ⚠ {'; '.join(context_status['blocked_reasons'])}")
    from .brief import duration_budget, duration_issues
    budget = duration_budget(project, shots)
    lines.append(f"Runtime: {budget['total_s']:g}s; requested minimum: {budget['required_minimum_s']:g}s; remaining deficit: {budget['deficit_s']:g}s.")
    lines.extend(duration_issues(project, shots))
    return "\n".join(lines)


def _layout_images(shots: list[Shot], *, only_shot_ids: set[str] | None = None) -> list[ChatImage]:
    out: list[ChatImage] = []
    for i, s in enumerate(shots, 1):
        if only_shot_ids is not None and s.id not in only_shot_ids:
            continue
        if not s.layout_asset_id:
            continue
        out.append(
            ChatImage(
                url=f"/api/files/library/layouts/{s.layout_asset_id}/layout.png",
                caption=f"{i}. {s.title}",
                shot_id=s.id,
            )
        )
    return out


def _extracted_tail_frame_image(
    extracted: dict[str, Any],
    *,
    source_title: str,
    target_title: str,
) -> ChatImage | None:
    image_url = str(extracted.get("image_url") or "").strip()
    if not image_url:
        return None
    return ChatImage(
        url=image_url,
        caption=(
            f"Tail frame · {source_title} · v{extracted.get('source_version')} · "
            f"{extracted.get('output_kind')} → {target_title}"
        ),
        shot_id=str(extracted.get("target_shot_id") or "") or None,
    )


DIRECTOR_CHAT_SYSTEM = """You are the Director Agent in Director Studio, a collaborative director helping the user create an AI short film.

PROJECT_STATE provides the current project metadata, script, shot list/summaries, Asset Library inventory, and workflow state. Use the native tools provided when state must change; never print tool JSON in prose or claim a job or edit succeeded before a tool returns ok.

Communication & evidence:
- Reply in English, naturally and concisely like a production colleague. Answer questions without mutating state.
- Copy asset IDs, layout IDs, and job IDs only from PROJECT_STATE or current-turn tool results; never invent them or treat prior chat prose as execution proof.
- Library inventory is metadata, not visual proof. Call inspect_asset when appearance is unknown or labels conflict; describe only what is actually visible in attached images.
- Ask a focused question on genuine requirement conflicts (including retaining existing assets and adapting the story) instead of silently substituting or replacing work.
- When the user uploads images, call classify_chat_image once per uploaded image before unrelated mutations.

Tool routing (follow each tool's native schema for parameters and constraints):
- Screenplay: a premise or one-line brief is a model-authored draft; do not call set_script until the user explicitly asks to save, use, or adopt it. review_asset_coverage is advisory and never blocks storyboarding.
- Storyboard edits: use revise_shot for authored-field changes to exactly one shot (call revise_shot then write_prompt when a prompt rewrite is also requested). Use append_shot or append_shots to add new shots at the end without modifying existing shots. Reserve save_storyboard for initial authoring or coordinated multi-shot revisions (replacing an existing board requires explicit user confirmation via confirm_storyboard_replacement). Proposed storyboards may be discussed without persistence; only a validated save_storyboard result persists (up to three automatic submissions per turn; a new user turn resets the budget for further discussion).
- Reference-only edits: use patch_shot_refs to update Picture bindings without changing story fields. When the user specifies an exact scene asset and file_key, use set_shot_scene_ref; the human-specified file_key is authoritative, so do not reinterpret angle tokens in filenames.
- Layouts: default to local queue_ref_frame (Qwen Image 2.1); use queue_gpt_ref_frame only when the user explicitly requests GPT/ChatGPT, and never silently switch providers on failure. Use revise_ref_frame for critiques, accept_ref_frame for explicit approvals, and extract_clip_tail_frame to pull a prior clip's tail frame.
- Prompts & video: call write_prompt to review changed materials and write the six-section H3 prompt (Layouts are optional). Video generation stays in Production except for an active managed run or an explicit one-shot start_h3_video request."""


def _filter_tools_to_offered_schemas(
    tools: list[dict[str, Any]],
    *,
    tool_schemas: Iterable[dict[str, Any]],
    storyboard_budget: _StoryboardSubmissionBudget,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Partition model calls before pipeline sanitization or execution."""
    offered = _offered_tool_names(tool_schemas)
    accepted: list[dict[str, Any]] = []
    rejected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for tool in tools:
        name = _tool_name(tool)
        if name in offered:
            accepted.append(tool)
            continue
        display_name = name or "(missing name)"
        rejected.append(
            (
                tool,
                {
                    "ok": False,
                    "tool_name": name,
                    "error": f"Unknown or unavailable tool: {display_name}.",
                    "offered_tools": sorted(offered),
                    "save_storyboard_submissions": storyboard_budget.submissions,
                },
            )
        )
    return accepted, rejected


def _requested_minimum_duration_s(message: str) -> float:
    from .brief import requested_minimum_duration_s
    return requested_minimum_duration_s(message)


def _native_reply(value: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    content = str(value.get("content") or "").strip()
    thinking = str(value.get("thinking") or "").strip()
    tools: list[dict[str, Any]] = []
    for call in list(value.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        arguments = call.get("arguments")
        if name:
            tools.append(
                {
                    "name": name,
                    "args": arguments if isinstance(arguments, dict) else {},
                }
            )
    return content, thinking, tools


def _tool_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or "").strip()


def sanitize_tools_for_pipeline(
    tools: list[dict[str, Any]],
    *,
    project: Project,
    shots: list[Shot],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Enforce script → plan → ref_frame order on LLM tool lists.

    Returns (sanitized_tools, user-visible notes about what was adjusted).
    """
    from .context_io import load_agent_context
    from .service import _script_hash

    notes: list[str] = []
    from ...core.managed_runs.context import managed_turn_scope
    scope = managed_turn_scope.get()
    if scope is not None and scope.project_id == project.id:
        managed_tools = {"get_status", "inspect_asset", "write_prompt", "start_h3_video",
                         "configure_video_context", "set_task_context", "read_task_context"}
        allowed_tools = []
        for item in tools:
            name = _tool_name(item) if isinstance(item, dict) else ""
            if name in managed_tools:
                allowed_tools.append(item)
            else:
                notes.append(f"Skipped {name or '(missing name)'}: unavailable during a managed H3 run.")
        return allowed_tools, notes

    if project.script_locked:
        unlocked_tools: list[dict[str, Any]] = []
        for item in tools:
            if isinstance(item, dict) and _tool_name(item) in _SCRIPT_TOOLS:
                notes.append(f"Skipped set_script: {_SCRIPT_LOCKED_MESSAGE}")
                continue
            unlocked_tools.append(item)
        tools = unlocked_tools

    if not tools:
        return [], notes

    # A changed script invalidates derived evidence, not the authored board.
    # Per-shot source/reference checks still gate publication. Never inject a
    # destructive re-plan as a side effect of a local operation on existing shots.
    if shots:
        return tools, notes

    names = [_tool_name(t) for t in tools if isinstance(t, dict)]
    # Read-only tools must never implicitly replace a board (including before
    # or after an append on a stale board).
    if names and set(names) <= {"get_status", "status", "inspect_asset", "set_task_context", "read_task_context", "configure_video_context"}:
        return tools, notes
    if names and set(names) <= {"start_h3_video", "get_status", "inspect_asset", "configure_video_context"}:
        return tools, notes
    if names and set(names) <= {"queue_actor_design", "confirm_actor_design", "accept_actor_design"}:
        return tools, notes
    has_set = any(n in _SCRIPT_TOOLS for n in names)
    has_plan = any(n in _PLAN_TOOLS or n in _STORYBOARD_TOOLS or n in {"append_shot", "append_shots"} for n in names)
    has_image = any(n in _IMAGE_TOOLS for n in names)

    script = (project.script_text or "").strip()
    script_hash = _script_hash(script)
    agent_ctx = load_agent_context(project.id)
    planned_hash = (agent_ctx.script_hash if agent_ctx else "") or ""
    shots_stale = bool(shots) and bool(script) and (
        not planned_hash or planned_hash != script_hash
    )
    # set_script this turn / empty or stale shots → must plan before imaging
    needs_plan = has_set or (bool(script) and (not shots or shots_stale))

    out: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        n = _tool_name(item)
        if not n:
            continue
        # Drop imaging tools when we still need a fresh plan
        if needs_plan and n in _IMAGE_TOOLS:
            notes.append(
                f"Skipped {n}: after a script change or stale shot plan, run plan_shots before generating images."
            )
            continue
        out.append(item)

    names2 = [_tool_name(t) for t in out]
    has_set = any(n in _SCRIPT_TOOLS for n in names2)
    has_plan = any(n in _PLAN_TOOLS or n in _STORYBOARD_TOOLS or n in {"append_shot", "append_shots"} for n in names2)

    # After set_script or stale/missing shots, ensure plan_shots runs this turn
    must_inject_plan = (has_set or shots_stale or (bool(script) and not shots)) and not has_plan
    if must_inject_plan:
        plan_item: dict[str, Any] = {"name": "plan_shots", "args": {}}
        if has_set:
            inserted: list[dict[str, Any]] = []
            injected = False
            for t in out:
                inserted.append(t)
                if _tool_name(t) in _SCRIPT_TOOLS and not injected:
                    inserted.append(plan_item)
                    injected = True
            if not injected:
                inserted.append(plan_item)
            out = inserted
        else:
            out = [plan_item] + out
        notes.append("Added plan_shots automatically so the current script is planned before composition work.")

    return out, notes


async def _approve_layout_with_prompt(
    shot: Shot,
    *,
    svc: DirectorService,
    write_prompt: bool = True,
    on_progress: ProgressFn | None = None,
) -> tuple[Shot, str]:
    """Approve Gate1 layout; by default also fill six-section H3 prompt via LLM."""
    s2 = apply_transition(shot, "approve_layout")
    if s2.layout_asset_id:
        _mark_layout_review(s2.layout_asset_id, "approved")
    save_shot(s2)
    note = f"Approved layout: **{s2.title}**"
    if not write_prompt:
        return s2, note + " (prompt not written)"
    await _emit(on_progress, "status", f"Approval complete · Writing the six-section H3 prompt for {s2.title}…")
    try:
        s2 = await svc.write_prompts_after_layout(s2.id, **({"on_progress": on_progress} if on_progress else {}))
        note += "; six-section prompt written"
        await _emit(on_progress, "status", f"Prompt complete: {s2.title}")
    except Exception as e:
        logger.exception("write_prompts after approve failed for %s", s2.id)
        note += f"; prompt failed ({e}) — ask me to write the prompt for that shot to retry"
        await _emit(on_progress, "status", f"Prompt failed: {e}")
    return s2, note


def _prompt_nonempty(shot: Shot) -> bool:
    ps = shot.prompt_sections
    if ps is None:
        return False
    for key in (
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "overall_soundscape",
        "non_diegetic_music",
    ):
        if str(getattr(ps, key, "") or "").strip():
            return True
    return False


def _parse_tools_from_llm(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Split natural reply and optional trailing tools JSON."""
    raw = (text or "").strip()
    if not raw:
        return "", []

    tools: list[dict[str, Any]] = []
    reply = raw

    # Fenced ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.I)
    if fence:
        try:
            obj = json.loads(fence.group(1))
            if isinstance(obj, dict) and "tools" in obj:
                tools = list(obj.get("tools") or [])
                reply = (raw[: fence.start()] + raw[fence.end() :]).strip()
                return reply, tools
            if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
                tools = [
                    {
                        "name": obj["tool"],
                        "args": obj.get("params") or obj.get("arguments") or {},
                    }
                ]
                reply = (raw[: fence.start()] + raw[fence.end() :]).strip()
                return reply, tools
        except json.JSONDecodeError:
            pass

    # Schema-constrained single-tool output is one bare JSON object.
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
            return "", [
                {
                    "name": obj["tool"],
                    "args": obj.get("params") or obj.get("arguments") or {},
                }
            ]
    except json.JSONDecodeError:
        pass

    # Trailing bare JSON object with tools
    m = re.search(r"(\{\s*\"tools\"\s*:\s*\[[\s\S]*\]\s*\})\s*$", raw)
    if m:
        try:
            obj = json.loads(m.group(1))
            tools = list(obj.get("tools") or [])
            reply = raw[: m.start()].strip()
            return reply, tools
        except json.JSONDecodeError:
            pass

    return reply, []


def _explicit_layout_queue_note(shot: Shot) -> str:
    if shot.blocked_reasons:
        return (
            f"Composition reference blocked for **{shot.title}**: "
            + "; ".join(shot.blocked_reasons)
        )
    if not shot.layout_refs:
        return f"Could not queue a Layout for **{shot.title}**"
    layout = shot.layout_refs[-1]
    source_count = len(layout.source_refs)
    return (
        f"Queued Layout {layout.id} for **{shot.title}**: "
        f"purpose {layout.purpose!r}, job {layout.job_id or '(none)'}, "
        f"{source_count} source{'s' if source_count != 1 else ''}"
    )


def _storyboard_snapshot(shots: list[Shot]) -> dict[str, Any]:
    return {
        "shots": [
            {
                "id": shot.id,
                "scene_id": shot.scene_id,
                "title": shot.title,
                "duration_s": shot.duration_s,
                "script_beat": shot.script_beat,
                "shot_type": shot.shot_type,
                "camera_angle": shot.camera_angle,
                "camera_motion": shot.camera_motion,
                "composition": shot.composition,
                "dialogue": list(shot.dialogue),
                "refs": [ref.model_dump(mode="json") for ref in shot.refs],
                "voice_refs": [
                    ref.model_dump(mode="json") for ref in shot.voice_refs
                ],
                "music_segment": (
                    shot.music_segment.model_dump(mode="json")
                    if shot.music_segment else None
                ),
            }
            for shot in shots
        ]
    }


async def _execute_intent(
    *,
    intent: str,
    params: dict[str, Any],
    project_id: str,
    message: str,
    svc: DirectorService,
    on_progress: ProgressFn | None = None,
) -> tuple[str, list[str], set[str]]:
    """Legacy/fast-path intent execution → (reply, actions, image shot ids)."""
    actions: list[str] = []
    touched: set[str] = set()
    project = load_project(project_id)
    assert project is not None
    shots = list_shots(project_id)

    def refresh() -> tuple[Project, list[Shot]]:
        p = load_project(project_id)
        assert p is not None
        return p, list_shots(project_id)

    if intent == "help":
        actions.append("help")
        await _emit(on_progress, "status", "Shortcut: help")
        return (
            "I am the Director for this project. Tell me about the story, how you want to shoot it, "
            "or which composition is not working. You can also use shortcuts such as status, plan, "
            "reference frame all, or ask me to write the H3 prompt for a shot.",
            actions,
            touched,
        )

    if intent == "status":
        actions.append("status")
        await _emit(on_progress, "status", "Shortcut: summarize project status")
        for s in shots:
            if s.layout_asset_id:
                touched.add(s.id)
        return _status_summary(project, shots), actions, touched

    if intent == "set_script":
        if project.script_locked:
            return _SCRIPT_LOCKED_MESSAGE, actions, touched
        script_text = params.get("script_text") or message
        await _emit(on_progress, "status", f"Saving script ({len(script_text)} characters)…")
        project = project.model_copy(update={"script_text": script_text})
        save_project(project)
        actions.append("set_script")
        # Script changed → next step is plan, not reference frame
        return (
            f"Saved the script ({len(script_text)} characters). "
            "Next, run **Plan shots** to break down the new story and cast assets before generating composition references.",
            actions,
            touched,
        )

    if intent == "plan":
        if not (project.script_text or "").strip():
            return "This project has no script yet. Send me the story or script first.", actions, touched
        actions.append("plan")
        await _emit(on_progress, "status", "Shortcut: plan shots · Queueing the GPU, unloading Comfy, and loading Ollama…")
        try:
            await _emit(on_progress, "status", "The Director planning model is breaking down shots and casting assets…")
            await svc.plan_project(project_id)
            project, shots = refresh()
            if not shots:
                await _emit(on_progress, "status", "Shot planning failed: 0 shots")
                return (
                    "Shot planning produced no shots. The model output could not be parsed and no Asset Library fallback was available. Please retry.",
                    actions,
                    touched,
                )
            await _emit(on_progress, "status", f"Shot planning complete: {len(shots)} shot{'s' if len(shots) != 1 else ''}")
            return "Shot planning is complete.\n\n" + _status_summary(project, shots), actions, touched
        except Exception as e:
            logger.exception("chat plan failed")
            return f"Shot planning failed: {e}", actions, touched

    if intent == "ref_frame_all":
        actions.append("ref_frame_all")
        msg_l = (message or "").lower()
        force_all = any(
            k in (message or "") for k in ("重做", "再出", "重新", "redo", "regen")
        ) or "force" in msg_l
        # Nothing left in "needs layout" → user is asking again: auto redo.
        if not force_all and shots:
            needs_layout = any(
                s.status
                in (
                    ShotStatus.draft,
                    ShotStatus.planning,
                    ShotStatus.ref_frame_pending,
                    ShotStatus.blocked,
                    ShotStatus.failed,
                )
                for s in shots
            )
            if not needs_layout:
                force_all = True
        await _emit(
            on_progress,
            "status",
            "Casting missing assets and queueing composition references…"
            + (" (forced regeneration)" if force_all else ""),
        )
        try:
            updated = await svc.queue_ref_frames(
                project_id, shot_ids=None, force=force_all
            )
            # Safety net: empty without force → retry once with force
            if not updated and not force_all and shots:
                force_all = True
                await _emit(on_progress, "status", "No shots were available, so reference regeneration is being forced…")
                updated = await svc.queue_ref_frames(
                    project_id, shot_ids=None, force=True
                )
            project, shots = refresh()
            for s in updated:
                touched.add(s.id)
            n = len(
                [
                    s
                    for s in updated
                    if s.ref_frame_job_id and s.status != ShotStatus.blocked
                ]
            )
            blocked = [s for s in shots if s.status == ShotStatus.blocked]
            if n == 0 and not blocked:
                # Last resort: explicit per-shot force for single-shot projects
                if len(shots) == 1:
                    await _emit(on_progress, "status", "Forcing reference regeneration for the only shot…")
                    updated = await svc.queue_ref_frames(
                        project_id, shot_ids=[shots[0].id], force=True
                    )
                    project, shots = refresh()
                    for s in updated:
                        touched.add(s.id)
                    n = len(
                        [
                            s
                            for s in updated
                            if s.ref_frame_job_id and s.status != ShotStatus.blocked
                        ]
                    )
            if n == 0 and not blocked:
                reply = (
                    "No composition-reference job could be queued. Every shot may already have a queued or running video job. "
                    "Wait for H3 to finish, then request reference regeneration for a specific shot."
                )
            else:
                reply = f"Queued {n} composition-reference job{'s' if n != 1 else ''}" + (" for regeneration" if force_all else "") + "."
            if blocked:
                reply += "\nBlocked shots:\n" + "\n".join(
                    f"- {s.title}: {'; '.join(s.blocked_reasons)}" for s in blocked
                )
            reply += "\n\n" + _status_summary(project, shots)
            await _emit(on_progress, "status", f"Composition-reference queue complete ({n} updated)")
            return reply, actions, touched
        except Exception as e:
            logger.exception("chat ref_frame_all failed")
            return f"Could not queue composition references: {e}", actions, touched

    if intent == "ref_frame":
        sid = params.get("shot_id")
        if not sid:
            if len(shots) == 1:
                sid = shots[0].id
            else:
                pending = [
                    s
                    for s in shots
                    if s.status
                    in (
                        ShotStatus.draft,
                        ShotStatus.planning,
                        ShotStatus.ref_frame_pending,
                        ShotStatus.needs_review,
                        ShotStatus.failed,
                        ShotStatus.blocked,
                        ShotStatus.needs_review,
                    )
                ]
                if len(pending) == 1:
                    sid = pending[0].id
        if not sid:
            return "Which shot needs a composition reference? Give me its number or title; an existing reference can also be regenerated.", actions, touched
        actions.append(f"ref_frame:{sid}")
        await _emit(on_progress, "status", f"Casting missing assets and queueing the reference for shot {sid[-8:]}…")
        try:
            prev = next((x for x in shots if x.id == sid), None)
            prev_job = prev.ref_frame_job_id if prev else None
            updated = await svc.queue_ref_frames(project_id, shot_ids=[sid], force=True)
            project, shots = refresh()
            s = next((x for x in shots if x.id == sid), None)
            touched.add(sid)
            if not updated:
                reply = f"Could not queue a composition reference for {s.title if s else sid} (status {s.status.value if s else '?'})."
            elif s and s.blocked_reasons:
                reply = f"Composition reference blocked for {s.title}: " + "; ".join(s.blocked_reasons)
            else:
                redo = bool(prev_job and s and s.ref_frame_job_id != prev_job)
                reply = (
                    f"{'Requeued' if redo else 'Queued'} the composition reference for "
                    f"{s.title if s else sid}.\n"
                    "When it finishes, inspect the image and decide whether it is usable."
                )
            return reply, actions, touched
        except Exception as e:
            logger.exception("chat ref_frame failed")
            return f"Composition-reference generation failed: {e}", actions, touched

    if intent == "approve_all":
        actions.append("approve_all")
        n = 0
        notes_local: list[str] = []
        for s in list(shots):
            if s.status == ShotStatus.needs_review or s.layout_review_status == "pending_review":
                try:
                    s2, note = await _approve_layout_with_prompt(
                        s, svc=svc, write_prompt=True, on_progress=on_progress
                    )
                    n += 1
                    touched.add(s2.id)
                    notes_local.append(note)
                except ValueError:
                    continue
        project, shots = refresh()
        body = "\n".join(f"· {x}" for x in notes_local) if notes_local else f"Approved {n} layout{'s' if n != 1 else ''}."
        return body + "\n\n" + _status_summary(project, shots), actions, touched

    if intent == "approve":
        sid = params.get("shot_id")
        if not sid and len(shots) == 1:
            sid = shots[0].id
        if not sid:
            reviewable = [
                s
                for s in shots
                if s.status == ShotStatus.needs_review
                or s.layout_review_status == "pending_review"
            ]
            if len(reviewable) == 1:
                sid = reviewable[0].id
        if not sid:
            return "Which shot should be approved? Give me its number or title.", actions, touched
        actions.append(f"approve:{sid}")
        s = load_shot(project_id, sid)
        if not s:
            return f"Shot not found: {sid}", actions, touched
        try:
            s2, note = await _approve_layout_with_prompt(
                s, svc=svc, write_prompt=True, on_progress=on_progress
            )
            touched.add(s2.id)
            extra = ""
            if _prompt_nonempty(s2):
                extra = " You can generate the video in Production or continue with the next shot."
            return note + "." + extra, actions, touched
        except ValueError as e:
            return f"Could not approve the shot: {e}", actions, touched

    if intent == "write_prompt":
        sid = params.get("shot_id")
        if not sid and len(shots) == 1:
            sid = shots[0].id
        if not sid:
            return "Which shot needs an H3 prompt? Give me its number or title.", actions, touched
        actions.append(f"write_prompt:{sid}")
        s = load_shot(project_id, sid)
        if not s:
            return f"Shot not found: {sid}", actions, touched
        await _emit(on_progress, "status", f"Writing the six-section H3 prompt for {s.title}…")
        try:
            s2 = await svc.write_prompts_after_layout(s.id, revision_request=message,
                                                     **({"on_progress": on_progress} if on_progress else {}))
            touched.add(s2.id)
            return f"The six-section H3 prompt for **{s2.title}** is ready. You can generate the video in Production.", actions, touched
        except Exception as e:
            logger.exception("intent write_prompt failed")
            return f"Prompt writing failed: {e}", actions, touched

    if intent == "reject":
        sid = params.get("shot_id")
        feedback = params.get("feedback") or ""
        if not sid and len(shots) == 1:
            sid = shots[0].id
        if not sid:
            return "Which shot should be rejected? Give me its number and the reason.", actions, touched
        actions.append(f"reject:{sid}")
        s = load_shot(project_id, sid)
        if not s:
            return f"Shot not found: {sid}", actions, touched
        try:
            s2 = apply_transition(
                s,
                "reject_layout",
                feedback=feedback,
                status=ShotStatus.ref_frame_pending,
            )
            save_shot(s2)
            touched.add(s2.id)
            return (
                f"Rejected **{s2.title}**."
                + (f" Feedback: {feedback}" if feedback else "")
                + " I can regenerate the reference frame from that feedback if needed.",
                actions,
                touched,
            )
        except ValueError as e:
            return f"Could not reject the shot: {e}", actions, touched

    return "", actions, touched


from .task_context_runtime import (scoped_task_turn, current_task_context, present_task_tools,
    render_task_context, task_authority, available_packet_chars, context_mode)


@scoped_task_turn
async def orchestrate_chat(
    *,
    project_id: str,
    message: str,
    svc: DirectorService,
    chat_fn: ChatFn | None = None,
    history: list[dict[str, str]] | None = None,
    on_progress: ProgressFn | None = None,
    user_images_b64: list[str] | None = None,
    user_image_captions: list[str] | None = None,
    run_tools: Callable[..., Awaitable[tuple[list[str], set[str]]]],
) -> ChatResult:
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")

    shots = list_shots(project_id)
    intent, params = detect_intent(
        message,
        shots,
        script_locked=project.script_locked,
    )
    actions: list[str] = []
    reply = ""
    image_ids: set[str] = set()
    attached_images: list[ChatImage] = []
    thinking_parts: list[str] = []
    steps: list[str] = []
    storyboard_save_attempted = False
    storyboard_save_blocked = False
    prompt_failure_message = ""
    prompt_failure_code = "PROMPT_GENERATION_FAILED"
    prompt_failure_kind = "unknown"
    from .configuration_recovery import ConfigurationRecovery
    configuration_recovery = ConfigurationRecovery()

    async def progress(type_: str, text: str) -> None:
        """Local helper — also forwards to external on_progress as event dict."""
        if type_ == "status" and text:
            steps.append(text)
        if type_ == "think" and text:
            thinking_parts.append(text)
        await _emit(on_progress, type_, text)

    async def progress_event(ev: dict[str, Any]) -> None:
        await progress(str(ev.get("type") or "status"), str(ev.get("text") or ""))

    def finish(r: str, acts: list[str], ids: set[str], *, thinking: str = "") -> ChatResult:
        p = load_project(project_id)
        assert p is not None
        sh = list_shots(project_id)
        if "save_storyboard" in acts:
            shot_count = len(sh)
            r = f"Storyboard saved: {shot_count} shot{'s' if shot_count != 1 else ''}."
        elif "append_shot" in acts or any(a.startswith("append_shots:") for a in acts):
            count = acts.count("append_shot") + sum(int(a.split(":")[1]) for a in acts if a.startswith("append_shots:"))
            r = f"Appended {count} new shot{'s' if count != 1 else ''} at the end."
        elif (
            storyboard_save_attempted
            or storyboard_save_blocked
            or _claims_completed_storyboard(r)
        ):
            authoritative = (
                "Storyboard was not saved; existing project shots remain unchanged."
            )
            r = (
                authoritative
                if _claims_completed_storyboard(r) or not r.strip()
                else f"{authoritative}\n\n{r.strip()}"
            )
        extras = list(attached_images)
        extra_shot_ids = {img.shot_id for img in extras if img.shot_id}
        extra_urls = {img.url for img in extras}
        target_shot = _shot_ref(message, sh)
        target_ids = set(ids)
        if not target_ids and target_shot is not None:
            target_ids.add(target_shot.id)
        # Attach only layouts relevant to the explicit/touched Shot. Project-wide
        # status and bulk actions may still return the complete Layout set.
        imgs = _layout_images(sh, only_shot_ids=target_ids) if target_ids else []
        if not ids and any(a.startswith(("approve", "reject", "status", "ref_frame")) for a in acts):
            imgs = _layout_images(sh, only_shot_ids=target_ids or None)
        # Always show existing layouts on status
        if "status" in acts or intent == "status":
            imgs = _layout_images(sh, only_shot_ids=target_ids or None)
        if extras:
            imgs = [
                img
                for img in imgs
                if img.url not in extra_urls and img.shot_id not in extra_shot_ids
            ]
            imgs = extras + imgs
        all_think = "\n".join(thinking_parts)
        if thinking:
            all_think = (all_think + "\n" + thinking).strip() if all_think else thinking
        return ChatResult(
            reply=r,
            failure_code=prompt_failure_code if prompt_failure_message else "",
            failure_message=prompt_failure_message,
            failure_kind=prompt_failure_kind,
            actions=acts,
            project=p,
            shots=sh,
            images=imgs,
            thinking=all_think.strip(),
            steps=list(steps),
        )

    # --- Fast path: short commands / pasted script ---
    if intent != "llm":
        reply, actions, image_ids = await _execute_intent(
            intent=intent,
            params=params,
            project_id=project_id,
            message=message,
            svc=svc,
            on_progress=progress_event,
        )
        return finish(reply, actions, image_ids)

    # --- Natural language via LLM + tools ---
    if _explicit_gpt_image_intent(message) and not settings.gpt_bridge_configured:
        return finish(
            "GPT image generation is unavailable on this machine. Configure "
            "DS_GPT_BRIDGE_BASE_URL and DS_GPT_BRIDGE_ENV_FILE, ensure the Bridge "
            "and a connected ChatGPT tab are running, then ask me to use GPT again. "
            "I did not start a Comfy job.",
            [],
            image_ids,
        )
    actions.append("llm")
    if chat_fn is None:
        # Offline fallback: soft status + hint
        return finish(
            _status_summary(project, shots)
            + "\n\nNo inference model is connected. You can still use shortcuts such as status, plan, and reference frame all.",
            actions,
            image_ids,
        )

    hist_lines: list[str] = []
    for h in (history or [])[-4:]:
        role = h.get("role") or "user"
        content = (h.get("content") or "").strip()
        if content:
            hist_lines.append(f"{role.upper()}: {content[:400]}")

    # User uploads are authoritative for this turn. Automatic project-image
    # collection remains available only when no explicit upload was supplied.
    vision_b64 = list(user_images_b64 or [])
    captions = list(user_image_captions or [])
    vision_note = ""
    if vision_b64:
        caption_lines = [
            f"User-uploaded image {index}: "
            f"{captions[index - 1] if index <= len(captions) else 'attachment'}"
            for index in range(1, len(vision_b64) + 1)
        ]
        vision_note = (
            "The user explicitly attached these images for this turn. Inspect their "
            "visible contents before answering; do not claim you cannot see them. "
            "Classify every image with classify_chat_image using both the image and "
            "the user's message. Generate a concise name in the user's language and "
            "factual Library notes; use chat_only when genuinely uncertain:\n"
            + "\n".join(caption_lines)
        )
        actions.append(f"vision:{len(vision_b64)}")
        await progress(
            "status",
            f"Attached {len(vision_b64)} user image"
            f"{'s' if len(vision_b64) != 1 else ''} for the multimodal model",
        )
    else:
        try:
            from .vision import (
                collect_vision_attachments,
                layout_reference_ids_from_message,
                wants_vision,
            )

            requested_layout_ids = layout_reference_ids_from_message(message, shots)
            if wants_vision(message) or requested_layout_ids:
                await progress("status", "Preparing visual review: packaging reference frames and Asset Library thumbnails…")
                pack = collect_vision_attachments(
                    project_id=project_id,
                    shots=shots,
                    message=message,
                    layout_ref_ids=requested_layout_ids or None,
                )
                vision_b64 = list(pack.get("images_b64") or [])
                vision_note = str(pack.get("note") or "")
                if vision_b64:
                    actions.append(f"vision:{len(vision_b64)}")
                    await progress(
                        "status", f"Attached {len(vision_b64)} image{'s' if len(vision_b64) != 1 else ''} for the multimodal model"
                    )
                else:
                    await progress("status", vision_note or "No viewable images were found")
        except Exception:
            logger.exception("vision pack failed")

    user_uploads: list[dict[str, Any]] = [
        {
            "image_index": index,
            "filename": captions[index - 1] if index <= len(captions) else f"image-{index}.png",
            "data_b64": encoded,
        }
        for index, encoded in enumerate(vision_b64, start=1)
    ] if user_images_b64 else []

    def tool_schemas_for(
        current_project: Project,
        *,
        allow_save_storyboard: bool = True,
    ) -> list[dict[str, Any]]:
        pending_uploads = any(
            not isinstance(upload.get("classification"), dict)
            for upload in user_uploads
        )
        schemas = _director_tool_schemas(
            current_project,
            current_message=message,
            allow_save_storyboard=allow_save_storyboard,
            include_chat_image_import=pending_uploads,
            shots=list_shots(current_project.id),
        )
        if storyboard_budget.repairing:
            schemas = [schema for schema in schemas if schema["function"]["name"] != "set_script"]
        from ...core.managed_runs.context import managed_turn_scope
        scope = managed_turn_scope.get()
        if scope is not None and scope.project_id == current_project.id:
            managed_tools = {"get_status", "inspect_asset", "write_prompt", "start_h3_video",
                             "configure_video_context"}
            schemas = [schema for schema in schemas
                       if schema["function"]["name"] in managed_tools]
        state = current_task_context(current_project.id)
        if state is not None and context_mode(current_project) == "pilot" and not pending_uploads:
            schemas = present_task_tools(schemas, state)
        return schemas

    context_blob = (
        _gpt_generation_context_blob(project, shots, message)
        if _explicit_gpt_image_intent(message) and not _actor_design_intent(message)
        else _project_context_blob(project, shots, message=message)
    )
    user_prompt = (
        f"PROJECT_STATE:\n{context_blob}\n\n"
        + (f"RECENT_CHAT:\n" + "\n".join(hist_lines) + "\n\n" if hist_lines else "")
        + (f"VISION:\n{vision_note}\n\n" if vision_note else "")
        + f"USER:\n{message}\n"
    )
    requested_minimum_duration_s = _requested_minimum_duration_s(message)
    from .turn_identity import current_user_message_id
    user_message_id = current_user_message_id(project_id, message)
    storyboard_budget = _StoryboardSubmissionBudget()
    video_authorization_cache: dict = {}
    offered_tool_schemas = tool_schemas_for(project)
    chat_guides = _director_chat_guides(
        project,
        include_visual_qc=bool(vision_b64),
        current_message=message,
    )

    original_suffix = user_prompt[len(f"PROJECT_STATE:\n{context_blob}"):]

    def refresh_task_prompt(conversation=None):
        if current_task_context(project_id) is None:
            return user_prompt
        from .skill_loader import with_director_skill
        budget_messages = ([{**item, "content": original_suffix} if i == 0 else item
                            for i, item in enumerate(conversation)] if conversation else
                           [{"role": "user", "content": original_suffix}])
        state_text = render_task_context(project, objective=message,
            authority=task_authority(project, offered_tool_schemas), legacy_state=context_blob,
            max_chars=available_packet_chars(system=with_director_skill(DIRECTOR_CHAT_SYSTEM, guides=chat_guides),
                messages=budget_messages, tools=offered_tool_schemas, image_count=len(vision_b64)))
        refreshed = f"PROJECT_STATE:\n{state_text}{original_suffix}"
        if conversation:
            conversation[0] = {**conversation[0], "content": refreshed}
        return refreshed

    user_prompt = refresh_task_prompt()

    try:
        if vision_b64:
            raw = await chat_fn(
                DIRECTOR_CHAT_SYSTEM,
                user_prompt,
                images=vision_b64,
                require_vision=True,
                tools=offered_tool_schemas,
                guides=chat_guides,
            )
        else:
            raw = await chat_fn(
                DIRECTOR_CHAT_SYSTEM,
                user_prompt,
                tools=offered_tool_schemas,
                guides=chat_guides,
            )
    except GenerationActiveError:
        raise
    except Exception as e:
        logger.exception("chat llm failed")
        await progress("runtime", f"Inference failed: {e}")
        hint = (
            "\nIf Comfy just generated an image, the GPU may still be busy or Ollama may need to reload its model. "
            "Wait for composition or video jobs to finish, then retry, or POST /api/director/wake to warm the model.\n"
        )
        if vision_b64:
            hint += (
                "Visual review failed. Confirm that the selected model supports vision and that Ollama is current. "
                "You can switch to a multimodal model or continue with text only.\n"
            )
        return finish(
            f"Director inference is temporarily unavailable: {e}{hint}\n" + _status_summary(project, shots),
            actions,
            image_ids,
        )

    # Native Ollama tool loop. Each tool result is returned to the model so it
    # can produce a grounded final response after the state mutation succeeds.
    if isinstance(raw, dict):
        conversation: list[dict[str, Any]] = [
            {"role": "user", "content": user_prompt}
        ]
        if vision_b64:
            conversation[0]["images"] = vision_b64
        final_reply = ""
        storyboard_retry_pending = False
        from ...core.managed_runs.context import managed_turn_scope
        managed_scope = managed_turn_scope.get()
        tool_turn_limit = (
            5
            if managed_scope is not None and managed_scope.project_id == project_id
            else 4
        )
        safety_limit_reply = (
            "Tool calling exceeded the managed-turn safety limit."
            if tool_turn_limit > 4
            else "Tool calling exceeded the four-turn safety limit."
        )

        for _tool_turn in range(tool_turn_limit):
            native_content, native_think, native_tools = _native_reply(raw)
            if native_think:
                await progress("think", native_think)

            if not native_tools:
                fallback_content, fallback_tools = _parse_tools_from_llm(
                    native_content
                )
                if fallback_tools:
                    native_content = fallback_content
                    native_tools = fallback_tools
                else:
                    should_force_storyboard_continuation = (
                        _tool_turn < 3
                        and not storyboard_budget.exhausted
                        and (
                            (
                                storyboard_retry_pending
                                and _claims_storyboard_resubmission(native_content)
                            )
                            or (
                                _tool_turn == 0
                                and _explicit_storyboard_save_request(message)
                                and not native_content.strip()
                                and bool(native_think.strip())
                            )
                        )
                    )
                    if should_force_storyboard_continuation:
                        conversation.append(
                            {"role": "assistant", "content": native_content}
                        )
                        conversation.append(
                            {
                                "role": "user",
                                "content": _STORYBOARD_CONTINUATION_PROMPT,
                            }
                        )
                        project = load_project(project_id) or project
                        offered_tool_schemas = tool_schemas_for(
                            project,
                            allow_save_storyboard=not storyboard_budget.exhausted,
                        )
                        user_prompt = refresh_task_prompt(conversation)
                        raw = await chat_fn(
                            DIRECTOR_CHAT_SYSTEM,
                            user_prompt,
                            images=vision_b64 or None,
                            messages=conversation,
                            tools=offered_tool_schemas,
                            guides=_director_chat_guides(
                                project,
                                include_visual_qc=bool(vision_b64),
                                current_message=message,
                            ),
                        )
                        continue
                    if not native_content.strip():
                        done_reason = str(
                            raw.get("done_reason") or ""
                        ).strip().lower()
                        if done_reason == "length":
                            final_reply = (
                                "The Director request filled the model's active context window "
                                "before it could answer or call a tool. No project state was "
                                "changed. Please retry; if this continues, ask about one Shot or "
                                "one operation at a time."
                            )
                        else:
                            final_reply = (
                                "The Director model returned no answer or tool action. No project "
                                "state was changed. Please retry the command."
                            )
                        break
                    final_reply = native_content or final_reply
                    break

            project = load_project(project_id) or project
            shots = list_shots(project_id)
            native_tools, unknown_tool_results = _filter_tools_to_offered_schemas(
                native_tools,
                tool_schemas=offered_tool_schemas,
                storyboard_budget=storyboard_budget,
            )
            native_tools, pipeline_notes = sanitize_tools_for_pipeline(
                native_tools, project=project, shots=shots
            )
            for note in pipeline_notes:
                await progress("status", note)

            if not native_tools and not unknown_tool_results:
                notes_text = "\n".join(f"- {note}" for note in pipeline_notes)
                final_reply = "\n\n".join(
                    part for part in (native_content, notes_text) if part
                )
                break

            if native_tools:
                await progress("status", f"Executing {len(native_tools)} model tool(s)...")
            terminal_tool_reply = ""
            conversation_tools = native_tools + [
                tool for tool, _payload in unknown_tool_results
            ]
            conversation.append(
                {
                    "role": "assistant",
                    "content": native_content,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": tool["name"],
                                "arguments": tool.get("args") or {},
                            },
                        }
                        for tool in conversation_tools
                    ],
                }
            )

            for tool in native_tools:
                if storyboard_retry_pending and tool["name"] in _IMAGE_TOOLS:
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_name": tool["name"],
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "blocked": True,
                                    "error": (
                                        "Storyboard save failed. Layout and prompt work is "
                                        "blocked until save_storyboard succeeds."
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue
                if tool["name"] == "save_storyboard":
                    storyboard_save_attempted = True
                structured_results: list[dict[str, Any]] = []
                configuration_block = None
                if tool["name"] == "write_prompt":
                    current_shot = configuration_recovery.target(list_shots(project_id), tool.get("args") or {})
                    if current_shot is not None:
                        configuration_block = configuration_recovery.before_write(current_shot)
                if configuration_block:
                    tool_notes, touched = [configuration_block["error"]], set()
                    structured_results.append(configuration_block)
                else:
                    tool_notes, touched = await run_tools(
                        project_id=project_id,
                        tools=[tool],
                        svc=svc,
                        actions=actions,
                        on_progress=progress_event,
                        result_payloads=structured_results,
                        user_feedback=message,
                        user_message_id=user_message_id,
                        video_authorization_cache=video_authorization_cache,
                        requested_minimum_duration_s=requested_minimum_duration_s,
                        storyboard_budget=storyboard_budget,
                        images=attached_images,
                        user_uploads=user_uploads,
                    )
                image_ids |= touched
                tool_payload: dict[str, Any] = {
                    "ok": True,
                    "notes": tool_notes,
                }
                for structured_result in structured_results:
                    tool_payload.update(structured_result)
                if tool_payload.get("code") == "SHOT_CONFIGURATION_CONFLICT" and not configuration_block:
                    from ...core.managed_runs.context import managed_turn_scope
                    current_shot = load_shot(project_id, tool_payload.get("shot_id"))
                    may_revise = any(schema["function"]["name"] == "revise_shot" for schema in offered_tool_schemas)
                    if managed_turn_scope.get() is not None or not may_revise:
                        configuration_recovery.stop_for_scope(tool_payload)
                    elif current_shot is None:
                        tool_payload.update(retryable=False, concludes_turn=True)
                    else:
                        configuration_recovery.record(current_shot, tool_payload)
                if tool["name"] == "write_prompt":
                    prompt_failure_kind = tool_payload.get("failure_kind", "unknown")
                    prompt_failure_code = tool_payload.get("code") or "PROMPT_GENERATION_FAILED"
                    prompt_failure_message = (
                        str(tool_payload.get("reply") or tool_payload.get("error") or "Prompt generation failed")
                        if tool_payload.get("ok") is False and tool_payload.get("code") != "CONTEXT_REQUIRED"
                        and tool_payload.get("concludes_turn") is not False else ""
                    )
                    if prompt_failure_message:
                        from ...core.managed_runs.context import managed_turn_scope
                        if managed_turn_scope.get() is not None:
                            terminal_tool_reply = prompt_failure_message
                if tool["name"] == "save_storyboard":
                    storyboard_retry_pending = (
                        tool_payload.get("ok") is False
                        and tool_payload.get("blocked") is not True
                    )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_name": tool["name"],
                        "content": json.dumps(
                            tool_payload,
                            ensure_ascii=False,
                        ),
                    }
                )
                if tool_payload.get("concludes_turn") is True:
                    terminal_tool_reply = str(tool_payload.get("reply") or "\n".join(tool_notes)).strip()
                    break
                if tool["name"] in {
                    "queue_gpt_ref_frame",
                    "queue_actor_design",
                    "accept_ref_frame",
                }:
                    terminal_tool_reply = "\n".join(tool_notes).strip()
                if tool["name"] == "write_prompt" and prompt_failure_message:
                    from ...core.managed_runs.context import managed_turn_scope
                    if managed_turn_scope.get() is not None:
                        break

            for tool, unknown_payload in unknown_tool_results:
                conversation.append(
                    {
                        "role": "tool",
                        "tool_name": tool["name"],
                        "content": json.dumps(
                            unknown_payload,
                            ensure_ascii=False,
                        ),
                    }
                )

            if terminal_tool_reply:
                final_reply = terminal_tool_reply
                break

            project = load_project(project_id) or project
            offered_tool_schemas = tool_schemas_for(
                project,
                allow_save_storyboard=not storyboard_budget.exhausted,
            )
            user_prompt = refresh_task_prompt(conversation)
            raw = await chat_fn(
                DIRECTOR_CHAT_SYSTEM,
                user_prompt,
                images=vision_b64 or None,
                messages=conversation,
                tools=offered_tool_schemas,
                guides=_director_chat_guides(
                    project,
                    include_visual_qc=bool(vision_b64),
                    current_message=message,
                ),
            )
            if not isinstance(raw, dict):
                followup_think, final_reply = split_thinking(str(raw).strip())
                if followup_think:
                    await progress("think", followup_think)
                break
            if _tool_turn == tool_turn_limit - 1:
                final_content, final_think, final_tools = _native_reply(raw)
                if final_think:
                    await progress("think", final_think)
                final_reply = (
                    safety_limit_reply
                    if final_tools
                    else final_content or final_reply
                )
                if any(tool.get("name") == "save_storyboard" for tool in final_tools):
                    storyboard_save_blocked = True
                break
        else:
            final_reply = safety_limit_reply

        if not final_reply:
            refreshed_project = load_project(project_id)
            refreshed_shots = list_shots(project_id)
            if refreshed_project:
                final_reply = _status_summary(refreshed_project, refreshed_shots)
        return finish(final_reply, actions, image_ids)

    raw = str(raw).strip()
    model_think, visible = split_thinking(raw)
    # Avoid re-emitting think if stream already pushed it chunk-by-chunk
    if model_think and not thinking_parts:
        await progress("think", model_think)
    await progress("runtime", "Interpreting the model response…")

    reply, tools = _parse_tools_from_llm(visible or raw)
    # If reply still has think leftovers
    t2, reply = split_thinking(reply)
    if t2 and not model_think and not thinking_parts:
        await progress("think", t2)
        model_think = t2
    elif t2 and not model_think:
        model_think = t2

    if not reply and not tools:
        reply = _status_summary(project, shots)

    # Enforce pipeline: script change → plan_shots, never jump to imaging
    project = load_project(project_id) or project
    shots = list_shots(project_id)
    tools, unknown_tool_results = _filter_tools_to_offered_schemas(
        tools,
        tool_schemas=offered_tool_schemas,
        storyboard_budget=storyboard_budget,
    )
    tools, pipeline_notes = sanitize_tools_for_pipeline(
        tools, project=project, shots=shots
    )
    unknown_tool_notes = [
        f"Unknown tool result: {json.dumps(payload, ensure_ascii=False)}"
        for _tool, payload in unknown_tool_results
    ]
    if pipeline_notes:
        for n in pipeline_notes:
            await progress("status", n)

    if tools:
        if any(tool.get("name") == "save_storyboard" for tool in tools):
            storyboard_save_attempted = True
        await progress("status", f"The model requested {len(tools)} tool call{'s' if len(tools) != 1 else ''}…")
        notes, touched = await run_tools(
            project_id=project_id,
            tools=tools,
            svc=svc,
            actions=actions,
            on_progress=progress_event,
            user_feedback=message,
            user_message_id=user_message_id,
            video_authorization_cache=video_authorization_cache,
            requested_minimum_duration_s=requested_minimum_duration_s,
            storyboard_budget=storyboard_budget,
            images=attached_images,
            user_uploads=user_uploads,
        )
        image_ids |= touched
        all_notes = list(unknown_tool_notes) + list(pipeline_notes) + list(notes)
        if all_notes:
            note_block = "\n".join(f"· {n}" for n in all_notes)
            if reply:
                reply = f"{reply}\n\n{note_block}"
            else:
                reply = note_block

        # Optional: if tools ran but reply was empty-ish, refresh summary
        project = load_project(project_id)
        shots = list_shots(project_id)
        if project and not reply.strip():
            reply = _status_summary(project, shots)
    elif pipeline_notes or unknown_tool_notes:
        note_block = "\n".join(
            f"· {n}" for n in [*unknown_tool_notes, *pipeline_notes]
        )
        reply = f"{reply}\n\n{note_block}" if reply else note_block

    return finish(reply or "Okay.", actions, image_ids, thinking=model_think)
