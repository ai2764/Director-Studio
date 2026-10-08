"""Versioned data contracts for H3 Ref2AV workflow profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_serializer, model_validator

from .errors import ProfileWarning


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class H3InputMapping(_StrictModel):
    """Application-owned inputs on an otherwise workflow-owned graph."""

    h3_node_id: StrictStr = Field(min_length=1)
    prompt_input: StrictStr = Field(min_length=1)
    width_input: StrictStr = Field(min_length=1)
    height_input: StrictStr = Field(min_length=1)
    frames_input: StrictStr = Field(min_length=1)
    picture_input_pattern: StrictStr = Field(min_length=1)
    audio_input_pattern: StrictStr | None = None
    seed_node_id: StrictStr | None = None
    seed_input: StrictStr | None = None

    @model_validator(mode="after")
    def validate_seed_pair(self) -> H3InputMapping:
        if (self.seed_node_id is None) != (self.seed_input is None):
            raise ValueError("seed_node_id and seed_input must be set together")
        return self


class H3OutputSelection(_StrictModel):
    """The confirmed terminal node and optional observed artifact choice."""

    node_id: StrictStr = Field(min_length=1)
    artifact_index: int | None = Field(default=None, ge=0)


class H3ContextVideoInput(_StrictModel):
    """Optional file input that receives one uploaded context video."""

    node_id: StrictStr = Field(min_length=1)
    input_name: StrictStr = Field(min_length=1)


class H3BoundaryMapping(_StrictModel):
    """The complete Director Studio boundary around an opaque H3 graph."""

    inputs: H3InputMapping
    output: H3OutputSelection
    context_video: H3ContextVideoInput | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_context_video(self, handler):
        payload = handler(self)
        if isinstance(payload, dict) and payload.get("context_video") is None:
            payload.pop("context_video", None)
        return payload


def contract_version_for(mapping: H3BoundaryMapping) -> Literal[2, 3]:
    """v2 mappings stay v2. A context-video file mapping uses the v3 contract."""
    return 3 if mapping.context_video is not None else 2


class H3WorkflowProfile(_StrictModel):
    """A persisted profile metadata record, separate from workflow bytes."""

    id: StrictStr = Field(pattern=r"[a-z0-9][a-z0-9-]{0,63}")
    kind: Literal["h3_ref2av"] = "h3_ref2av"
    contract_version: Literal[2, 3] = 2
    workflow_sha256: StrictStr = Field(pattern=r"[0-9a-f]{64}")
    mapping: H3BoundaryMapping
    status: Literal["draft", "mapped", "validated", "tested", "active", "broken"]


class H3AnalysisIssue(_StrictModel):
    """One deterministic reason a workflow cannot be mapped automatically."""

    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    node_id: StrictStr | None = None
    node_name: StrictStr | None = None
    input_name: StrictStr | None = None


class H3NodeCandidate(_StrictModel):
    """A graph node eligible for one application-owned boundary role."""

    node_id: StrictStr = Field(min_length=1)
    class_type: StrictStr = Field(min_length=1)
    title: StrictStr = ""
    object_display_name: StrictStr = ""
    display_name: StrictStr = Field(min_length=1)
    terminal: bool = False
    output_node: bool = False
    output_types: tuple[StrictStr, ...] = ()


class H3ContextVideoCandidate(_StrictModel):
    """A reachable video-file input the operator can map as context."""

    node_id: StrictStr = Field(min_length=1)
    class_type: StrictStr = Field(min_length=1)
    input_name: StrictStr = Field(min_length=1)
    display_name: StrictStr = Field(min_length=1)


class H3FixedDependency(_StrictModel):
    """A reachable workflow-owned local file input disclosed during inspection."""

    node_id: StrictStr = Field(min_length=1)
    class_type: Literal["LoadImage", "LoadAudio"]
    input_name: StrictStr = Field(min_length=1)
    value: StrictStr = Field(min_length=1)


class H3WorkflowAnalysis(_StrictModel):
    """Bounded deterministic compatibility result safe to expose to setup tools."""

    compatibility: Literal["auto_compatible", "needs_confirmation", "unsupported"]
    mapping: H3BoundaryMapping | None = None
    output_candidates: tuple[H3NodeCandidate, ...] = ()
    h3_candidates: tuple[H3NodeCandidate, ...] = ()
    seed_candidates: tuple[H3NodeCandidate, ...] = ()
    context_video_candidates: tuple[H3ContextVideoCandidate, ...] = ()
    fixed_dependencies: tuple[H3FixedDependency, ...] = ()
    issues: tuple[H3AnalysisIssue, ...] = ()


class H3ValidationIssue(_StrictModel):
    """One exact contract failure tied to its graph location where possible."""

    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    node_id: StrictStr | None = None
    input_name: StrictStr | None = None


class ValidationReport(_StrictModel):
    """Result of structural, mapping, and synthetic-fill contract validation."""

    valid: bool
    issues: tuple[H3ValidationIssue, ...] = ()
    fixed_dependencies: tuple[H3FixedDependency, ...] = ()
    synthetic_boundary: dict[str, int] = Field(
        default_factory=lambda: {
            "pictures": 1,
            "audios": 0,
            "width": 864,
            "height": 480,
            "frames": 56,
            "seed": 42,
        }
    )


@dataclass(frozen=True)
class ResolvedH3Profile:
    """An immutable, verified graph ready for a single H3 job to snapshot."""

    profile_id: str
    workflow: dict[str, Any]
    mapping: H3BoundaryMapping
    workflow_sha256: str
    source: Literal["builtin", "custom"]
    warning: ProfileWarning | None = None
    display_name: str = "Custom H3 workflow"
    validated_at: str | None = None
