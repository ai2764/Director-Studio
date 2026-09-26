"""Backend-owned, versioned task evidence. No persisted agent state."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Trust = Literal["user", "authored", "derived", "execution"]


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    version: str
    trust: Trust
    payload: dict


class TaskSnapshot(BaseModel):
    project_id: str
    project: dict
    shots: dict[str, dict]
    sources: dict[str, SourceRecord]


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["overview", "shot_prompt"] = "overview"
    target_shot_id: str | None = None
    objective: str = ""


class TaskPacket(BaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    task: TaskRequest
    authority: dict
    source_versions: dict[str, str]
    facts: dict
    available_context: list[dict] = Field(default_factory=list)
    omitted: list[dict] = Field(default_factory=list)
    missing: list[dict] = Field(default_factory=list)
    complete: bool
