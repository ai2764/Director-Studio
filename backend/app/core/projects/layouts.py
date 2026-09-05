"""Layout models and compatibility helpers for multi-layout shots."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..schemas import JobStatus

if TYPE_CHECKING:
    from .models import Shot


class RefRole(str, Enum):
    layout_ref_frame = "layout_ref_frame"
    actor = "actor"
    costume = "costume"
    scene = "scene"
    prop = "prop"
    other = "other"


class LayoutReviewStatus(str, Enum):
    pending_review = "pending_review"
    usable = "usable"
    usable_with_repair = "usable_with_repair"
    reject = "reject"


class LayoutProvider(str, Enum):
    comfy = "comfy"
    gpt = "gpt"


class LayoutSourceRef(BaseModel):
    role: RefRole
    asset_id: str
    file_key: str | None = None
    notes: str = ""


class ClipTailFrameOrigin(BaseModel):
    kind: Literal["clip_tail_frame"] = "clip_tail_frame"
    source_shot_id: str
    source_job_id: str
    source_generation: int = Field(ge=1)
    output_kind: Literal["enhanced", "raw"]
    output_key: Literal["video", "video_raw"]
    source_filename: str
    source_duration_s: float | None = None
    extracted_timestamp_s: float | None = None


class LayoutReference(BaseModel):
    id: str
    provider: LayoutProvider = LayoutProvider.comfy
    asset_id: str | None = None
    job_id: str | None = None
    job_status: JobStatus | None = None
    job_error: str = ""
    purpose: str = "primary composition"
    state_description: str = ""
    time_hint: str = ""
    source_refs: list[LayoutSourceRef] = Field(default_factory=list)
    review_status: LayoutReviewStatus | None = None
    review_feedback: str = ""
    feedback_source: str = ""
    feedback_quote: str = ""
    revision_of: str | None = None
    superseded_by: str | None = None
    selected_for_h3: bool = False
    activation_mode: Literal["replace", "append"] = "replace"
    created_at: str = ""
    origin: ClipTailFrameOrigin | None = None

    @model_validator(mode="after")
    def _limit_source_images(self) -> "LayoutReference":
        if self.provider == LayoutProvider.comfy and len(self.source_refs) > 3:
            raise ValueError("a Comfy Layout accepts at most 3 source images")
        return self


class LayoutBrief(BaseModel):
    purpose: str = "primary composition"
    state_description: str = ""
    time_hint: str = ""
    source_refs: list[LayoutSourceRef] = Field(default_factory=list)
    activation_mode: Literal["replace", "append"] = "replace"

    @model_validator(mode="after")
    def _limit_explicit_sources(self) -> "LayoutBrief":
        if len(self.source_refs) > 3:
            raise ValueError("a Layout accepts at most 3 source images")
        return self


class GptLayoutBrief(BaseModel):
    purpose: str = "primary composition"
    state_description: str = ""
    time_hint: str = ""
    source_refs: list[LayoutSourceRef]
    generation_prompt: str = Field(min_length=1)
    activation_mode: Literal["replace", "append"] = "replace"


def legacy_review_to_layout(value: str | None) -> str | None:
    return {
        "approved": LayoutReviewStatus.usable.value,
        "rejected": LayoutReviewStatus.reject.value,
        "pending_review": LayoutReviewStatus.pending_review.value,
    }.get(value)


def layout_review_to_legacy(value: str | None) -> str | None:
    return {
        LayoutReviewStatus.pending_review.value: "pending_review",
        LayoutReviewStatus.usable.value: "approved",
        LayoutReviewStatus.usable_with_repair.value: "approved",
        LayoutReviewStatus.reject.value: "rejected",
    }.get(value, value)


def mirror_legacy_layout_fields(
    shot: Shot,
    *,
    compatibility_primary_layout_id: str | None = None,
) -> Shot:
    """Return a shot copy with its legacy layout fields derived from Layouts."""
    layout_refs = list(shot.layout_refs)
    if not layout_refs:
        return shot.model_copy()

    compatibility_primary = None
    if compatibility_primary_layout_id:
        compatibility_primary = next(
            (
                layout
                for layout in layout_refs
                if layout.id == compatibility_primary_layout_id
            ),
            None,
        )
        if compatibility_primary is None:
            raise ValueError(
                "LayoutReference not found: "
                f"{compatibility_primary_layout_id}"
            )
    human_overrides = dict(
        (shot.meta or {}).get("layout_human_overrides") or {}
    )
    selected_usable = next(
        (
            layout
            for layout in layout_refs
            if layout.selected_for_h3
            and (
                layout.review_status == LayoutReviewStatus.usable
                or (
                    layout.review_status
                    == LayoutReviewStatus.usable_with_repair
                    and bool(human_overrides.get(layout.id))
                )
            )
        ),
        None,
    )
    layout = compatibility_primary or selected_usable or layout_refs[0]
    return shot.model_copy(
        update={
            "layout_asset_id": layout.asset_id,
            "ref_frame_job_id": layout.job_id,
            "layout_review_status": layout_review_to_legacy(
                layout.review_status.value if layout.review_status else None
            ),
        }
    )


def replace_layout_reference(
    shot: Shot,
    replacement: LayoutReference,
) -> Shot:
    """Replace one LayoutReference immutably and refresh legacy projection."""
    found = False
    refs: list[LayoutReference] = []
    for current in shot.layout_refs:
        if current.id == replacement.id:
            refs.append(replacement)
            found = True
        else:
            refs.append(current)
    if not found:
        raise ValueError(f"LayoutReference not found: {replacement.id}")
    return mirror_legacy_layout_fields(shot.model_copy(update={"layout_refs": refs}))


def _selected_layouts_for_h3(shot: Shot) -> list[LayoutReference]:
    unavailable_jobs = {
        JobStatus.queued,
        JobStatus.uploading,
        JobStatus.running,
        JobStatus.failed,
        JobStatus.cancelled,
    }
    available = [
        layout
        for layout in shot.layout_refs
        if layout.asset_id
        and layout.review_status != LayoutReviewStatus.reject
        and not layout.superseded_by
        and layout.job_status not in unavailable_jobs
    ]

    for layout in available:
        matches = [
            candidate
            for candidate in shot.layout_refs
            if candidate.asset_id == layout.asset_id
        ]
        if len(matches) > 1:
            ids = ", ".join(candidate.id for candidate in matches)
            raise ValueError(
                f"ambiguous Layout asset {layout.asset_id} matches "
                f"LayoutReferences: {ids}"
            )

    if not available:
        return []

    explicitly_active = [
        layout for layout in available if layout.selected_for_h3
    ]
    if explicitly_active:
        return explicitly_active

    current = next(
        (
            layout
            for layout in available
            if layout.asset_id == shot.layout_asset_id
        ),
        None,
    )
    if current is None:
        return []
    return [current]


def sync_selected_layout_refs(shot: Shot) -> Shot:
    """Rebuild H3 Pictures from the Shot's explicitly active Layout set.

    Replacement generation creates a one-Layout active set. Explicit append
    generation keeps compatible active Layouts and adds another state. Prior
    alternatives remain as history. Rejected, superseded, incomplete, or
    active/failed generations are omitted.
    """
    from .models import ShotRef

    selected = _selected_layouts_for_h3(shot)
    active_ids = {layout.id for layout in selected}
    normalized_layouts = [
        layout.model_copy(
            update={"selected_for_h3": layout.id in active_ids}
        )
        for layout in shot.layout_refs
    ]
    working = shot.model_copy(update={"layout_refs": normalized_layouts})
    if active_ids:
        primary = next(
            (
                layout
                for layout in selected
                if layout.asset_id == shot.layout_asset_id
            ),
            selected[-1],
        )
        working = mirror_legacy_layout_fields(
            working,
            compatibility_primary_layout_id=primary.id,
        )
        selected = _selected_layouts_for_h3(working)
    elif shot.layout_asset_id and any(
        layout.asset_id == shot.layout_asset_id for layout in shot.layout_refs
    ):
        working = working.model_copy(
            update={
                "layout_asset_id": None,
                "layout_review_status": None,
                "ref_frame_job_id": None,
            }
        )
    selected_by_asset = {str(layout.asset_id): layout for layout in selected}
    non_layout_refs = sorted(
        (ref for ref in working.refs if ref.role != RefRole.layout_ref_frame),
        key=lambda ref: ref.picture_index,
    )
    current_layout_refs = sorted(
        (ref for ref in working.refs if ref.role == RefRole.layout_ref_frame),
        key=lambda ref: ref.picture_index,
    )

    ordered_layouts: list[tuple[LayoutReference, ShotRef | None]] = []
    retained_assets: set[str] = set()
    for ref in current_layout_refs:
        layout = selected_by_asset.get(ref.asset_id)
        if layout is None:
            continue
        if ref.asset_id in retained_assets:
            raise ValueError(
                f"ambiguous Layout Picture association for asset {ref.asset_id}"
            )
        retained_assets.add(ref.asset_id)
        ordered_layouts.append((layout, ref))
    for layout in selected:
        asset_id = str(layout.asset_id)
        if asset_id not in retained_assets:
            retained_assets.add(asset_id)
            ordered_layouts.append((layout, None))

    proposed_count = len(non_layout_refs) + len(ordered_layouts)
    if proposed_count > 9:
        inventory = [
            f"{ref.asset_id} ({ref.role.value}: {ref.notes or 'reference'})"
            for ref in non_layout_refs
        ]
        inventory.extend(
            f"{layout.asset_id} (Layout: {layout.purpose})"
            for layout, _existing in ordered_layouts
        )
        raise ValueError(
            "H3 supports at most 9 Picture references; proposed inventory: "
            + "; ".join(inventory)
        )

    rebuilt: list[ShotRef] = list(non_layout_refs)
    for layout, existing in ordered_layouts:
        if existing is not None:
            rebuilt.append(
                existing.model_copy(
                    update={
                        "asset_id": str(layout.asset_id),
                        "file_key": existing.file_key or "layout",
                    }
                )
            )
        else:
            rebuilt.append(
                ShotRef(
                    role=RefRole.layout_ref_frame,
                    asset_id=str(layout.asset_id),
                    picture_index=1,
                    file_key="layout",
                    notes=layout.purpose,
                )
            )

    packed = [
        ref.model_copy(update={"picture_index": index})
        for index, ref in enumerate(rebuilt, start=1)
    ]
    return working.model_copy(update={"refs": packed})


def selected_layout_prompt_context(shot: Shot) -> list[dict[str, Any]]:
    """Return active Layouts grounded to their actual Picture indices."""
    packed = sync_selected_layout_refs(shot)
    selected = _selected_layouts_for_h3(packed)
    by_asset = {str(layout.asset_id): layout for layout in selected}
    context: list[dict[str, Any]] = []
    for ref in packed.refs:
        if ref.role != RefRole.layout_ref_frame:
            continue
        layout = by_asset[ref.asset_id]
        context.append(
            {
                "asset_id": ref.asset_id,
                "picture_index": ref.picture_index,
                "purpose": layout.purpose,
                "state_description": layout.state_description,
                "time_hint": layout.time_hint,
                "origin_kind": layout.origin.kind if layout.origin else "",
                "visible_transition_required": bool(
                    layout.origin and layout.origin.kind == "clip_tail_frame"
                ),
            }
        )
    return context


def layout_prompt_signature(shot: Shot) -> str:
    """Hash the ordered Layout grounding that an H3 prompt must describe."""
    packed = sync_selected_layout_refs(shot)
    status_by_asset = {
        str(layout.asset_id): (
            layout.review_status.value if layout.review_status is not None else ""
        )
        for layout in _selected_layouts_for_h3(packed)
    }
    payload = [
        (
            item["asset_id"],
            item["picture_index"],
            item["purpose"],
            item["state_description"],
            item["time_hint"],
            item["origin_kind"],
            item["visible_transition_required"],
            status_by_asset[item["asset_id"]],
        )
        for item in selected_layout_prompt_context(packed)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
