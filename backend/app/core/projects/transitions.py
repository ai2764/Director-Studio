from __future__ import annotations

from .layouts import (
    LayoutReference,
    LayoutReviewStatus,
    mirror_legacy_layout_fields,
    replace_layout_reference,
    sync_selected_layout_refs,
)
from .models import PromptSections, RefRole, Shot, ShotRef, ShotStatus
from ..schemas import JobStatus

# Optional caller fields for approve_layout; gate outcomes always override.
_APPROVE_LAYOUT_ALLOW = frozenset({"feedback", "meta", "layout_asset_id"})
_REJECT_LAYOUT_STATUSES = frozenset(
    {
        ShotStatus.ref_frame_pending,
        ShotStatus.needs_review,
    }
)


def _next_free_picture_index(refs: list[ShotRef]) -> int:
    used = {r.picture_index for r in refs}
    for i in range(1, 10):
        if i not in used:
            return i
    raise ValueError("cannot add layout ref: more than 9 refs")


def ensure_layout_ref(shot: Shot) -> list[ShotRef]:
    """Ensure a layout_ref_frame ref exists without reordering other refs.

    H3 Picture numbers follow ``picture_index``; layout is not forced to Picture 1.
    An existing layout ref keeps its index and only updates asset/file key.
    """
    if not shot.layout_asset_id:
        raise ValueError("layout_asset_id is required to bind layout")

    refs = list(shot.refs or [])
    for i, ref in enumerate(refs):
        if ref.role == RefRole.layout_ref_frame:
            updates: dict[str, str] = {"asset_id": shot.layout_asset_id}
            if not ref.file_key:
                updates["file_key"] = "layout"
            refs[i] = ref.model_copy(update=updates)
            return refs

    refs.append(
        ShotRef(
            role=RefRole.layout_ref_frame,
            asset_id=shot.layout_asset_id,
            picture_index=_next_free_picture_index(refs),
            file_key="layout",
        )
    )
    return refs


def ensure_layout_ref_at_picture_1(shot: Shot) -> list[ShotRef]:
    """Backward-compatible alias; layout is no longer forced to Picture 1."""
    return ensure_layout_ref(shot)


def _layout_reference(shot: Shot, layout_ref_id: str) -> LayoutReference:
    target = next(
        (layout for layout in shot.layout_refs if layout.id == layout_ref_id),
        None,
    )
    if target is None:
        raise ValueError(f"LayoutReference not found: {layout_ref_id}")
    return target


def _compatibility_layout_reference(
    shot: Shot,
) -> LayoutReference:
    mirrored = mirror_legacy_layout_fields(shot)
    target = next(
        (
            layout
            for layout in shot.layout_refs
            if (
                mirrored.layout_asset_id
                and layout.asset_id == mirrored.layout_asset_id
            )
            or (
                mirrored.ref_frame_job_id
                and layout.job_id == mirrored.ref_frame_job_id
            )
        ),
        None,
    )
    if target is None:
        raise ValueError("LayoutReference not found for compatibility Layout")
    return target


def _approval_layout_reference(
    shot: Shot,
    requested_asset_id: str | None,
) -> LayoutReference:
    """Resolve a legacy approval to an existing Layout before fallback."""
    if requested_asset_id:
        matches = [
            layout
            for layout in shot.layout_refs
            if layout.asset_id == requested_asset_id
        ]
        if len(matches) > 1:
            ids = ", ".join(layout.id for layout in matches)
            raise ValueError(
                f"ambiguous Layout asset {requested_asset_id} matches "
                f"LayoutReferences: {ids}"
            )
        if matches:
            return matches[0]
    return _compatibility_layout_reference(shot)


def review_layout_reference(
    shot: Shot,
    layout_ref_id: str,
    status: LayoutReviewStatus | str,
    feedback: str,
    *,
    human_override: bool = False,
    feedback_source: str = "",
    feedback_quote: str = "",
) -> Shot:
    """Review exactly one Layout without mutating its siblings."""
    try:
        review_status = LayoutReviewStatus(status)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid Layout review status: {status}") from exc

    target = _layout_reference(shot, layout_ref_id)
    selected = target.selected_for_h3
    if review_status != LayoutReviewStatus.usable:
        selected = False

    meta = dict(shot.meta or {})
    overrides = dict(meta.get("layout_human_overrides") or {})
    if review_status == LayoutReviewStatus.usable_with_repair and human_override:
        overrides[layout_ref_id] = True
    else:
        overrides.pop(layout_ref_id, None)
    if overrides:
        meta["layout_human_overrides"] = overrides
    else:
        meta.pop("layout_human_overrides", None)

    replacement = target.model_copy(
        update={
            "review_status": review_status,
            "review_feedback": feedback or "",
            "feedback_source": feedback_source or "",
            "feedback_quote": feedback_quote or "",
            "selected_for_h3": selected,
        }
    )
    working = shot.model_copy(update={"meta": meta})
    return replace_layout_reference(working, replacement)


def select_layout_reference(
    shot: Shot,
    layout_ref_id: str,
    selected: bool,
) -> Shot:
    """Add or remove one reviewed LayoutReference from the active H3 set."""
    target = _layout_reference(shot, layout_ref_id)
    if selected:
        if target.review_status == LayoutReviewStatus.reject:
            raise ValueError("rejected Layout cannot be selected")
        if target.review_status == LayoutReviewStatus.usable_with_repair:
            overrides = dict(
                (shot.meta or {}).get("layout_human_overrides") or {}
            )
            if not bool(overrides.get(layout_ref_id)):
                raise ValueError(
                    "usable_with_repair Layout requires a recorded human override"
                )
        elif target.review_status != LayoutReviewStatus.usable:
            raise ValueError("Layout must be usable before it can be selected")
    replacements = [
        layout.model_copy(
            update={
                "selected_for_h3": (
                    True if selected and layout.id == target.id else (
                        False if layout.id == target.id else layout.selected_for_h3
                    )
                )
            }
        )
        for layout in shot.layout_refs
    ]
    working = shot.model_copy(update={"layout_refs": replacements})
    if selected:
        return mirror_legacy_layout_fields(
            working,
            compatibility_primary_layout_id=target.id,
        )
    if target.asset_id and target.asset_id == shot.layout_asset_id:
        return working.model_copy(
            update={
                "layout_asset_id": None,
                "layout_review_status": None,
                "ref_frame_job_id": None,
            }
        )
    return working


def apply_transition(shot: Shot, event: str, **payload) -> Shot:
    """Apply a named human-gate / lifecycle event. Raises ValueError on illegal moves."""
    event = (event or "").strip()

    if event == "approve_layout":
        requested_asset_id = str(payload.get("layout_asset_id") or "").strip() or None
        target = _approval_layout_reference(shot, requested_asset_id)
        allowed = {
            k: v
            for k, v in payload.items()
            if k in _APPROVE_LAYOUT_ALLOW and k != "layout_asset_id"
        }
        working = shot.model_copy(update=allowed) if allowed else shot
        asset_id = requested_asset_id or target.asset_id
        if not asset_id:
            raise ValueError("layout_asset_id is required to approve layout")
        if asset_id != target.asset_id:
            if len(working.layout_refs) != 1:
                raise ValueError(
                    "requested Layout asset does not match an existing "
                    f"LayoutReference: {asset_id}"
                )
            target = target.model_copy(update={"asset_id": asset_id})
            working = replace_layout_reference(working, target)
        working = review_layout_reference(
            working,
            target.id,
            LayoutReviewStatus.usable,
            str(payload.get("feedback") or ""),
        )
        working = select_layout_reference(working, target.id, True)
        working = mirror_legacy_layout_fields(
            working,
            compatibility_primary_layout_id=target.id,
        )
        refs = sync_selected_layout_refs(working).refs
        # Gate fields applied last so payload cannot clobber outcomes.
        return working.model_copy(
            update={
                "status": ShotStatus.needs_review,
                "refs": refs,
            }
        )

    if event == "reject_layout":
        # Stay reviewable or move to allow regen; mark rejected.
        next_status = payload.get("status", ShotStatus.ref_frame_pending)
        if isinstance(next_status, str):
            next_status = ShotStatus(next_status)
        if next_status not in _REJECT_LAYOUT_STATUSES:
            raise ValueError(
                "reject_layout status must be ref_frame_pending or needs_review, "
                f"got {next_status.value}"
            )
        target = _compatibility_layout_reference(shot)
        working = review_layout_reference(
            shot,
            target.id,
            LayoutReviewStatus.reject,
            str(payload.get("feedback") or ""),
        )
        updates: dict = {"status": next_status}
        if "feedback" in payload:
            updates["feedback"] = payload["feedback"]
        return working.model_copy(update=updates)

    if event == "approve_shot":
        # Layout / reference-frame is optional — may approve from review or pre-layout states.
        allowed = {
            ShotStatus.needs_review,
            ShotStatus.ref_frame_pending,
            ShotStatus.draft,
        }
        if shot.status not in allowed:
            raise ValueError(
                "approve_shot only allowed from "
                f"{', '.join(s.value for s in sorted(allowed, key=lambda s: s.value))}, "
                f"got {shot.status.value}"
            )
        return shot.model_copy(update={"status": ShotStatus.approved})

    if event == "skip_layout":
        # Mark ready for Gate 2 without a reference-frame (layout optional).
        if shot.status not in (
            ShotStatus.ref_frame_pending,
            ShotStatus.needs_review,
            ShotStatus.draft,
            ShotStatus.blocked,
        ):
            raise ValueError(
                f"skip_layout not allowed from {shot.status.value}"
            )
        return shot.model_copy(
            update={
                "status": ShotStatus.needs_review,
                # leave layout_asset_id as-is; do not invent approval
            }
        )

    if event == "submit_h3":
        assert_h3_submittable(shot)
        return shot.model_copy(
            update={"status": ShotStatus.queued, "blocked_reasons": []}
        )

    raise ValueError(f"unknown transition event: {event}")


def assert_h3_submittable(shot: Shot) -> None:
    """Raise ValueError if shot cannot be submitted as pure H3 Ref2AV.

    No separate "approve shot" gate: needs refs + complete six-section prompt.
    A generated layout/reference asset is only an H3 input when explicitly present
    in ``shot.refs``. ``layout_asset_id`` by itself is candidate metadata.
    """
    if shot.status in (ShotStatus.queued, ShotStatus.running):
        raise ValueError(f"shot already {shot.status.value}")

    if len(shot.refs) > 9:
        raise ValueError("H3 supports at most 9 image refs")

    if not shot.refs:
        raise ValueError("at least one image ref is required for H3 submit")

    indices = [r.picture_index for r in shot.refs]
    if len(indices) != len(set(indices)):
        raise ValueError("picture indices must be unique within a shot")
    if sorted(indices) != list(range(1, len(indices) + 1)):
        raise ValueError("picture indices must be contiguous from 1")

    layout_refs = [r for r in shot.refs if r.role == RefRole.layout_ref_frame]
    for bound in layout_refs:
        candidates = [
            layout
            for layout in shot.layout_refs
            if layout.asset_id == bound.asset_id
        ]
        if not candidates:
            continue
        if len(candidates) > 1:
            matching_ids = ", ".join(layout.id for layout in candidates)
            raise ValueError(
                f"ambiguous Layout asset {bound.asset_id} matches "
                f"LayoutReferences: {matching_ids}"
            )
        eligible = next(
            (
                layout
                for layout in candidates
                if layout.asset_id
                and layout.review_status != LayoutReviewStatus.reject
                and not layout.superseded_by
                and layout.job_status
                not in {
                    JobStatus.queued,
                    JobStatus.uploading,
                    JobStatus.running,
                    JobStatus.failed,
                    JobStatus.cancelled,
                }
            ),
            None,
        )
        if eligible is not None:
            continue
        rejected = next(
            (
                layout
                for layout in candidates
                if layout.review_status == LayoutReviewStatus.reject
            ),
            None,
        )
        ineligible = rejected or candidates[0]
        status = (
            ineligible.review_status.value
            if ineligible.review_status is not None
            else "unreviewed"
        )
        raise ValueError(
            f"LayoutReference {ineligible.id} (asset {bound.asset_id}) "
            f"is {status} and cannot be used for H3"
        )

    _assert_prompt_complete(shot.prompt_sections)


def _assert_prompt_complete(sections: PromptSections) -> None:
    fields = [
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "overall_soundscape",
        "non_diegetic_music",
    ]
    empty = [name for name in fields if not (getattr(sections, name) or "").strip()]
    if empty:
        raise ValueError(f"prompt sections must be non-empty: {', '.join(empty)}")
