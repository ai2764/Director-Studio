from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class JobStatus(str, Enum):
    queued = "queued"
    uploading = "uploading"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class OutputSlot(BaseModel):
    key: str
    label: str
    path: str | None = None
    filename: str | None = None
    url: str | None = None


class ComfyImageRef(BaseModel):
    filename: str
    subfolder: str = ""
    type: str = "output"


class JobRecord(BaseModel):
    """Generic generation job. Pipeline-specific fields live in `params`."""

    id: str
    pipeline_id: str = "actor"
    asset_kind: str = "actors"
    status: JobStatus
    name: str
    notes: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    fixed_seed: bool = False
    error: str | None = None
    comfy_prompt_id: str | None = None
    external_task_id: str | None = None
    created_at: str
    updated_at: str
    outputs: dict[str, OutputSlot] = Field(default_factory=dict)
    input_previews: dict[str, str] = Field(default_factory=dict)
    library_asset_id: str | None = None
    # Owning project for assets / production outputs (None = unassigned / legacy)
    project_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_actor_job(cls, data: Any) -> Any:
        """Accept v0.1 flat actor job.json written before pipeline refactor."""
        if not isinstance(data, dict):
            return data
        if "params" not in data and "mode" in data:
            data = dict(data)
            data.setdefault("pipeline_id", "actor")
            data.setdefault("asset_kind", "actors")
            data["params"] = {
                "mode": data.pop("mode", None),
                "description": data.pop("description", "") or "",
                "negative_prompt": data.pop("negative_prompt", "") or "",
                "extract_outfit": data.pop("extract_outfit", False) or False,
            }
            if "actor_id" in data and not data.get("library_asset_id"):
                data["library_asset_id"] = data.pop("actor_id")
            elif "actor_id" in data:
                data.pop("actor_id", None)
            outs = data.get("outputs")
            if isinstance(outs, dict) and outs and not _looks_like_slot_map(outs):
                # JobOutputs shape: {master: {key,label,...}|null, ...}
                flat: dict[str, Any] = {}
                for k, v in outs.items():
                    if isinstance(v, dict) and v.get("key"):
                        flat[k] = v
                    elif isinstance(v, dict) and v.get("url"):
                        flat[k] = {**v, "key": k, "label": v.get("label") or k}
                data["outputs"] = flat
        return data


def _looks_like_slot_map(outs: dict) -> bool:
    """True if values are OutputSlot-like with consistent keys."""
    for v in outs.values():
        if v is None:
            continue
        if isinstance(v, dict) and "key" in v:
            return True
        return False
    return True


class LibraryAsset(BaseModel):
    """Generic library entry. Kind-specific payload in `meta` + `files`."""

    id: str
    kind: str  # actors | costumes | scenes | props | ...
    name: str
    notes: str = ""
    pipeline_id: str
    job_id: str
    seed: int | None = None
    created_at: str
    files: dict[str, str | None] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    urls: dict[str, str] = Field(default_factory=dict)
    # Project ownership — assets belong to a production project when set
    project_id: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    comfy_reachable: bool
    comfy_error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PipelineInfo(BaseModel):
    id: str
    asset_kind: str
    display_name: str
    description: str = ""
    enabled: bool = True
