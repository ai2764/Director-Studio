from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from ...core.schemas import JobRecord, JobStatus, LibraryAsset, OutputSlot
from .workflow import derive_mode


class ActorMode(str, Enum):
    """Derived display label from which optional images were used (not a Comfy toggle)."""

    text = "text"
    reference = "reference"
    wardrobe = "wardrobe"


class ActorJobOutputs(BaseModel):
    wardrobe_ref: OutputSlot | None = None
    master: OutputSlot | None = None
    bust_threeview: OutputSlot | None = None
    fullbody_threeview: OutputSlot | None = None
    asset_sheet: OutputSlot | None = None


class ActorJobResponse(BaseModel):
    id: str
    status: JobStatus
    mode: ActorMode
    name: str
    notes: str = ""
    description: str = ""
    body_description: str = ""
    hair_description: str = ""
    negative_prompt: str = ""
    has_actor_ref: bool = False
    has_wardrobe_ref: bool = False
    include_headwear: bool = False
    include_footwear: bool = False
    seed: int | None = None
    fixed_seed: bool = False
    error: str | None = None
    comfy_prompt_id: str | None = None
    created_at: str
    updated_at: str
    outputs: ActorJobOutputs = Field(default_factory=ActorJobOutputs)
    input_previews: dict[str, str] = Field(default_factory=dict)
    actor_id: str | None = None
    pipeline_id: str = "actor"

    @classmethod
    def from_job(cls, job: JobRecord) -> ActorJobResponse:
        p = job.params or {}
        # legacy: mode / extract_outfit jobs
        has_actor = bool(p.get("has_actor_ref"))
        has_wardrobe = bool(p.get("has_wardrobe_ref"))
        if "has_actor_ref" not in p and p.get("mode") in ("reference", "wardrobe"):
            has_actor = True
        if "has_wardrobe_ref" not in p and p.get("mode") == "wardrobe":
            has_wardrobe = True
        mode_str = p.get("mode") or derive_mode(
            has_actor_ref=has_actor, has_wardrobe_ref=has_wardrobe
        )
        try:
            mode = ActorMode(mode_str)
        except ValueError:
            mode = ActorMode.text
        outs = job.outputs or {}
        return cls(
            id=job.id,
            status=job.status,
            mode=mode,
            name=job.name,
            notes=job.notes,
            description=p.get("description") or "",
            body_description=p.get("body_description") or "",
            hair_description=p.get("hair_description") or "",
            negative_prompt=p.get("negative_prompt") or "",
            has_actor_ref=has_actor,
            has_wardrobe_ref=has_wardrobe,
            include_headwear=bool(p.get("include_headwear")),
            include_footwear=bool(p.get("include_footwear")),
            seed=job.seed,
            fixed_seed=job.fixed_seed,
            error=job.error,
            comfy_prompt_id=job.comfy_prompt_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            outputs=ActorJobOutputs(
                wardrobe_ref=outs.get("wardrobe_ref"),
                master=outs.get("master"),
                bust_threeview=outs.get("bust_threeview"),
                fullbody_threeview=outs.get("fullbody_threeview"),
                asset_sheet=outs.get("asset_sheet"),
            ),
            input_previews=job.input_previews,
            actor_id=job.library_asset_id,
            pipeline_id=job.pipeline_id,
        )


class SaveActorRequest(BaseModel):
    name: str | None = None
    notes: str | None = None
    project_id: str | None = None


class ActorAssetFiles(BaseModel):
    master: str | None = None
    bust_threeview: str | None = None
    fullbody_threeview: str | None = None
    asset_sheet: str | None = None
    wardrobe_ref: str | None = None
    input_actor_ref: str | None = None
    input_wardrobe_ref: str | None = None


class ActorRecord(BaseModel):
    id: str
    name: str
    notes: str = ""
    mode: ActorMode
    description: str = ""
    body_description: str = ""
    hair_description: str = ""
    has_actor_ref: bool = False
    has_wardrobe_ref: bool = False
    include_headwear: bool = False
    include_footwear: bool = False
    seed: int | None = None
    job_id: str
    created_at: str
    files: ActorAssetFiles
    urls: dict[str, str] = Field(default_factory=dict)
    project_id: str | None = None

    @classmethod
    def from_library(cls, asset: LibraryAsset) -> ActorRecord:
        meta = asset.meta or {}
        files_raw = asset.files or {}
        files = ActorAssetFiles(
            master=files_raw.get("master"),
            bust_threeview=files_raw.get("bust_threeview"),
            fullbody_threeview=files_raw.get("fullbody_threeview"),
            asset_sheet=files_raw.get("asset_sheet"),
            wardrobe_ref=files_raw.get("wardrobe_ref"),
            input_actor_ref=files_raw.get("input_actor") or files_raw.get("input_actor_ref"),
            input_wardrobe_ref=files_raw.get("input_wardrobe")
            or files_raw.get("input_wardrobe_ref"),
        )
        urls = dict(asset.urls)
        if "input_actor" in urls and "input_actor_ref" not in urls:
            urls["input_actor_ref"] = urls["input_actor"]
        if "input_wardrobe" in urls and "input_wardrobe_ref" not in urls:
            urls["input_wardrobe_ref"] = urls["input_wardrobe"]

        has_actor = bool(meta.get("has_actor_ref")) or bool(files.input_actor_ref)
        has_wardrobe = bool(meta.get("has_wardrobe_ref")) or bool(files.input_wardrobe_ref)
        mode_raw = meta.get("mode") or derive_mode(
            has_actor_ref=has_actor, has_wardrobe_ref=has_wardrobe
        )
        try:
            mode = ActorMode(mode_raw)
        except ValueError:
            mode = ActorMode.text

        return cls(
            id=asset.id,
            name=asset.name,
            notes=asset.notes,
            mode=mode,
            description=meta.get("description") or "",
            body_description=meta.get("body_description") or "",
            hair_description=meta.get("hair_description") or "",
            has_actor_ref=has_actor,
            has_wardrobe_ref=has_wardrobe,
            include_headwear=bool(meta.get("include_headwear")),
            include_footwear=bool(meta.get("include_footwear")),
            seed=asset.seed,
            job_id=asset.job_id,
            created_at=asset.created_at,
            files=files,
            urls=urls,
            project_id=asset.project_id,
        )


class ActorListResponse(BaseModel):
    items: list[ActorRecord]
