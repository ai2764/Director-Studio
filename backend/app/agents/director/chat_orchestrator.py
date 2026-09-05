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

Decide for yourself when to call tools; the user does not need to memorize commands. PROJECT_STATE includes the script preview, shot list, Asset Library inventory, and recommended_next_step.

Communication:
- Reply in English, naturally and concisely, like a production colleague.
- For questions about the project, answer only. Do not call set_script or mutate state.
- When project state must change, use the provided native tools. Never print tool JSON in the response or claim work completed before a tool succeeds.
- You are responsible for asset casting. Never invent an asset ID that is absent from the library inventory.
- When the user uploads images, classify every Image in the same turn from both its visible contents and the user's message. Use classify_chat_image once per Image before other state changes. Supply a concise name in the user's language and factual notes covering visible appearance and intended production use. Use chat_only when the classification is genuinely uncertain.

Recommended pipeline; use judgment to decide when to advance:
1) set_script — save a new or revised story supplied by the user
2) review_asset_coverage — before first storyboarding or after a script change, inspect the script and available file_keys; recommend only useful missing actor angles, scene angles/zones, props, costumes, or future Layout states. Explain why each would help. This is advisory: the user may skip it, and storyboard tools remain available.
3) revise_shot — for an authored-field change to exactly one Shot, update only the supplied fields and preserve all other Shots, refs, voices, and Layouts. If the same request also asks for a rewritten production prompt, call revise_shot then write_prompt
   save_storyboard — reserve complete storyboard replacement for first authoring, Shot count/order changes, or coordinated multi-Shot revisions; save against PROJECT_STATE.script_hash, copy each existing Shot's id into shot_id unchanged, and omit shot_id only for genuinely new Shots
   candidates are checked before replacement; if the tool returns observed issues, revise your own complete payload and resubmit
   each chat turn permits up to three automatic save_storyboard submissions; a new user turn starts with a fresh budget and may continue the discussion
   proposed storyboards may be discussed without persistence; only a validated save_storyboard result persists, and discussion does not require an approval gate or an immediate save
   after the automatic budget is exhausted, explain the unresolved issues and invite further user discussion instead of treating the project as permanently failed
   plan_shots remains available only as a compatibility shortcut that invokes the separate legacy planner
   when only Picture bindings change, use patch_shot_refs instead; it cannot rewrite story beats, dialogue, durations, titles, or shot order. Do not generate a Layout merely because Library refs were added, removed, or replaced; queue_ref_frame is only for an explicit composition-reference request
   when a human specifies an exact scene asset and file_key for one Shot, use set_shot_scene_ref instead of patch_shot_refs; the human-specified file_key is authoritative, so do not reinterpret h90/h270/h315 or other filename tokens as camera direction
   set_shot_scene_ref changes only that Shot's existing scene Picture binding; it does not regenerate Layouts or rewrite H3 prompts, so tell the user those may still reflect the previous scene
4) queue_ref_frame — generate a composition reference after shots match the current script
   when the user proposes an additional Layout, first inspect the existing Layout images and discuss whether it has a distinct visual job; do not queue while the user is still exploring the idea
   after the user confirms, define the new Layout's purpose, exact continuity state, and timing, select 1–3 real source assets from PROJECT_STATE, and call queue_ref_frame with explicit purpose, state_description, time_hint, source_refs, and activation_mode="append"
   ordinary generation or regeneration uses activation_mode="replace"; append only when the user explicitly asks to keep the existing Layout and add another compatible state in the same continuous Shot
   multiple active Layouts all condition the entire H3 clip; never describe an appended Layout as beginning at a timestamp, and recommend a separate Shot when early subject leakage would break the beat
   when PROJECT_STATE marks a target Shot material_review_pending, audit its current refs and material_changes before calling write_prompt: identify missing critical subjects or scenes and incompatible active Layout states; if a blocking issue exists, ask one concrete question and do not call write_prompt. A three-view Actor or Scene board is allowed as a Picture and is not a problem by itself.
   choose source_refs for what the new frame must establish: for example, add the entering actor or newly introduced prop alongside a scene/Layout source that preserves spatial continuity; never add sources merely to fill slots
   when the user explicitly chooses GPT/ChatGPT image generation, use queue_gpt_ref_frame; when no provider is specified, default to the local queue_ref_frame tool
   a question about GPT capability is not a generation request; answer it without calling either generation tool
   never fall back from failed GPT generation to local Comfy, or from failed local generation to GPT, unless the user chooses the other provider
   for GPT, source_refs may be empty when no useful real asset exists; then write a prompt-only generation_prompt without ImageN labels. When sources are attached, name every Image1…ImageN and state the visual job of each. An Actor image is an authoritative character reference, never a loose style hint: require the same exact identity, facial structure, hair, body proportions, and approved wardrobe. Do not write "identity only" or invent replacement clothing. A wardrobe change requires an attached Costume source or an explicit user request. Use an exact actor file_key; prefer bust_threeview for face fidelity and a full-body/master source for wardrobe when both are important and capacity allows. Always request one final frame rather than a collage
5) extract_clip_tail_frame — extract the last decoded frame of a succeeded H3 clip as a pending Layout on a later shot
   parse natural language into exact source_shot_id and target_shot_id plus version/job/output selectors; clarify rather than guess
   do not visually approve the extracted image; wait for the user to accept it or request a redraw
6) accept_ref_frame — when the user accepts an existing Layout, record the dialogue decision and select that exact Layout for H3
   acceptance rewrites the target shot H3 prompt with its real Picture index; do not also call write_prompt in the same tool batch
7) revise_ref_frame — when the user critiques an existing Layout and asks for another version, record the feedback on that exact Layout and generate a linked replacement
   for a tail-frame origin, the extracted frame is Image1; add other references only when they have a specific job
8) write_prompt — generate or rewrite the six H3 sections after a reference frame exists; no approval step is required
9) H3 video generation happens later in Production

Tools (name + args):
- set_script  {"script":"..."}  // only when the user supplies or changes story content; a question is not set_script
- review_asset_coverage  {"expected_script_hash":"...","status":"reviewed|skipped","recommendations":[],"notes":"..."}
- save_storyboard  {"expected_script_hash":"...","shots":[complete ShotDraft objects]}  // preserve PROJECT_STATE Shot ids in shot_id; omit only for new Shots
- revise_shot  {"shot_id":"...","script_beat":"..."}  // partial authored fields for exactly one Shot; follow with write_prompt when requested
- patch_shot_refs  {"updates":[{"shot_id":"...","refs":[complete ordered Picture bindings]}]}
- set_shot_scene_ref  {"shot_id":"...","scene_asset_id":"...","file_key":"..."}  // exact human override; never infer a replacement file_key
- plan_shots  {}  // compatibility shortcut; prefer save_storyboard for natural authoring/revision
- queue_ref_frame  {"shot_index":1,"activation_mode":"replace|append"} | {"all":true} | {"shot_id":"..."} | {"force":true}
- queue_gpt_ref_frame  {"shot_id":"...","purpose":"...","state_description":"...","time_hint":"...","activation_mode":"replace|append","source_refs":[],"generation_prompt":"..."}  // use only when the user chooses GPT/ChatGPT
- queue_actor_design  {"name":"...","description":"...","body_description":"...","hair_description":"...","wardrobe_description":"...","provider":"gpt|local","generation_prompt":"..."}  // generate a review image; local is default, GPT requires an explicit user request
- accept_actor_design  {"job_id":"...","name":"...","notes":"..."}  // only after the user explicitly accepts the shown design
- classify_chat_image  {"image_index":1,"kind":"actors|costumes|scenes|props|layouts|chat_only","name":"...","notes":"...","confidence":0.0}  // required once for every current user upload
- extract_clip_tail_frame  {"source_shot_id":"...","target_shot_id":"...","source_version":"latest|vN","source_job_id":null,"output_kind":"enhanced|raw"}
- accept_ref_frame  {"shot_id":"...","layout_ref_id":"...","feedback":"optional concise acceptance note"}
- revise_ref_frame  {"shot_id":"...","layout_ref_id":"...","feedback":"concise actionable summary","additional_source_refs":[]}
- write_prompt / get_status

Vision: the system may attach Image 1…N when the user asks you to inspect references or composition. Describe only what is actually visible.

Tool-call rules:
1) Use native tool calls for state changes; do not disguise them as natural language or Markdown JSON.
2) Tool results are returned to you. Only then briefly explain what actually completed.
3) Do not call tools for a question that only needs an answer."""


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
    """Extract an explicitly requested minimum duration from the current message."""
    text = message or ""
    number = r"(\d+(?:\.\d+)?)"
    unit = r"(seconds?|secs?|s|minutes?|mins?|min)"
    patterns = (
        rf"(?:at\s+least|minimum(?:\s+duration)?(?:\s+of)?|no\s+less\s+than)\s+{number}\s*{unit}\b",
        rf"{number}\s*{unit}\s*(?:minimum|at\s+minimum)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit_value = match.group(2).lower()
        if unit_value.startswith("m"):
            value *= 60.0
        return max(value, 0.0)
    return 0.0


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

    names = [_tool_name(t) for t in tools if isinstance(t, dict)]
    if names and set(names) <= {"queue_actor_design", "accept_actor_design"}:
        return tools, notes
    has_set = any(n in _SCRIPT_TOOLS for n in names)
    has_plan = any(n in _PLAN_TOOLS or n in _STORYBOARD_TOOLS for n in names)
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
    has_plan = any(n in _PLAN_TOOLS or n in _STORYBOARD_TOOLS for n in names2)

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
        s2 = await svc.write_prompts_after_layout(s2.id)
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
            s2 = await svc.write_prompts_after_layout(s.id)
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
        return _director_tool_schemas(
            current_project,
            current_message=message,
            allow_save_storyboard=allow_save_storyboard,
            include_chat_image_import=pending_uploads,
        )

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
    storyboard_budget = _StoryboardSubmissionBudget()
    offered_tool_schemas = tool_schemas_for(project)
    chat_guides = _director_chat_guides(
        project,
        include_visual_qc=bool(vision_b64),
        current_message=message,
    )

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

        for _tool_turn in range(4):
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
                tool_notes, touched = await run_tools(
                    project_id=project_id,
                    tools=[tool],
                    svc=svc,
                    actions=actions,
                    on_progress=progress_event,
                    result_payloads=structured_results,
                    user_feedback=message,
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
                if tool["name"] in {
                    "queue_gpt_ref_frame",
                    "queue_actor_design",
                    "accept_ref_frame",
                }:
                    terminal_tool_reply = "\n".join(tool_notes).strip()

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
            if _tool_turn == 3:
                final_content, final_think, final_tools = _native_reply(raw)
                if final_think:
                    await progress("think", final_think)
                final_reply = (
                    "Tool calling exceeded the four-turn safety limit."
                    if final_tools
                    else final_content or final_reply
                )
                if any(tool.get("name") == "save_storyboard" for tool in final_tools):
                    storyboard_save_blocked = True
                break
        else:
            final_reply = "Tool calling exceeded the four-turn safety limit."

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
