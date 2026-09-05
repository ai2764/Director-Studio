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
    save_job,
    start_pipeline_job,
)
from ...core.library import list_assets, load_asset
from ...core.schemas import JobStatus
from ...pipelines.registry import get_pipeline
from .schemas import (
    SaveSceneRequest,
    SceneJobResponse,
    SceneListResponse,
    SceneRecord,
)
from .workflow import DEFAULT_ANGLES

router = APIRouter(tags=["scenes"])

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


def _to_response(job) -> SceneJobResponse:
    pipe = get_pipeline("scene")
    labels = pipe.labels_for_job(job) if hasattr(pipe, "labels_for_job") else pipe.output_labels
    job = enrich_job_urls(job, labels=labels)
    return SceneJobResponse.from_job(job, labels=labels)


@router.get("/meta/scene-defaults")
async def scene_defaults() -> dict:
    pipe = get_pipeline("scene")
    meta = pipe.meta_defaults()
    meta["max_upload_mb"] = settings.max_upload_mb
    meta["default_angles"] = DEFAULT_ANGLES
    return meta


@router.post("/scenes/generate", response_model=SceneJobResponse)
async def generate_scene(
    name: str = Form(...),
    notes: str = Form(""),
    angle_prompts: str = Form(""),
    prepend_text: str = Form(""),
    append_text: str = Form(""),
    start_index: int = Form(0),
    max_rows: str = Form(""),
    seed: str = Form(""),
    fixed_seed: bool = Form(False),
    project_id: str = Form(""),
    scene_image: UploadFile | None = File(None),
) -> SceneJobResponse:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    scene_img = await _read_image(scene_image, "scene_image")
    if not scene_img:
        raise HTTPException(400, "scene_image is required")

    angles = (angle_prompts or "").strip() or DEFAULT_ANGLES
    if not any(ln.strip() for ln in angles.splitlines()):
        raise HTTPException(400, "angle_prompts must contain at least one non-empty line")

    seed_val: int | None = None
    if seed.strip():
        try:
            seed_val = int(seed.strip())
        except ValueError:
            raise HTTPException(400, "seed must be an integer") from None

    max_rows_val: int | None = None
    if max_rows.strip():
        try:
            max_rows_val = int(max_rows.strip())
        except ValueError:
            raise HTTPException(400, "max_rows must be an integer") from None

    pipe = get_pipeline("scene")
    proj = (project_id or "").strip() or None
    job = create_job(
        pipeline_id=pipe.id,
        asset_kind=pipe.asset_kind,
        name=name,
        notes=notes,
        params={
            "angle_prompts": angles,
            "prepend_text": prepend_text or "",
            "append_text": append_text or "",
            "start_index": start_index,
            "max_rows": max_rows_val,
        },
        seed=seed_val,
        fixed_seed=fixed_seed,
        project_id=proj,
    )

    job = await start_pipeline_job(job, images={"scene": scene_img})
    # build_prompt mutates used_angles on the in-memory job during run; after start
    # the runner reloads — ensure used_angles saved after first build by re-saving params
    # The runner calls build_prompt which sets job.params["used_angles"] then only
    # updates seed — need runner to persist params. Patch: save used_angles in router
    # from a dry build for labels immediately.
    from .workflow import build_scene_prompt

    _, _, used, stems = build_scene_prompt(
        scene_image_name="placeholder",
        scene_name=name,
        angle_prompts=angles,
        prepend_text=prepend_text or "",
        append_text=append_text or "",
        start_index=start_index,
        max_rows=max_rows_val,
        seed=seed_val,
        job_id=job.id,
    )
    job = load_job(job.id) or job
    # Prefer runner-written params if already present; otherwise seed labels early
    job.params.setdefault("used_angles", used)
    job.params.setdefault("output_stems", stems)
    save_job(job)
    return _to_response(job)


@router.get("/scenes/jobs/{job_id}", response_model=SceneJobResponse)
async def get_job(job_id: str) -> SceneJobResponse:
    job = load_job(job_id)
    if not job or job.pipeline_id != "scene":
        raise HTTPException(404, "Job not found")
    return _to_response(job)


@router.get("/scenes/jobs", response_model=list[SceneJobResponse])
async def list_scene_jobs(
    limit: int = 30,
    project_id: str | None = None,
) -> list[SceneJobResponse]:
    return [
        _to_response(j)
        for j in list_jobs(
            limit,
            pipeline_id="scene",
            project_id=project_id or None,
        )
    ]


@router.post("/scenes/jobs/{job_id}/cancel", response_model=SceneJobResponse)
async def cancel_scene_job(job_id: str) -> SceneJobResponse:
    job = load_job(job_id)
    if not job or job.pipeline_id != "scene":
        raise HTTPException(404, "Job not found")
    job = await cancel_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _to_response(job)


@router.post("/scenes/jobs/{job_id}/save", response_model=SceneRecord)
async def save_job_to_library(job_id: str, body: SaveSceneRequest | None = None) -> SceneRecord:
    job = load_job(job_id)
    if not job or job.pipeline_id != "scene":
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.succeeded:
        raise HTTPException(400, f"Job status is {job.status}, need succeeded")
    pipe = get_pipeline("scene")
    proj = (body.project_id if body else None) or job.project_id
    if isinstance(proj, str):
        proj = proj.strip() or None
    try:
        if proj and not job.project_id:
            job.project_id = proj
            save_job(job)
        asset = pipe.save_to_library(
            job,
            name=body.name if body else None,
            notes=body.notes if body else None,
            project_id=proj,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return SceneRecord.from_library(asset)


@router.get("/scenes", response_model=SceneListResponse)
async def list_scenes(
    project_id: str | None = None,
    include_unassigned: bool = False,
) -> SceneListResponse:
    return SceneListResponse(
        items=[
            SceneRecord.from_library(a)
            for a in list_assets(
                "scenes",
                project_id=project_id or None,
                include_unassigned=include_unassigned,
            )
        ]
    )


@router.get("/scenes/{scene_id}", response_model=SceneRecord)
async def get_scene(scene_id: str) -> SceneRecord:
    asset = load_asset("scenes", scene_id)
    if not asset:
        raise HTTPException(404, "Scene not found")
    return SceneRecord.from_library(asset)
