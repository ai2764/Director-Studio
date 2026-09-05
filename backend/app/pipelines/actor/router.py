from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...config import settings
from ...core.jobs import (
    cancel_job,
    create_job,
    enrich_job_urls,
    list_jobs,
    load_job,
    start_pipeline_job,
)
from ...core.library import list_assets, load_asset
from ...core.schemas import JobStatus
from ...pipelines.registry import get_pipeline
from .schemas import (
    ActorJobResponse,
    ActorListResponse,
    ActorRecord,
    SaveActorRequest,
)
from .workflow import derive_mode

router = APIRouter(tags=["actors"])

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


async def _read_image(upload: UploadFile | None, field: str) -> tuple[str, bytes] | None:
    if upload is None:
        return None
    filename = upload.filename or f"{field}.png"
    ext = Path(filename).suffix.lower()
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(400, f"{field}: unsupported file type {ext}")
    data = await upload.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(400, f"{field}: file exceeds {settings.max_upload_mb}MB")
    if len(data) == 0:
        raise HTTPException(400, f"{field}: empty file")
    return filename, data


def _to_response(job) -> ActorJobResponse:
    pipe = get_pipeline("actor")
    job = enrich_job_urls(job, labels=pipe.output_labels)
    return ActorJobResponse.from_job(job)


@router.get("/meta/actor-defaults")
async def actor_defaults() -> dict:
    pipe = get_pipeline("actor")
    meta = pipe.meta_defaults()
    meta["max_upload_mb"] = settings.max_upload_mb
    return meta


@router.post("/actors/generate", response_model=ActorJobResponse)
async def generate_actor(
    name: str = Form(...),
    notes: str = Form(""),
    description: str = Form(""),
    body_description: str = Form(""),
    hair_description: str = Form(""),
    negative_prompt: str = Form(""),
    seed: str = Form(""),
    fixed_seed: bool = Form(False),
    project_id: str = Form(""),
    include_headwear: bool = Form(False),
    include_footwear: bool = Form(False),
    actor_image: UploadFile | None = File(None),
    wardrobe_image: UploadFile | None = File(None),
) -> ActorJobResponse:
    """
    Auto-routed casting job:
    - optional actor_image (none → text; headshot/fullbody inferred in workflow)
    - optional wardrobe_image (none → keep outfit; present → extract + transfer)
    - no toggles
    """
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    actor_img = await _read_image(actor_image, "actor_image")
    wardrobe_img = await _read_image(wardrobe_image, "wardrobe_image")
    has_actor = actor_img is not None
    has_wardrobe = wardrobe_img is not None
    include_headwear = has_wardrobe and include_headwear
    include_footwear = has_wardrobe and include_footwear

    if not has_actor and not (description or "").strip():
        raise HTTPException(
            400,
            "description is required when no actor reference is uploaded",
        )

    seed_val: int | None = None
    if seed.strip():
        try:
            seed_val = int(seed.strip())
        except ValueError:
            raise HTTPException(400, "seed must be an integer") from None

    mode = derive_mode(has_actor_ref=has_actor, has_wardrobe_ref=has_wardrobe)
    pipe = get_pipeline("actor")
    proj = (project_id or "").strip() or None
    job = create_job(
        pipeline_id=pipe.id,
        asset_kind=pipe.asset_kind,
        name=name,
        notes=notes,
        params={
            "description": description,
            "body_description": body_description,
            "hair_description": hair_description,
            "negative_prompt": negative_prompt,
            "has_actor_ref": has_actor,
            "has_wardrobe_ref": has_wardrobe,
            "include_headwear": include_headwear,
            "include_footwear": include_footwear,
            "mode": mode,  # display only
        },
        seed=seed_val,
        fixed_seed=fixed_seed,
        project_id=proj,
    )

    images: dict[str, tuple[str, bytes]] = {}
    if actor_img:
        images["actor"] = actor_img
    if wardrobe_img:
        images["wardrobe"] = wardrobe_img

    job = await start_pipeline_job(job, images=images)
    return _to_response(job)


@router.get("/actors/jobs/{job_id}", response_model=ActorJobResponse)
async def get_job(job_id: str) -> ActorJobResponse:
    job = load_job(job_id)
    if not job or job.pipeline_id != "actor":
        raise HTTPException(404, "Job not found")
    return _to_response(job)


@router.get("/actors/jobs", response_model=list[ActorJobResponse])
async def list_actor_jobs(
    limit: int = 30,
    project_id: str | None = None,
) -> list[ActorJobResponse]:
    return [
        _to_response(j)
        for j in list_jobs(
            limit,
            pipeline_id="actor",
            project_id=project_id or None,
        )
    ]


@router.post("/actors/jobs/{job_id}/cancel", response_model=ActorJobResponse)
async def cancel_actor_job(job_id: str) -> ActorJobResponse:
    job = load_job(job_id)
    if not job or job.pipeline_id != "actor":
        raise HTTPException(404, "Job not found")
    job = await cancel_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _to_response(job)


@router.post("/actors/jobs/{job_id}/save", response_model=ActorRecord)
async def save_job_to_library(job_id: str, body: SaveActorRequest | None = None) -> ActorRecord:
    job = load_job(job_id)
    if not job or job.pipeline_id != "actor":
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.succeeded:
        raise HTTPException(400, f"Job status is {job.status}, need succeeded")
    pipe = get_pipeline("actor")
    proj = (body.project_id if body else None) or job.project_id
    if isinstance(proj, str):
        proj = proj.strip() or None
    try:
        if proj and not job.project_id:
            job.project_id = proj
            from ...core.jobs import save_job as _save_job

            _save_job(job)
        asset = pipe.save_to_library(
            job,
            name=body.name if body else None,
            notes=body.notes if body else None,
            project_id=proj,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return ActorRecord.from_library(asset)


@router.get("/actors", response_model=ActorListResponse)
async def list_actors(
    project_id: str | None = None,
    include_unassigned: bool = False,
) -> ActorListResponse:
    items = [
        ActorRecord.from_library(a)
        for a in list_assets(
            "actors",
            project_id=project_id or None,
            include_unassigned=include_unassigned,
        )
    ]
    return ActorListResponse(items=items)


@router.get("/actors/{actor_id}", response_model=ActorRecord)
async def get_actor(actor_id: str) -> ActorRecord:
    asset = load_asset("actors", actor_id)
    if not asset:
        raise HTTPException(404, "Actor not found")
    return ActorRecord.from_library(asset)
