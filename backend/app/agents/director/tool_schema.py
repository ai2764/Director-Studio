"""Director native-tool contracts and per-turn availability policy."""

from __future__ import annotations

import re
from typing import Any, Iterable

from ...config import settings
from ...core.projects.models import AssetCoverageReviewSubmission, Project
from .intent import actor_design_intent, explicit_gpt_image_intent
from .planner import (
    ShotRefsPatchSubmission,
    ShotRevisionSubmission,
    ShotSceneRefSelection,
    StoryboardSubmission,
)


IMAGE_TOOLS = frozenset(
    {
        "queue_ref_frame",
        "queue_gpt_ref_frame",
        "ref_frame",
        "extract_clip_tail_frame",
        "accept_ref_frame",
        "revise_ref_frame",
        "approve_layout",
        "approve",
        "reject_layout",
        "reject",
        "write_prompt",
    }
)
PLAN_TOOLS = frozenset({"plan_shots", "plan"})
STORYBOARD_TOOLS = frozenset({"save_storyboard"})
SCRIPT_TOOLS = frozenset({"set_script"})


def function_tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


SHOT_SELECTOR = {
    "shot_id": {"type": "string", "description": "Exact shot id"},
    "shot_index": {"type": "integer", "minimum": 1},
    "title": {"type": "string", "description": "Shot title"},
    "all": {"type": "boolean", "default": False},
}
LAYOUT_SOURCE_REF_ITEM = {
    "type": "object",
    "properties": {
        "role": {
            "type": "string",
            "enum": [
                "layout_ref_frame",
                "actor",
                "costume",
                "scene",
                "prop",
                "other",
            ],
        },
        "asset_id": {"type": "string", "minLength": 1},
        "file_key": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["role", "asset_id"],
}

LAYOUT_ACTIVATION_MODE = {
    "type": "string",
    "enum": ["replace", "append"],
    "default": "replace",
    "description": (
        "replace regenerates the active composition set; append keeps "
        "existing active Layouts and adds a compatible state."
    ),
}

GPT_REF_FRAME_TOOL = function_tool(
    "queue_gpt_ref_frame",
    (
        "Generate one Layout through the optional local ChatGPT Bridge only "
        "after the user explicitly requests GPT/ChatGPT image generation. "
        "Preserve ordered real inventory sources and assign every ImageN one job. "
        "An Actor source is an authoritative identity reference, not a loose style "
        "hint: preserve exact facial structure, hair, body proportions, and its "
        "approved wardrobe unless an attached Costume source or explicit user "
        "request changes clothing. Use exact file_keys; prefer bust_threeview for "
        "face fidelity and a full-body/master source for wardrobe when both jobs "
        "matter and reference capacity allows. "
        "When no useful source asset exists, use an empty source_refs array for "
        "prompt-only generation and do not mention ImageN."
    ),
    {
        "shot_id": {"type": "string", "minLength": 1},
        "purpose": {"type": "string", "minLength": 1},
        "state_description": {"type": "string", "minLength": 1},
        "time_hint": {"type": "string"},
        "activation_mode": LAYOUT_ACTIVATION_MODE,
        "source_refs": {
            "type": "array",
            "items": LAYOUT_SOURCE_REF_ITEM,
        },
        "generation_prompt": {"type": "string", "minLength": 1},
    },
    required=[
        "shot_id",
        "purpose",
        "state_description",
        "source_refs",
        "generation_prompt",
    ],
)

ACTOR_DESIGN_TOOL = function_tool(
    "queue_actor_design",
    (
        "Generate one reviewable character design. Local is the default provider; "
        "use GPT only when the user explicitly asks for GPT or ChatGPT generation. "
        "Do not save it to the Actor library until the user accepts it."
    ),
    {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "body_description": {"type": "string"},
        "hair_description": {"type": "string"},
        "wardrobe_description": {"type": "string"},
        "provider": {
            "type": "string",
            "enum": ["gpt", "local"],
            "default": "local",
        },
        "generation_prompt": {"type": "string", "minLength": 1},
    },
    required=["name", "description", "generation_prompt"],
)

ACTOR_ACCEPT_TOOL = function_tool(
    "accept_actor_design",
    "Save one succeeded Actor design job after explicit user acceptance.",
    {
        "job_id": {"type": "string", "minLength": 1},
        "name": {"type": "string"},
        "notes": {"type": "string"},
    },
    required=["job_id"],
)

CHAT_IMAGE_CLASSIFICATION_TOOL = function_tool(
    "classify_chat_image",
    (
        "Classify one user-uploaded chat image from its visible contents and the "
        "current user message, give it a concise useful name and factual notes, "
        "then import it into the current project's Library when confidence is "
        "sufficient. Use chat_only when the image is too ambiguous."
    ),
    {
        "image_index": {
            "type": "integer",
            "minimum": 1,
            "maximum": 4,
            "description": "One-based Image number from the current user upload.",
        },
        "kind": {
            "type": "string",
            "enum": [
                "actors",
                "costumes",
                "scenes",
                "props",
                "layouts",
                "chat_only",
            ],
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "Concise human-readable asset name in the user's language.",
        },
        "notes": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": (
                "Factual visible appearance and intended production use; do not "
                "invent details that are not visible or stated by the user."
            ),
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
    },
    required=["image_index", "kind", "name", "notes", "confidence"],
)

DIRECTOR_TOOL_SCHEMAS: list[dict[str, Any]] = [
    ACTOR_DESIGN_TOOL,
    ACTOR_ACCEPT_TOOL,
    function_tool(
        "set_script",
        "Save a new or revised screenplay supplied by the user.",
        {"script": {"type": "string", "minLength": 1}},
        required=["script"],
    ),
    {
        "type": "function",
        "function": {
            "name": "review_asset_coverage",
            "description": (
                "Persist an advisory review of whether the current assets and their "
                "file_keys cover the screenplay. Recommend useful additions, or record "
                "that the user chose to skip. This never blocks storyboarding."
            ),
            "parameters": AssetCoverageReviewSubmission.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_storyboard",
            "description": (
                "Persist the exact complete ordered storyboard you authored for the "
                "current screenplay. Use PROJECT_STATE.script_hash. Preserve every "
                "existing Shot's PROJECT_STATE id in shot_id, and omit shot_id only "
                "for a genuinely new Shot."
            ),
            "parameters": StoryboardSubmission.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revise_shot",
            "description": (
                "Update only explicitly supplied authored fields on exactly one "
                "existing Shot. Preserves neighboring Shots, Picture and voice refs, "
                "Layouts, and historical jobs while invalidating that Shot's stale "
                "prompt and active H3 link."
            ),
            "parameters": ShotRevisionSubmission.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_shot_refs",
            "description": (
                "Replace only the complete ordered Picture bindings on named "
                "existing shots. Use for exact asset additions or recasting without "
                "changing story beats, dialogue, duration, title, or shot order."
            ),
            "parameters": ShotRefsPatchSubmission.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_shot_scene_ref",
            "description": (
                "Apply a human's exact scene asset and file_key selection to one "
                "existing shot while preserving every other Picture binding and "
                "all story fields. The file_key is authoritative: do not reinterpret "
                "camera direction or replace it from filename angle tokens."
            ),
            "parameters": ShotSceneRefSelection.model_json_schema(),
        },
    },
    function_tool("plan_shots", "Plan shots from the current screenplay."),
    function_tool(
        "queue_ref_frame",
        (
            "Generate or regenerate a Layout reference frame for selected shots. "
            "For an additional Layout, use only after discussion establishes a "
            "distinct visual purpose, and set activation_mode=append only when "
            "the user explicitly wants to preserve existing active Layouts. "
            "Provide an explicit continuity brief and "
            "1–3 exact source assets from the inventory so the downstream Qwen "
            "prompt can assign each image a clear visual job."
        ),
        {
            **SHOT_SELECTOR,
            "force": {"type": "boolean", "default": False},
            "purpose": {
                "type": "string",
                "description": "Why this Layout is needed for the shot.",
            },
            "state_description": {
                "type": "string",
                "description": "The exact story or continuity state shown.",
            },
            "time_hint": {
                "type": "string",
                "description": "Optional script or shot timing hint.",
            },
            "activation_mode": LAYOUT_ACTIVATION_MODE,
            "source_refs": {
                "type": "array",
                "maxItems": 3,
                "items": LAYOUT_SOURCE_REF_ITEM,
            },
        },
    ),
    function_tool(
        "extract_clip_tail_frame",
        (
            "Extract the last decoded frame of a succeeded H3 clip into a "
            "pending, unselected Layout on a later shot. Use exact shot IDs. "
            "Clarify rather than guess when the source clip is ambiguous. "
            "Do not visually approve the image."
        ),
        {
            "source_shot_id": {
                "type": "string",
                "minLength": 1,
                "description": "Exact source shot id whose H3 clip is extracted.",
            },
            "target_shot_id": {
                "type": "string",
                "minLength": 1,
                "description": "Exact target shot id that receives the Layout.",
            },
            "source_version": {
                "type": "string",
                "description": "latest, or a generation number such as v2 or 2.",
            },
            "source_job_id": {
                "type": "string",
                "description": "Exact H3 job id when the user named one.",
            },
            "output_kind": {
                "type": "string",
                "enum": ["enhanced", "raw"],
                "description": "Which materialized video output to decode.",
            },
        },
        required=["source_shot_id", "target_shot_id"],
    ),
    function_tool(
        "accept_ref_frame",
        (
            "Record acceptance from this Director conversation on one existing "
            "Layout and select that exact Layout for the H3 Picture pack."
        ),
        {
            **SHOT_SELECTOR,
            "layout_ref_id": {
                "type": "string",
                "minLength": 1,
                "description": "Exact LayoutReference id the user accepted.",
            },
            "feedback": {
                "type": "string",
                "description": "Optional concise reason the Layout is usable.",
            },
        },
        required=["layout_ref_id"],
    ),
    function_tool(
        "revise_ref_frame",
        (
            "Record feedback from this Director conversation on one existing "
            "Layout and generate a linked replacement. Use this instead of "
            "queue_ref_frame when the user critiques a generated reference. "
            "For a clip_tail_frame origin, additional_source_refs (max 2) are "
            "honored; the extracted Layout is always Qwen Image1."
        ),
        {
            **SHOT_SELECTOR,
            "layout_ref_id": {
                "type": "string",
                "minLength": 1,
                "description": "Exact LayoutReference id being critiqued.",
            },
            "feedback": {
                "type": "string",
                "minLength": 1,
                "description": "Concise actionable summary of the user's feedback.",
            },
            "additional_source_refs": {
                "type": "array",
                "maxItems": 2,
                "description": (
                    "Optional extra Qwen sources for a clip_tail_frame redraw. "
                    "Ignored for ordinary generated Layouts."
                ),
                "items": LAYOUT_SOURCE_REF_ITEM,
            },
        },
        required=["layout_ref_id", "feedback"],
    ),
    function_tool(
        "write_prompt",
        "Write or rewrite the six-section H3 production prompt for a shot with a reference frame.",
        dict(SHOT_SELECTOR),
    ),
    function_tool("get_status", "Read the current project and shot status."),
]


def director_tool_schemas(
    project: Project,
    *,
    current_message: str = "",
    allow_save_storyboard: bool = True,
    include_chat_image_import: bool = False,
) -> list[dict[str, Any]]:
    if include_chat_image_import:
        return [CHAT_IMAGE_CLASSIFICATION_TOOL]
    if actor_design_intent(current_message):
        return [ACTOR_DESIGN_TOOL]
    excluded = set()
    if project.script_locked:
        excluded.update(SCRIPT_TOOLS)
    if not allow_save_storyboard:
        excluded.update(STORYBOARD_TOOLS)
    tools = [
        tool
        for tool in DIRECTOR_TOOL_SCHEMAS
        if tool["function"]["name"] not in excluded
    ]
    normalized = current_message.lower()
    shot_layout_turn = bool(
        re.search(r"(?:shot\s*\d+|第\s*\d+\s*镜)", normalized)
        and re.search(
            r"(?:layout|reference(?:\s+frame)?|refs?\b|materials?\b|assets?\b|"
            r"composition|构图|参考帧|首帧|素材|绑定|h3\b|prompt\b)",
            normalized,
        )
    )
    if shot_layout_turn:
        relevant = {
            "revise_shot",
            "patch_shot_refs",
            "set_shot_scene_ref",
            "queue_ref_frame",
            "extract_clip_tail_frame",
            "accept_ref_frame",
            "revise_ref_frame",
            "write_prompt",
            "get_status",
        }
        tools = [
            tool
            for tool in tools
            if tool["function"]["name"] in relevant
        ]
    if settings.gpt_bridge_configured:
        if not shot_layout_turn or explicit_gpt_image_intent(current_message):
            tools.append(GPT_REF_FRAME_TOOL)
    return tools


def director_chat_guides(
    project: Project,
    *,
    include_visual_qc: bool,
    current_message: str = "",
) -> tuple[str, ...]:
    guides: list[str] = []
    if project.script_locked:
        guides.append("script-planning")
    if explicit_gpt_image_intent(current_message) and not actor_design_intent(
        current_message
    ):
        guides.append("reference-frame-generation")
    if include_visual_qc:
        guides.append("visual-qc")
    return tuple(guides)


def offered_tool_names(
    tool_schemas: Iterable[dict[str, Any]],
) -> frozenset[str]:
    return frozenset(
        str(tool.get("function", {}).get("name") or "").strip()
        for tool in tool_schemas
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    )
