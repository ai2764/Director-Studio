"""Thin job-status routes for h3_ref2va. Primary submit is via projects API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...config import settings
from ...core.jobs import (
    cancel_job,
    enrich_job_urls,
    list_jobs,
    load_job,
    resume_pipeline_job,
    save_job,
)
from ...core.schemas import JobStatus
from ...pipelines.registry import get_pipeline
from .schemas import H3Ref2VaJobResponse

router = APIRouter(tags=["h3_ref2va"])


def _to_response(job) -> H3Ref2VaJobResponse:
    pipe = get_pipeline("h3_ref2va")
    job = enrich_job_urls(job, labels=pipe.output_labels)
    return H3Ref2VaJobResponse.from_job(job)


@router.get("/h3-ref2va/provider")
async def get_h3_provider_status() -> dict[str, object]:
    default_provider = str(settings.h3_provider or "local").strip().lower()
    if default_provider not in {"local", "minimax"}:
        default_provider = "local"
    return {
        "default_provider": default_provider,
        "minimax_configured": bool(str(settings.h3_minimax_api_key or "").strip()),
        "minimax_resolution": str(settings.h3_minimax_resolution or "768P"),
    }


@router.get("/h3-ref2va/jobs/{job_id}", response_model=H3Ref2VaJobResponse)
async def get_h3_job(job_id: str) -> H3Ref2VaJobResponse:
    job = load_job(job_id)
    if not job or job.pipeline_id != "h3_ref2va":
        raise HTTPException(404, "Job not found")
    return _to_response(job)


@router.get("/h3-ref2va/jobs", response_model=list[H3Ref2VaJobResponse])
async def list_h3_jobs(
    limit: int = 30,
    project_id: str | None = None,
    json_shot_id: str | None = None,
    json_storyboard_revision: int | None = None,
) -> list[H3Ref2VaJobResponse]:
    if json_shot_id is not None or json_storyboard_revision is not None:
        jobs = list_jobs(None, pipeline_id="h3_ref2va", project_id=project_id)
        if json_shot_id is not None:
            jobs = [
                job
                for job in jobs
                if (job.params or {}).get("json_shot_id") == json_shot_id
            ]
        if json_storyboard_revision is not None:
            jobs = [
                job
                for job in jobs
                if (job.params or {}).get("json_storyboard_revision")
                == json_storyboard_revision
            ]
        jobs = jobs[:limit]
    else:
        jobs = list_jobs(limit, pipeline_id="h3_ref2va", project_id=project_id)
    return [_to_response(j) for j in jobs]


@router.post("/h3-ref2va/jobs/{job_id}/cancel", response_model=H3Ref2VaJobResponse)
async def cancel_h3_job(job_id: str) -> H3Ref2VaJobResponse:
    job = load_job(job_id)
    if not job or job.pipeline_id != "h3_ref2va":
        raise HTTPException(404, "Job not found")
    job = await cancel_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _to_response(job)


@router.post("/h3-ref2va/jobs/{job_id}/resume", response_model=H3Ref2VaJobResponse)
async def resume_h3_job(job_id: str) -> H3Ref2VaJobResponse:
    """Resume monitoring a previously submitted local or MiniMax H3 task."""
    job = load_job(job_id)
    if not job or job.pipeline_id != "h3_ref2va":
        raise HTTPException(404, "Job not found")
    if not (job.external_task_id or job.comfy_prompt_id):
        raise HTTPException(409, "Job has no submitted provider task to resume")
    if job.status == JobStatus.succeeded:
        raise HTTPException(409, "Job already succeeded")
    job.status = JobStatus.running
    job.error = None
    save_job(job)
    await resume_pipeline_job(job)
    return _to_response(job)
