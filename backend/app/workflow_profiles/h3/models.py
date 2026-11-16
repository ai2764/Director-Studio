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


class H3NumericPort(H3ContextVideoInput):
    """A literal numeric input; result = application value * scale + offset."""

    scale: float = Field(default=1, allow_inf_nan=False)
    offset: float = Field(default=0, allow_inf_nan=False)


class H3DirectInputMapping(_StrictModel):
    """Agent-authored literal ports. Media lists are ordered, fixed-arity slots."""

    kind: Literal['direct'] = 'direct'
    prompt: tuple[H3ContextVideoInput, ...] = Field(min_length=1, max_length=32)
    width: tuple[H3NumericPort, ...] = Field(min_length=1, max_length=32)
    height: tuple[H3NumericPort, ...] = Field(min_length=1, max_length=32)
    frames: tuple[H3NumericPort, ...] = Field(min_length=1, max_length=32)
    seed: tuple[H3NumericPort, ...] = Field(default=(), max_length=32)
    pictures: tuple[H3ContextVideoInput, ...] = Field(min_length=1, max_length=9)
    audios: tuple[H3ContextVideoInput, ...] = Field(default=(), max_length=3)


class H3NativeLatentContext(_StrictModel):
    load_path: H3ContextVideoInput | None = None
    load_index: H3ContextVideoInput | None = None
    save_prefix: H3ContextVideoInput | None = None
    save_index: H3ContextVideoInput | None = None
    save_output_node_id: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_load_pair(self):
        if (self.load_path is None) != (self.load_index is None):
            raise ValueError("Native load_path and load_index must be set together")
        saves = (self.save_prefix, self.save_index, self.save_output_node_id)
        if any(value is not None for value in saves) and not all(value is not None for value in saves):
            raise ValueError('Native save_prefix, save_index and save_output_node_id must be set together')
        if self.load_path is None and self.save_prefix is None:
            raise ValueError('Native context requires a load pair or a complete save group')
        return self


class H3BoundaryMapping(_StrictModel):
    """The complete Director Studio boundary around an opaque H3 graph."""

    inputs: H3InputMapping | H3DirectInputMapping
    output: H3OutputSelection
    context_video: H3ContextVideoInput | None = None
    native_latent_context: H3NativeLatentContext | None = None

    @model_validator(mode="after")
    def validate_native_boundary(self):
        if self.native_latent_context is not None:
            if not isinstance(self.inputs, H3DirectInputMapping) or self.context_video is not None:
                raise ValueError("Native latent context requires direct inputs without context_video")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent_context_video(self, handler):
        payload = handler(self)
        if isinstance(payload, dict) and payload.get("context_video") is None:
            payload.pop("context_video", None)
        if isinstance(payload, dict) and payload.get("native_latent_context") is None:
            payload.pop("native_latent_context", None)
        return payload


def contract_version_for(mapping: H3BoundaryMapping) -> Literal[2, 3, 4]:
    """v2 mappings stay v2. A context-video file mapping uses the v3 contract."""
    if isinstance(mapping.inputs, H3DirectInputMapping):
        return 4
    return 3 if mapping.context_video is not None else 2


class H3WorkflowProfile(_StrictModel):
    """A persisted profile metadata record, separate from workflow bytes."""

    id: StrictStr = Field(pattern=r"[a-z0-9][a-z0-9-]{0,63}")
    kind: Literal["h3_ref2av"] = "h3_ref2av"
    contract_version: Literal[2, 3, 4] = 2
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
