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


class RunPlan(BaseModel):
    steps: list[RunStep] = Field(min_length=1)


class ManagedRun(BaseModel):
    run_id: str
    project_id: str
    steps: list[RunStep]
    plan_fingerprint: str
    current_fingerprint: str = ""
    state: RunState = "draft"
    resolution_preset: str | None = None
    current_index: int = 0
    current_job_id: str | None = None
    completed_job_ids: dict[str, str] = Field(default_factory=dict)
    pending_event_id: str | None = None
    prepared_tail_layout_ids: dict[str, str] = Field(default_factory=dict)
    processed_event_ids: list[str] = Field(default_factory=list)
    paused_reason: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
