from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...core.schemas import JobRecord, JobStatus, OutputSlot


class H3Ref2VaJobResponse(BaseModel):
    id: str
    status: JobStatus
    name: str
    notes: str = ""
    prompt: str = ""
    dialogue: list[str] = Field(default_factory=list)
    frames: int | None = None
    width: int | None = None
    height: int | None = None
    image_keys: list[str] = Field(default_factory=list)
    seed: int | None = None
    fixed_seed: bool = False
    error: str | None = None
    comfy_prompt_id: str | None = None
    external_task_id: str | None = None
    created_at: str
    updated_at: str
    outputs: dict[str, OutputSlot] = Field(default_factory=dict)
    input_previews: dict[str, str] = Field(default_factory=dict)
    pipeline_id: str = "h3_ref2va"
    project_id: str | None = None
    json_shot_id: str | None = None
    json_storyboard_revision: int | None = None
    h3_provider: str = "local"
    h3_profile_id: str | None = None
    h3_profile_sha256: str | None = None
    h3_contract_version: int | None = None

    @classmethod
    def from_job(cls, job: JobRecord) -> H3Ref2VaJobResponse:
        p = job.params or {}
        dialogue = p.get("dialogue") or []
        if not isinstance(dialogue, list):
            dialogue = []
        image_keys = p.get("image_keys") or []
        if not isinstance(image_keys, list):
            image_keys = []
        frames = p.get("frames")
        try:
            frames_i = int(frames) if frames is not None else None
        except (TypeError, ValueError):
            frames_i = None
        revision = p.get("json_storyboard_revision")
        try:
            revision_i = int(revision) if revision is not None else None
        except (TypeError, ValueError):
            revision_i = None
        return cls(
            id=job.id,
            status=job.status,
            name=job.name,
            notes=job.notes,
            prompt=p.get("prompt") or "",
            dialogue=[str(x) for x in dialogue],
            frames=frames_i,
            width=p.get("width"),
            height=p.get("height"),
            image_keys=[str(x) for x in image_keys],
            seed=job.seed,
            fixed_seed=job.fixed_seed,
            error=job.error,
            comfy_prompt_id=job.comfy_prompt_id,
            external_task_id=job.external_task_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            outputs=dict(job.outputs or {}),
            input_previews=job.input_previews,
            pipeline_id=job.pipeline_id,
            project_id=job.project_id or p.get("project_id") or None,
            json_shot_id=p.get("json_shot_id") or None,
            json_storyboard_revision=revision_i,
            h3_provider=str(p.get("h3_provider") or "local"),
            h3_profile_id=p.get("h3_profile_id") or None,
            h3_profile_sha256=p.get("h3_profile_sha256") or None,
            h3_contract_version=p.get("h3_contract_version"),
        )


class H3Ref2VaMeta(BaseModel):
    id: str = "h3_ref2va"
    asset_kind: str = "productions"
    display_name: str = "H3 Ref2AV"
    extras: dict[str, Any] = Field(default_factory=dict)
