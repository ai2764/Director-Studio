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


@dataclass(frozen=True)
class ResolvedH3Profile:
    """An immutable, verified graph ready for a single H3 job to snapshot."""

    profile_id: str
    workflow: dict[str, Any]
    mapping: H3BoundaryMapping
    workflow_sha256: str
    source: Literal["builtin", "custom"]
    warning: ProfileWarning | None = None
