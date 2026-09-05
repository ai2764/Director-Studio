"""Versioned data contracts for H3 Ref2AV workflow profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .errors import ProfileWarning


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class H3BoundaryMapping(_StrictModel):
    """The application-owned inputs on an otherwise workflow-owned graph."""

    h3_node_id: StrictStr = Field(min_length=1)
    prompt_input: StrictStr = Field(min_length=1)
    width_input: StrictStr = Field(min_length=1)
    height_input: StrictStr = Field(min_length=1)
    frames_input: StrictStr = Field(min_length=1)
    picture_input_pattern: StrictStr = Field(min_length=1)
    audio_input_pattern: StrictStr | None = None
    seed_node_id: StrictStr = Field(min_length=1)
    seed_input: StrictStr = Field(min_length=1)
    saver_node_id: StrictStr = Field(min_length=1)
    output_prefix_input: StrictStr = Field(min_length=1)
    output_fields: tuple[StrictStr, ...] = ("videos",)


class H3WorkflowProfile(_StrictModel):
    """A persisted profile metadata record, separate from workflow bytes."""

    id: StrictStr = Field(pattern=r"[a-z0-9][a-z0-9-]{0,63}")
    kind: Literal["h3_ref2av"] = "h3_ref2av"
    contract_version: Literal[1] = 1
    workflow_sha256: StrictStr = Field(pattern=r"[0-9a-f]{64}")
    mapping: H3BoundaryMapping
    status: Literal["draft", "mapped", "validated", "tested", "active", "broken"]


class H3AnalysisIssue(_StrictModel):
    """One deterministic reason a workflow cannot be mapped automatically."""

    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    node_id: StrictStr | None = None
    input_name: StrictStr | None = None


class H3NodeCandidate(_StrictModel):
    """A graph node eligible for one application-owned boundary role."""

    node_id: StrictStr = Field(min_length=1)
    class_type: StrictStr = Field(min_length=1)
    title: StrictStr = ""


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
    seed_candidates: tuple[H3NodeCandidate, ...] = ()
    saver_candidates: tuple[H3NodeCandidate, ...] = ()
    fixed_dependencies: tuple[H3FixedDependency, ...] = ()
    issues: tuple[H3AnalysisIssue, ...] = ()
    agent_manifest: dict[str, Any] = Field(default_factory=lambda: {"nodes": []})


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
