"""Director model-context serialization. No chat orchestration or mutation."""

from __future__ import annotations

import json
from typing import Any

from ...core.projects.models import Project, RefRole, Shot


def project_context_blob(
    project: Project,
    shots: list[Shot],
    *,
    message: str = "",
) -> str:
    from .context_io import load_agent_context
    from .intent import shot_ref
    from .service import _inventory, _script_hash

    inv = _inventory(project.id)
    script = project.script_text or ""
    script_hash = _script_hash(script)
    agent_ctx = load_agent_context(project.id)
    planned_hash = (agent_ctx.script_hash if agent_ctx else "") or ""
    shots_stale = bool(shots) and bool(script.strip()) and (
        not planned_hash or planned_hash != script_hash
    )
    coverage_review = project.asset_coverage_review
    coverage_current = bool(
        coverage_review and coverage_review.script_hash == script_hash
    )
    # Suggested next step for the model (also enforced in tool sanitizer).
    if not script.strip():
        next_step = "ask_or_set_script"
    elif (not shots or shots_stale) and not coverage_current:
        next_step = "review_asset_coverage"
    elif not shots or shots_stale:
        next_step = "save_storyboard"
    elif any(
        (s.status.value if hasattr(s.status, "value") else str(s.status))
        in ("ref_frame_pending", "draft", "planning", "blocked", "failed")
        or not s.layout_asset_id
        for s in shots
    ):
        next_step = "queue_ref_frame"
    elif any(
        not all(str(value or "").strip() for value in s.prompt_sections.model_dump().values())
        for s in shots
    ):
        next_step = "write_or_rewrite_prompt"
    else:
        next_step = "ready_for_h3"

    def layout_refs_context(
        shot: Shot,
        *,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        bound_indices: dict[str, list[int]] = {}
        for ref in shot.refs or []:
            if ref.role != RefRole.layout_ref_frame:
                continue
            bound_indices.setdefault(ref.asset_id, []).append(ref.picture_index)

        layouts = list(shot.layout_refs)
        if compact:
            layouts = [
                layout
                for layout in layouts
                if (
                    layout.selected_for_h3
                    or (
                        shot.layout_asset_id
                        and layout.asset_id == shot.layout_asset_id
                    )
                )
                and not layout.superseded_by
                and layout.review_status != "reject"
            ]
            if not layouts:
                layouts = [
                    layout
                    for layout in shot.layout_refs[-1:]
                    if layout.review_status != "reject"
                    and not layout.superseded_by
                ]

        serialized: list[dict[str, Any]] = []
        for layout in layouts:
            matches = bound_indices.get(layout.asset_id or "", [])
            if compact:
                serialized.append(
                    {
                        "id": layout.id,
                        "provider": (
                            layout.provider.value
                            if getattr(layout, "provider", None)
                            else "comfy"
                        ),
                        "purpose": (layout.purpose or "")[:240],
                        "state_description": (
                            layout.state_description or ""
                        )[:400],
                        "job_status": (
                            layout.job_status.value
                            if layout.job_status
                            else None
                        ),
                        "asset_id": layout.asset_id,
                        "review_status": (
                            layout.review_status.value
                            if layout.review_status
                            else None
                        ),
                        "selected_for_h3": layout.selected_for_h3,
                        "picture_index": (
                            matches[0] if len(matches) == 1 else None
                        ),
                    }
                )
                continue
            serialized.append(
                {
                    "id": layout.id,
                    "provider": (
                        layout.provider.value
                        if getattr(layout, "provider", None)
                        else "comfy"
                    ),
                    "purpose": layout.purpose,
                    "state_description": layout.state_description,
                    "time_hint": layout.time_hint,
                    "source_refs": [
                        source.model_dump(mode="json")
                        for source in layout.source_refs
                    ],
                    "job_status": (
                        layout.job_status.value if layout.job_status else None
                    ),
                    "job_error": layout.job_error,
                    "asset_id": layout.asset_id,
                    "review_status": (
                        layout.review_status.value
                        if layout.review_status
                        else None
                    ),
                    "review_feedback": layout.review_feedback,
                    "feedback_source": layout.feedback_source,
                    "feedback_quote": layout.feedback_quote,
                    "revision_of": layout.revision_of,
                    "superseded_by": layout.superseded_by,
                    "selected_for_h3": layout.selected_for_h3,
                    "picture_index": matches[0] if len(matches) == 1 else None,
                    "origin": (
                        layout.origin.model_dump(mode="json")
                        if layout.origin
                        else None
                    ),
                }
            )
        return serialized

    target_shot = shot_ref(message, shots) if message else None
    compact_project_overview = bool(message) and target_shot is None

    def shot_context(index: int, shot: Shot) -> dict[str, Any]:
        summary = {
            "index": index,
            "id": shot.id,
            "title": shot.title,
            "status": (
                shot.status.value
                if hasattr(shot.status, "value")
                else str(shot.status)
            ),
            "layout_review": shot.layout_review_status,
            "layout_asset_id": shot.layout_asset_id,
            "duration_s": shot.duration_s,
        }
        if target_shot is not None and shot.id != target_shot.id:
            return summary
        return {
            **summary,
            "material_review_pending": bool(
                (shot.meta or {}).get("material_review_pending")
            ),
            "material_changes": (shot.meta or {}).get("material_changes"),
            "layout_refs": layout_refs_context(
                shot,
                compact=compact_project_overview,
            ),
            "blocked": shot.blocked_reasons,
            "script_beat": (shot.script_beat or "")[:1200],
            "shot_type": shot.shot_type,
            "camera_angle": shot.camera_angle,
            "camera_motion": shot.camera_motion,
            "composition": shot.composition,
            "refs": [
                {
                    "role": (
                        ref.role.value
                        if hasattr(ref.role, "value")
                        else str(ref.role)
                    ),
                    "asset_id": ref.asset_id,
                    "file_key": ref.file_key,
                    "notes": ref.notes,
                }
                for ref in (shot.refs or [])
            ],
        }

    ctx = {
        "project": {
            "id": project.id,
            "name": project.name,
            "script_locked": project.script_locked,
        },
        "script_chars": len(script),
        "script_hash": script_hash,
        "script_hash_at_last_plan": planned_hash or None,
        "shots_stale_vs_script": shots_stale,
        "asset_coverage_review": (
            coverage_review.model_dump(mode="json") if coverage_review else None
        ),
        "asset_coverage_review_current": coverage_current,
        "recommended_next_step": next_step,
        "pipeline": (
            [
                "review_asset_coverage",
                "save_storyboard",
                "queue_ref_frame",
                "write_prompt",
                "h3_video",
            ]
            if project.script_locked
            else [
                "set_script",
                "review_asset_coverage",
                "save_storyboard",
                "plan_shots",
                "queue_ref_frame",
                "write_prompt",
                "h3_video",
            ]
        ),
        # Full enough for the agent to answer questions about the script without tools.
        "script_text": script[:4000],
        "script_preview": script[:1200],
        "library_inventory": inv,
        "shots": [shot_context(i + 1, shot) for i, shot in enumerate(shots)],
        "note": (
            "Review asset coverage before storyboarding when useful. This is advisory: "
            "save_storyboard and plan_shots remain available, and the user may persist status=skipped."
            if next_step == "review_asset_coverage"
            else
            "If shots_stale_vs_script=true or recommended_next_step=save_storyboard, "
            "author and call save_storyboard against script_hash; do not call queue_ref_frame first. "
            "plan_shots remains available only for compatibility."
            if shots_stale or next_step == "save_storyboard"
            else None
        ),
    }
    return json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))


def gpt_generation_context_blob(
    project: Project,
    shots: list[Shot],
    message: str,
) -> str:
    """Compact context for the schema-constrained GPT image tool turn."""
    from .service import _inventory

    requested = [shot for shot in shots if shot.id and shot.id in message]
    scoped_shots = requested or shots

    def compact_layouts(shot: Shot) -> list[dict[str, Any]]:
        candidates = [
            layout
            for layout in shot.layout_refs
            if layout.selected_for_h3
            or (layout.id and layout.id in message)
            or (layout.asset_id and layout.asset_id in message)
        ]
        if not candidates:
            candidates = list(shot.layout_refs[-3:])
        return [
            {
                "id": layout.id,
                "asset_id": layout.asset_id,
                "provider": layout.provider.value,
                "purpose": (layout.purpose or "")[:500],
                "state_description": (layout.state_description or "")[:1200],
                "time_hint": (layout.time_hint or "")[:300],
                "review_status": (
                    layout.review_status.value if layout.review_status else None
                ),
                "selected_for_h3": layout.selected_for_h3,
                "source_refs": [
                    {
                        "role": source.role.value,
                        "asset_id": source.asset_id,
                        "file_key": source.file_key,
                    }
                    for source in layout.source_refs
                ],
            }
            for layout in candidates
        ]

    ctx = {
        "project": {"id": project.id, "name": project.name},
        "script_preview": (project.script_text or "")[:1500],
        "library_inventory": _inventory(project.id),
        "shots": [
            {
                "index": shots.index(shot) + 1,
                "id": shot.id,
                "title": shot.title,
                "script_beat": (shot.script_beat or "")[:1600],
                "shot_type": shot.shot_type,
                "camera_angle": shot.camera_angle,
                "camera_motion": shot.camera_motion,
                "composition": shot.composition,
                "duration_s": shot.duration_s,
                "status": shot.status.value,
                "refs": [
                    {
                        "role": ref.role.value,
                        "asset_id": ref.asset_id,
                        "file_key": ref.file_key,
                        "picture_index": ref.picture_index,
                        "notes": (ref.notes or "")[:500],
                    }
                    for ref in shot.refs
                ],
                "layout_candidates": compact_layouts(shot),
            }
            for shot in scoped_shots
        ],
        "instruction": (
            "Return queue_gpt_ref_frame parameters only. Use exact asset IDs and "
            "file_keys from this context when useful. source_refs may be empty for "
            "prompt-only generation; in that case generation_prompt must not mention "
            "ImageN. Otherwise generation_prompt must assign Image1..ImageN in the "
            "same order as source_refs. Actor images are authoritative identity and "
            "character-design anchors, not loose style hints: preserve exact face, "
            "hair, body proportions, and approved wardrobe unless the user explicitly "
            "requests a wardrobe change or supplies a Costume source. Never reduce an "
            "Actor binding to 'identity only'. Use exact file_keys; prefer "
            "bust_threeview for facial fidelity and a full-body/master source for "
            "wardrobe when both jobs matter and reference capacity allows."
        ),
    }
    return json.dumps(ctx, ensure_ascii=False, indent=2)
