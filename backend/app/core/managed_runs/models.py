"""The user-reviewed execution plan and durable run state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


RunState = Literal["draft", "active", "stopping", "paused", "completed", "stopped"]


class RunStep(BaseModel):
    shot_id: str
    tail_from_shot_id: str | None = None
    tail_reason: str = ""


class TailHandoffDecision(BaseModel):
    target_shot_id: str = Field(
        description="The later Shot that should start from an extracted tail frame."
    )
    source_shot_id: str = Field(
        description="An earlier Shot whose final frame should seed the target Shot."
    )
    reason: str = Field(
        min_length=1,
        description="Concrete visual-continuity reason for this handoff.",
    )


class RunPlan(BaseModel):
    """Inference chooses dependencies only; unsolicited authoring fields are ignored."""

    tail_handoffs: list[TailHandoffDecision] = Field(
        default_factory=list,
        description=(
            "Only the Shot-to-Shot tail-frame handoffs that are visually necessary. "
            "Omit Shots that do not need a handoff."
        ),
    )


class ManagedRun(BaseModel):
    run_id: str
    project_id: str
    steps: list[RunStep]
    plan_fingerprint: str
    current_fingerprint: str = ""
    fingerprint_version: int = 2
    state: RunState = "draft"
    resolution_preset: str | None = None
    current_index: int = 0
    current_job_id: str | None = None
    completed_job_ids: dict[str, str] = Field(default_factory=dict)
    selected_shot_ids: list[str] = Field(default_factory=list)
    pending_shot_ids: list[str] = Field(default_factory=list)
    skipped_shots: dict[str, str] = Field(default_factory=dict)
    tail_source_job_ids: dict[str, str] = Field(default_factory=dict)
    pending_event_id: str | None = None
    prepared_tail_layout_ids: dict[str, str] = Field(default_factory=dict)
    processed_event_ids: list[str] = Field(default_factory=list)
    paused_reason: str = ""
    prompt_retry_count: int = 0
    prompt_retry_error: str = ""
    recovery_history: list[dict] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ManagedRunView(ManagedRun):
    is_stale: bool = False
    stale_reason: str = ""
