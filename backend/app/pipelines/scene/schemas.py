from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...core.schemas import JobRecord, JobStatus, LibraryAsset, OutputSlot


class SceneJobResponse(BaseModel):
    id: str
    status: JobStatus
    name: str
    notes: str = ""
    angle_prompts: str = ""
    prepend_text: str = ""
    append_text: str = ""
    used_angles: list[str] = Field(default_factory=list)
    output_stems: list[str] = Field(default_factory=list)
    seed: int | None = None
    fixed_seed: bool = False
    error: str | None = None
    comfy_prompt_id: str | None = None
    created_at: str
    updated_at: str
    outputs: dict[str, OutputSlot] = Field(default_factory=dict)
    output_order: list[str] = Field(default_factory=list)
    input_previews: dict[str, str] = Field(default_factory=dict)
    scene_id: str | None = None
    pipeline_id: str = "scene"

    @classmethod
    def from_job(cls, job: JobRecord, labels: dict[str, str] | None = None) -> SceneJobResponse:
        p = job.params or {}
        outs = dict(job.outputs or {})
        # ensure labels on slots
        if labels:
            for k, slot in outs.items():
                if slot and labels.get(k):
                    outs[k] = slot.model_copy(update={"label": labels[k]})
        stems = p.get("output_stems") or []
        if not isinstance(stems, list):
            stems = []
        stems = [str(x) for x in stems]
        # Prefer generation order (stems), else stable sort of keys
        if stems:
            order = [s for s in stems if s in outs] + [k for k in outs if k not in stems]
        else:
            order = sorted(outs.keys())
        used = p.get("used_angles") or []
        if not isinstance(used, list):
            used = []
        return cls(
            id=job.id,
            status=job.status,
            name=job.name,
            notes=job.notes,
            angle_prompts=p.get("angle_prompts") or "",
            prepend_text=p.get("prepend_text") or "",
            append_text=p.get("append_text") or "",
            used_angles=[str(x) for x in used],
            output_stems=stems,
            seed=job.seed,
            fixed_seed=job.fixed_seed,
            error=job.error,
            comfy_prompt_id=job.comfy_prompt_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            outputs=outs,
            output_order=order,
            input_previews=job.input_previews,
            scene_id=job.library_asset_id,
            pipeline_id=job.pipeline_id,
        )


class SaveSceneRequest(BaseModel):
    name: str | None = None
    notes: str | None = None
    project_id: str | None = None


class SceneRecord(BaseModel):
    id: str
    name: str
    notes: str = ""
    angle_prompts: str = ""
    used_angles: list[str] = Field(default_factory=list)
    seed: int | None = None
    job_id: str
    created_at: str
    files: dict[str, str | None] = Field(default_factory=dict)
    urls: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None

    @classmethod
    def from_library(cls, asset: LibraryAsset) -> SceneRecord:
        meta = asset.meta or {}
        used = meta.get("used_angles") or []
        if not isinstance(used, list):
            used = []
        return cls(
            id=asset.id,
            name=asset.name,
            notes=asset.notes,
            angle_prompts=meta.get("angle_prompts") or "",
            used_angles=[str(x) for x in used],
            seed=asset.seed,
            job_id=asset.job_id,
            created_at=asset.created_at,
            files=dict(asset.files or {}),
            urls=dict(asset.urls or {}),
            meta=meta,
            project_id=asset.project_id,
        )


class SceneListResponse(BaseModel):
    items: list[SceneRecord]
