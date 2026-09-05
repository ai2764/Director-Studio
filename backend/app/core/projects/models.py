from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ShotStatus(str, Enum):
    draft = "draft"
    planning = "planning"
    ref_frame_pending = "ref_frame_pending"
    needs_review = "needs_review"
    approved = "approved"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"


from .layouts import (
    LayoutReference,
    LayoutReviewStatus,
    RefRole,
    legacy_review_to_layout,
)


class ShotRef(BaseModel):
    role: RefRole
    asset_id: str
    picture_index: int = Field(ge=1, le=9)
    notes: str = ""
    # Which file inside the library asset to feed H3 / reference-frame.
    # Actors default to fullbody_threeview when unset (see core.library.images).
    file_key: str | None = None


class ShotVoiceRef(BaseModel):
    asset_id: str
    audio_index: int = Field(ge=1, le=3)
    file_key: str = "reference"
    speaker: str = ""
    notes: str = ""

    @field_validator("asset_id", "file_key", "speaker", "notes")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def _require_identity(self) -> "ShotVoiceRef":
        if not self.asset_id:
            raise ValueError("voice asset_id is required")
        if not self.file_key:
            raise ValueError("voice file_key is required")
        return self


class AssetCoverageRecommendation(BaseModel):
    kind: Literal["actor", "scene", "prop", "costume", "layout", "other"]
    asset_id: str | None = None
    needed_variant: str
    reason: str
    shot_ids: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high"] = "medium"
    resolution: Literal["pending", "accepted", "dismissed", "generated"] = "pending"


class AssetCoverageReview(BaseModel):
    script_hash: str
    status: Literal["reviewed", "skipped"]
    recommendations: list[AssetCoverageRecommendation] = Field(default_factory=list)
    notes: str = ""


class AssetCoverageReviewSubmission(BaseModel):
    expected_script_hash: str
    status: Literal["reviewed", "skipped"]
    recommendations: list[AssetCoverageRecommendation] = Field(default_factory=list)
    notes: str = ""


def voice_ref_signature(refs: list[ShotVoiceRef]) -> str:
    payload = [
        (ref.asset_id, ref.audio_index, ref.file_key, ref.speaker)
        for ref in refs
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def picture_ref_signature(refs: list[ShotRef]) -> str:
    payload = [
        (ref.role.value, ref.asset_id, ref.picture_index, ref.file_key or "")
        for ref in sorted(refs, key=lambda item: item.picture_index)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


class PromptSections(BaseModel):
    subject_definitions: str = ""
    summary: str = ""
    retention_analysis: str = ""
    detailed_description: str = ""
    overall_soundscape: str = ""
    non_diegetic_music: str = ""

    def as_ordered_text(self) -> str:
        parts = [
            ("subject_definitions", self.subject_definitions),
            ("summary", self.summary),
            ("retention_analysis", self.retention_analysis),
            ("detailed_description", self.detailed_description),
            ("overall_soundscape", self.overall_soundscape),
            ("non_diegetic_music", self.non_diegetic_music),
        ]
        return "\n".join(f"{k}:\n{v}" for k, v in parts)


class Shot(BaseModel):
    id: str
    project_id: str
    scene_id: str
    title: str
    script_beat: str
    # Optional on persisted shots so projects created before structured camera
    # planning continue to load unchanged.
    shot_type: str = ""
    camera_angle: str = ""
    camera_motion: str = ""
    composition: str = ""
    duration_s: float
    status: ShotStatus = ShotStatus.draft
    refs: list[ShotRef] = Field(default_factory=list)
    voice_refs: list[ShotVoiceRef] = Field(default_factory=list)
    prompt_sections: PromptSections = Field(default_factory=PromptSections)
    dialogue: list[str] = Field(default_factory=list)
    layout_asset_id: str | None = None
    layout_review_status: str | None = None  # pending_review | approved | rejected
    ref_frame_job_id: str | None = None
    layout_refs: list[LayoutReference] = Field(default_factory=list)
    h3_job_id: str | None = None
    source_audio_path: str | None = None
    feedback: str = ""
    blocked_reasons: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _synthesize_legacy_layout_ref(cls, values: Any) -> Any:
        if not isinstance(values, dict) or "layout_refs" in values:
            return values

        layout_asset_id = values.get("layout_asset_id")
        ref_frame_job_id = values.get("ref_frame_job_id")
        if not (layout_asset_id or ref_frame_job_id):
            return values

        review_status = legacy_review_to_layout(values.get("layout_review_status"))
        values = dict(values)
        values["layout_refs"] = [
            LayoutReference(
                id=f"legacy_{layout_asset_id or ref_frame_job_id}",
                asset_id=layout_asset_id,
                job_id=ref_frame_job_id,
                review_status=review_status,
                selected_for_h3=review_status == LayoutReviewStatus.usable.value,
            )
        ]
        return values

    @model_validator(mode="after")
    def _validate_voice_refs(self) -> "Shot":
        refs = list(self.voice_refs)
        if len(refs) > 3:
            raise ValueError("H3 supports at most 3 Voice references")
        if len({ref.asset_id for ref in refs}) != len(refs):
            raise ValueError("Voice reference assets must be unique")
        indexes = [ref.audio_index for ref in refs]
        if indexes != list(range(1, len(refs) + 1)):
            raise ValueError("audio_index must be contiguous and ordered from 1")
        return self


class ProjectMode(str, Enum):
    director = "director"
    json_production = "json_production"


class Project(BaseModel):
    id: str
    name: str
    script_text: str
    script_locked: bool = False
    mode: ProjectMode = ProjectMode.director
    created_at: str
    updated_at: str
    shot_ids: list[str] = Field(default_factory=list)
    asset_coverage_review: AssetCoverageReview | None = None


class AgentContext(BaseModel):
    project_id: str
    script_hash: str
    last_phase: str = "planned"
    models_used: list[str] = Field(default_factory=list)
    shot_summaries: list[dict[str, Any]] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
