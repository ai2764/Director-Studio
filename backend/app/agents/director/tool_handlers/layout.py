"""Layout generation, review, selection, and prompt tools."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from ....config import settings
from ....core.library.store import load_asset
from ....core.projects.layouts import (
    GptLayoutBrief,
    LayoutBrief,
    LayoutReviewStatus,
    LayoutSourceRef,
    mirror_legacy_layout_fields,
    sync_selected_layout_refs,
)
from ....core.projects.models import Shot, ShotStatus
from ....core.projects.store import load_shot, save_shot
from ....core.projects.transitions import (
    apply_transition,
    review_layout_reference,
    select_layout_reference,
)
from ....core.schemas import JobStatus
from ..intent import (
    layout_activation_mode,
    resolve_shot,
    validate_gpt_generation_prompt,
)
from ..reference_service import build_tail_frame_revision_brief
from ..service import DirectorService

logger = logging.getLogger("director_studio.director.tool_handlers.layout")

_LAYOUT_TOOL_NAMES = frozenset({"accept_ref_frame", "revise_ref_frame", "queue_gpt_ref_frame", "queue_ref_frame", "ref_frame", "approve_layout", "approve", "write_prompt", "rewrite_prompt", "write_prompts", "reject_layout", "reject"})

_LIBRARY_KIND_BY_ROLE = {
    "actor": "actors",
    "scene": "scenes",
    "prop": "props",
    "layout_ref_frame": "layouts",
}


def _accepted_picture_order_note(
    shot: Shot,
    *,
    accepted_layout: Any,
) -> str:
    """Describe the persisted H3 Picture pack without asking the LLM to infer it."""
    lines = ["Current H3 Picture order:"]
    for ref in sorted(shot.refs, key=lambda item: item.picture_index):
        role = ref.role.value
        kind = _LIBRARY_KIND_BY_ROLE.get(role)
        asset = load_asset(kind, ref.asset_id) if kind else None
        asset_name = asset.name if asset is not None else ref.asset_id
        file_key = ref.file_key or ("layout" if role == "layout_ref_frame" else None)
        file_suffix = f" ({file_key})" if file_key else ""

        if ref.asset_id == accepted_layout.asset_id:
            origin = getattr(accepted_layout, "origin", None)
            if origin is not None and getattr(origin, "kind", None) == "clip_tail_frame":
                source_id = str(getattr(origin, "source_shot_id", "") or "").strip()
                source_shot = load_shot(shot.project_id, source_id) if source_id else None
                source_name = source_shot.title if source_shot is not None else source_id
                label = f"Tail frame from {source_name}: {asset_name}"
            else:
                label = f"Accepted Layout: {asset_name}"
        else:
            label = f"{role.replace('_', ' ').title()}: {asset_name}"
        lines.append(f"- Picture {ref.picture_index} — {label}{file_suffix}")
    return "\n".join(lines)


async def handle_layout_tool(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    shots: list[Shot],
    svc: DirectorService,
    runtime: Any,
    actions: list[str],
    notes: list[str],
    touched: set[str],
    prompt_written_shot_ids: set[str],
    result_payloads: list[dict[str, Any]] | None,
    images: list[Any] | None,
    user_feedback: str,
    on_progress: Any,
    refresh_shots: Callable[[], list[Shot]],
) -> bool:
    """Handle Layout-domain tools and return whether the name was recognized."""
    if name not in _LAYOUT_TOOL_NAMES:
        return False

    if name == "accept_ref_frame":
        shot = resolve_shot(
            shots,
            shot_id=args.get("shot_id"),
            shot_index=args.get("shot_index") or args.get("index"),
            title=args.get("title"),
        )
        if not shot and len(shots) == 1:
            shot = shots[0]
        if not shot:
            notes.append("accept_ref_frame: specify a shot")
            return True

        layout_ref_id = str(args.get("layout_ref_id") or "").strip()
        feedback = str(args.get("feedback") or "").strip()
        target = next(
            (layout for layout in shot.layout_refs if layout.id == layout_ref_id),
            None,
        )
        if target is None:
            raise ValueError(f"LayoutReference not found: {layout_ref_id}")
        if not target.asset_id:
            raise ValueError(
                f"LayoutReference has no generated image to accept: {layout_ref_id}"
            )

        accepted = review_layout_reference(
            shot,
            target.id,
            LayoutReviewStatus.usable,
            feedback,
            feedback_source="director_chat",
            feedback_quote=(user_feedback or "").strip(),
        )
        try:
            selected = select_layout_reference(accepted, target.id, True)
            packed = sync_selected_layout_refs(selected)
        except ValueError as exc:
            if "at most 9 Picture" not in str(exc):
                raise
            overflow = mirror_legacy_layout_fields(
                accepted,
                compatibility_primary_layout_id=target.id,
            ).model_copy(update={"status": ShotStatus.needs_review})
            save_shot(overflow)
            runtime.mark_layout_review(target.asset_id, "approved")
            actions.append(f"accept_ref_frame:{shot.id}")
            touched.add(shot.id)
            notes.append(
                f"Layout {target.id} is reviewed but unselected because "
                f"{exc}. Ask the user which existing Picture reference "
                "to remove before it can enter the H3 pack."
            )
            return True

        accepted = mirror_legacy_layout_fields(
            packed,
            compatibility_primary_layout_id=target.id,
        ).model_copy(update={"status": ShotStatus.needs_review})
        save_shot(accepted)
        runtime.mark_layout_review(target.asset_id, "approved")
        actions.append(f"accept_ref_frame:{shot.id}")
        touched.add(shot.id)
        notes.append(
            f"Selected Layout {target.id} for H3 on **{shot.title}** and recorded the Director dialogue decision."
        )
        writer = getattr(svc, "write_prompts_after_layout", None)
        if writer is not None:
            try:
                await writer(shot.id)
                prompt_written_shot_ids.add(shot.id)
                actions.append(f"write_prompt:{shot.id}")
                notes.append(
                    "Rewrote the H3 prompt with the accepted Layout's real Picture index."
                )
            except Exception as exc:
                logger.exception(
                    "write_prompt after accept_ref_frame failed for %s",
                    shot.id,
                )
                notes.append(f"write_prompt after accept failed: {exc}")
        persisted = load_shot(project_id, shot.id) or accepted
        notes.append(
            _accepted_picture_order_note(
                persisted,
                accepted_layout=target,
            )
        )

    elif name == "revise_ref_frame":
        shot = resolve_shot(
            shots,
            shot_id=args.get("shot_id"),
            shot_index=args.get("shot_index") or args.get("index"),
            title=args.get("title"),
        )
        if not shot and len(shots) == 1:
            shot = shots[0]
        if not shot:
            notes.append("revise_ref_frame: specify a shot")
            return True

        layout_ref_id = str(args.get("layout_ref_id") or "").strip()
        feedback = str(args.get("feedback") or "").strip()
        target = next(
            (layout for layout in shot.layout_refs if layout.id == layout_ref_id),
            None,
        )
        if target is None:
            raise ValueError(f"LayoutReference not found: {layout_ref_id}")
        if not target.asset_id:
            raise ValueError(
                f"LayoutReference has no generated image to revise: {layout_ref_id}"
            )
        if not feedback:
            raise ValueError("dialogue feedback is required")

        tail_origin = (
            target.origin is not None
            and getattr(target.origin, "kind", None) == "clip_tail_frame"
        )
        extra_payload = args.get("additional_source_refs") or []
        if tail_origin:
            if extra_payload and not isinstance(extra_payload, list):
                raise ValueError("additional_source_refs must be a list")
            additional_refs = [
                LayoutSourceRef.model_validate(item)
                for item in extra_payload
            ]
            if len(additional_refs) > 2:
                raise ValueError(
                    "revise_ref_frame: a tail-frame redraw accepts at most 3 "
                    "Qwen source images (the extracted Layout plus up to 2 "
                    "additional sources)"
                )
            review_status = LayoutReviewStatus.usable_with_repair
            asset_review = "usable_with_repair"
            revision_brief = build_tail_frame_revision_brief(
                target,
                additional_refs,
                feedback=feedback,
            )
        else:
            review_status = LayoutReviewStatus.reject
            asset_review = "rejected"
            revision_brief = LayoutBrief(
                purpose=target.purpose,
                state_description=target.state_description,
                time_hint=target.time_hint,
            )
        revision_brief = revision_brief.model_copy(
            update={"activation_mode": "append"}
        )

        reviewed = review_layout_reference(
            shot,
            target.id,
            review_status,
            feedback,
            feedback_source="director_chat",
            feedback_quote=(user_feedback or "").strip(),
        )
        reviewed = sync_selected_layout_refs(reviewed)
        reviewed = mirror_legacy_layout_fields(
            reviewed,
            compatibility_primary_layout_id=target.id,
        ).model_copy(
            update={
                "status": ShotStatus.ref_frame_pending,
                "feedback": feedback,
            }
        )
        save_shot(reviewed)
        runtime.mark_layout_review(target.asset_id, asset_review)

        before_ids = {layout.id for layout in reviewed.layout_refs}
        generated = await svc.queue_reference_frame(
            shot.id,
            brief=revision_brief,
            force=True,
        )
        replacement = next(
            (
                layout
                for layout in generated.layout_refs
                if layout.id not in before_ids
            ),
            None,
        )
        if replacement is None:
            notes.append(
                f"Recorded dialogue feedback for **{shot.title}**, but no replacement Layout was queued."
            )
            touched.add(shot.id)
            return True

        linked_layouts = []
        for layout in generated.layout_refs:
            if layout.id == target.id:
                layout = layout.model_copy(
                    update={"superseded_by": replacement.id}
                )
            elif layout.id == replacement.id:
                layout = layout.model_copy(
                    update={"revision_of": target.id}
                )
            linked_layouts.append(layout)
        linked = generated.model_copy(update={"layout_refs": linked_layouts})
        linked = mirror_legacy_layout_fields(
            linked,
            compatibility_primary_layout_id=replacement.id,
        )
        linked = sync_selected_layout_refs(linked)
        save_shot(linked)
        actions.append(f"revise_ref_frame:{shot.id}")
        touched.add(shot.id)
        notes.append(
            f"Recorded dialogue feedback for **{shot.title}** on Layout "
            f"{target.id} and queued linked revision {replacement.id}."
        )

    elif name == "queue_gpt_ref_frame":
        if not settings.gpt_bridge_configured:
            raise runtime.gpt_tool_error(
                "configuration",
                "Configure DS_GPT_BRIDGE_BASE_URL and DS_GPT_BRIDGE_ENV_FILE before using GPT image generation",
            )
        shot_id = str(args.get("shot_id") or "").strip()
        if not shot_id or load_shot(project_id, shot_id) is None:
            raise runtime.gpt_tool_error(
                "request_validation",
                f"queue_gpt_ref_frame requires an exact shot_id in this project: {shot_id or '(missing)'}",
            )
        try:
            brief_payload = dict(args)
            brief_payload.setdefault(
                "activation_mode",
                layout_activation_mode(user_feedback),
            )
            brief = GptLayoutBrief.model_validate(brief_payload)
        except Exception as exc:
            raise runtime.gpt_tool_error(
                "request_validation",
                f"Invalid GPT Layout request: {exc}",
            ) from exc
        try:
            validate_gpt_generation_prompt(
                brief.generation_prompt,
                len(brief.source_refs),
            )
        except ValueError as exc:
            raise runtime.gpt_tool_error("prompt_validation", str(exc)) from exc

        queued = await svc.queue_gpt_reference_frame(
            shot_id,
            brief=brief,
        )
        layout = next(
            (
                item
                for item in reversed(queued.layout_refs)
                if item.provider.value == "gpt" and item.job_id
            ),
            None,
        )
        if layout is None or not layout.job_id:
            raise runtime.gpt_tool_error(
                "job_state",
                "GPT Layout request did not create a durable Layout job",
            )
        terminal = await runtime.await_pipeline_job(layout.job_id)
        if terminal is None:
            raise runtime.gpt_tool_error(
                "job_state",
                f"GPT Layout job disappeared: {layout.job_id}",
            )
        if terminal.status != JobStatus.succeeded:
            error = str(terminal.error or "GPT Layout generation did not succeed")
            match = re.match(r"([a-z_]+):\s*", error)
            stage = match.group(1) if match else "generation"
            raise runtime.gpt_tool_error(stage, error)

        refreshed = load_shot(project_id, shot_id)
        promoted = next(
            (
                item
                for item in (refreshed.layout_refs if refreshed else [])
                if item.id == layout.id
            ),
            layout,
        )
        actions.append(f"gpt_ref_frame:{shot_id}")
        touched.add(shot_id)
        if images is not None and promoted.asset_id:
            target_shot = refreshed or queued
            images.append(
                runtime.chat_image_factory(
                    url=(
                        "/api/files/library/layouts/"
                        f"{promoted.asset_id}/layout.png"
                    ),
                    caption=f"GPT Layout · {target_shot.title}",
                    shot_id=shot_id,
                )
            )
        if result_payloads is not None:
            result_payloads.append(
                {
                    "ok": True,
                    "provider": "gpt",
                    "job_id": terminal.id,
                    "layout_ref_id": promoted.id,
                    "asset_id": promoted.asset_id,
                    "review_status": (
                        promoted.review_status.value
                        if promoted.review_status
                        else None
                    ),
                }
            )
        notes.append(
            f"GPT Layout {promoted.id} is ready for human review on shot {shot_id}."
        )

    elif name == "queue_ref_frame" or name == "ref_frame":
        force = bool(args.get("force") or args.get("regen") or args.get("redo"))
        has_layout_brief = any(
            key in args
            for key in (
                "purpose",
                "state_description",
                "time_hint",
                "source_refs",
                "activation_mode",
            )
        )
        explicit_brief: LayoutBrief | None = None
        purpose = str(args.get("purpose") or "")
        if has_layout_brief:
            brief_payload: dict[str, Any] = {
                "state_description": str(
                    args.get("state_description") or ""
                ),
                "time_hint": str(args.get("time_hint") or ""),
                "source_refs": list(args.get("source_refs") or []),
                "activation_mode": str(
                    args.get("activation_mode")
                    or layout_activation_mode(user_feedback)
                ),
            }
            if "purpose" in args:
                brief_payload["purpose"] = purpose
            explicit_brief = LayoutBrief.model_validate(brief_payload)
        if args.get("all"):
            actions.append("ref_frame_all")
            if explicit_brief is not None:
                if any(shot.layout_refs for shot in shots) and not purpose.strip():
                    raise ValueError(
                        "an additional Layout requires a distinct purpose"
                    )
                for shot in shots:
                    updated_shot = await svc.queue_reference_frame(
                        shot.id,
                        brief=explicit_brief,
                        force=force,
                    )
                    notes.append(runtime.explicit_layout_queue_note(updated_shot))
                    touched.add(shot.id)
                return True
            # Agent said "all" with no pending → treat as redo
            if not force:
                live = refresh_shots()
                needs = any(
                    s.status
                    in (
                        ShotStatus.draft,
                        ShotStatus.planning,
                        ShotStatus.ref_frame_pending,
                        ShotStatus.blocked,
                        ShotStatus.failed,
                    )
                    for s in live
                )
                if not needs and live:
                    force = True
            updated = await svc.queue_ref_frames(
                project_id, shot_ids=None, force=force
            )
            if not updated and not force:
                force = True
                updated = await svc.queue_ref_frames(
                    project_id, shot_ids=None, force=True
                )
            if not updated:
                live = refresh_shots()
                if len(live) == 1:
                    updated = await svc.queue_ref_frames(
                        project_id, shot_ids=[live[0].id], force=True
                    )
                    force = True
            queued = [
                s
                for s in updated
                if s.ref_frame_job_id and s.status != ShotStatus.blocked
            ]
            blocked = [s for s in updated if s.status == ShotStatus.blocked]
            if queued:
                notes.append(
                    f"Queued {len(queued)} composition reference job{'s' if len(queued) != 1 else ''}"
                    + (" including regeneration" if force else "")
                )
            elif blocked:
                notes.append(
                    "Composition references could not be queued; blocked shots: "
                    + "; ".join(
                        f"{s.title}: {', '.join(s.blocked_reasons or [])}"
                        for s in blocked[:4]
                    )
                )
            else:
                notes.append(
                    "No shots are available to queue; video jobs may already be queued or running. "
                    "Request regeneration for a specific shot to force another reference."
                )
            for s in updated:
                touched.add(s.id)
        else:
            shot = resolve_shot(
                shots,
                shot_id=args.get("shot_id"),
                shot_index=args.get("shot_index") or args.get("index"),
                title=args.get("title"),
            )
            if not shot and len(shots) == 1:
                shot = shots[0]
            if not shot:
                notes.append("queue_ref_frame: specify a shot by index or title")
                return True
            actions.append(f"ref_frame:{shot.id}")
            if explicit_brief is not None:
                if shot.layout_refs and not purpose.strip():
                    raise ValueError(
                        "an additional Layout requires a distinct purpose"
                    )
                s2 = await svc.queue_reference_frame(
                    shot.id,
                    brief=explicit_brief,
                    force=force,
                )
                notes.append(runtime.explicit_layout_queue_note(s2))
                touched.add(shot.id)
                return True
            prev_job = shot.ref_frame_job_id
            updated = await svc.queue_ref_frames(
                project_id, shot_ids=[shot.id], force=True
            )
            s2 = load_shot(project_id, shot.id) or shot
            if s2.blocked_reasons:
                notes.append(
                    f"Composition reference blocked for **{s2.title}**: {'; '.join(s2.blocked_reasons)}"
                )
            elif not updated:
                notes.append(
                    f"Could not queue a composition reference for **{s2.title}** "
                    f"(status {s2.status.value}); a shot rendering video cannot regenerate its layout."
                )
            elif s2.ref_frame_job_id and s2.ref_frame_job_id != prev_job:
                notes.append(
                    f"Requeued the composition reference for **{s2.title}** (job {s2.ref_frame_job_id})"
                )
            else:
                notes.append(
                    f"Queued the composition reference for **{s2.title}**"
                    + (
                        f" (job {s2.ref_frame_job_id})"
                        if s2.ref_frame_job_id
                        else ""
                    )
                )
            touched.add(shot.id)

    elif name == "approve_layout" or name == "approve":
        write_prompt = args.get("write_prompt")
        if write_prompt is None:
            write_prompt = True
        write_prompt = bool(write_prompt)
        if args.get("all"):
            actions.append("approve_all")
            n = 0
            for s in list(shots):
                if (
                    s.status == ShotStatus.needs_review
                    or s.layout_review_status == "pending_review"
                ):
                    try:
                        s2, note = await runtime.approve_layout_with_prompt(
                            s,
                            svc=svc,
                            write_prompt=write_prompt,
                            on_progress=on_progress,
                        )
                        n += 1
                        touched.add(s2.id)
                        notes.append(note)
                    except ValueError:
                        continue
            notes.append(f"Approved {n} layout{'s' if n != 1 else ''} in total")
            shots = refresh_shots()
        else:
            shot = resolve_shot(
                shots,
                shot_id=args.get("shot_id"),
                shot_index=args.get("shot_index") or args.get("index"),
                title=args.get("title"),
            )
            if not shot and len(shots) == 1:
                shot = shots[0]
            if not shot:
                notes.append("approve_layout: specify a shot")
                return True
            try:
                s2, note = await runtime.approve_layout_with_prompt(
                    shot,
                    svc=svc,
                    write_prompt=write_prompt,
                    on_progress=on_progress,
                )
            except ValueError as e:
                notes.append(f"approve_layout failed: {e}")
                return True
            actions.append(f"approve:{s2.id}")
            notes.append(note)
            touched.add(s2.id)
            shots = refresh_shots()

    elif name in ("write_prompt", "rewrite_prompt", "write_prompts"):
        shot = resolve_shot(
            shots,
            shot_id=args.get("shot_id"),
            shot_index=args.get("shot_index") or args.get("index"),
            title=args.get("title"),
        )
        if not shot and len(shots) == 1:
            shot = shots[0]
        if not shot:
            notes.append("write_prompt: specify a shot")
            return True
        if shot.id in prompt_written_shot_ids:
            notes.append(
                f"Prompt already rewritten for **{shot.title}** after Layout acceptance; "
                "skipped duplicate write_prompt."
            )
            return True
        actions.append(f"write_prompt:{shot.id}")
        await runtime.emit(
            on_progress, "status", f"Writing the six-section H3 prompt for {shot.title}…"
        )
        try:
            s2 = await svc.write_prompts_after_layout(shot.id)
            notes.append(f"Prompt written for **{s2.title}**")
            touched.add(s2.id)
        except Exception as e:
            logger.exception("write_prompt tool failed")
            notes.append(f"Prompt writing failed: {e}")

    elif name == "reject_layout" or name == "reject":
        shot = resolve_shot(
            shots,
            shot_id=args.get("shot_id"),
            shot_index=args.get("shot_index") or args.get("index"),
            title=args.get("title"),
        )
        if not shot and len(shots) == 1:
            shot = shots[0]
        if not shot:
            notes.append("reject_layout: specify a shot")
            return True
        feedback = str(args.get("feedback") or "")
        s2 = apply_transition(
            shot,
            "reject_layout",
            feedback=feedback,
            status=ShotStatus.ref_frame_pending,
        )
        save_shot(s2)
        actions.append(f"reject:{s2.id}")
        notes.append(f"Rejected **{s2.title}**" + (f": {feedback}" if feedback else ""))
        touched.add(s2.id)


    return True
