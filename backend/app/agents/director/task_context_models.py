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


class ContextRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str
    expected_version: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=8000)


class ContextPage(BaseModel):
    source_key: str
    version: str
    trust: Trust
    format: Literal["text", "json"]
    offset: int
    next_offset: int | None
    total_chars: int
    text: str
    truncated: bool


class TaskContextState(BaseModel):
    project_id: str
    request: TaskRequest
    retrieved_versions: dict[str, str]
    context_epoch: int = 0
    read_pages: set[tuple[str, str, int, int]] = Field(default_factory=set)
    writer_receipts: dict[str, dict] = Field(default_factory=dict)
    source_keys: list[str] = Field(default_factory=list)
