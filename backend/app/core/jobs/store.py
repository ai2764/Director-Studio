from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config import settings
from ..paths import find_job_dir, iter_job_dirs, job_write_dir
from ..schemas import JobRecord, JobStatus, OutputSlot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str, *, project_id: str | None = None) -> Path:
    """
    Directory for a job.

    Prefer explicit project_id write path; else locate existing; else global write.
    """
    if project_id:
        return job_write_dir(job_id, project_id=project_id)
    found = find_job_dir(job_id)
    if found is not None:
        return found
    return job_write_dir(job_id, project_id=None)


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def create_job(
    *,
    pipeline_id: str,
    asset_kind: str,
    name: str,
    notes: str = "",
    params: dict[str, Any] | None = None,
    seed: int | None = None,
    fixed_seed: bool = False,
    project_id: str | None = None,
) -> JobRecord:
    job_id = new_job_id()
    pid = (project_id or None) or None
    if isinstance(pid, str):
        pid = pid.strip() or None
    d = job_write_dir(job_id, project_id=pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "inputs").mkdir(exist_ok=True)
    (d / "outputs").mkdir(exist_ok=True)
    now = _now()
    job = JobRecord(
        id=job_id,
        pipeline_id=pipeline_id,
        asset_kind=asset_kind,
        status=JobStatus.queued,
        name=name.strip(),
        notes=notes or "",
        params=params or {},
        seed=seed,
        fixed_seed=fixed_seed,
        created_at=now,
        updated_at=now,
        project_id=pid,
    )
    save_job(job)
    return job


def save_job(job: JobRecord) -> None:
    job.updated_at = _now()
    # If job moved project, prefer write under project_id
    d = job_write_dir(job.id, project_id=job.project_id)
    # If an old location exists elsewhere, keep writing there to avoid split state
    found = find_job_dir(job.id)
    if found is not None and found.resolve() != d.resolve():
        # Prefer existing location unless we explicitly want project root
        if job.project_id and "projects" in str(d):
            # move on save when project_id set and still in global
            if "projects" not in str(found):
                import shutil

                d.parent.mkdir(parents=True, exist_ok=True)
                if d.exists():
                    shutil.rmtree(d)
                shutil.move(str(found), str(d))
            else:
                d = found
        else:
            d = found
    d.mkdir(parents=True, exist_ok=True)
    path = d / "job.json"
    path.write_text(job.model_dump_json(indent=2), encoding="utf-8")


def load_job(job_id: str) -> JobRecord | None:
    d = find_job_dir(job_id)
    if d is None:
        return None
    path = d / "job.json"
    if not path.exists():
        return None
    return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))


def list_jobs(
    limit: int | None = 50,
    *,
    pipeline_id: str | None = None,
    project_id: str | None = None,
) -> list[JobRecord]:
    items: list[JobRecord] = []
    for p in iter_job_dirs():
        meta = p / "job.json"
        if not meta.exists():
            continue
        try:
            job = JobRecord.model_validate_json(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if pipeline_id and job.pipeline_id != pipeline_id:
            continue
        if project_id is not None and (job.project_id or None) != project_id:
            continue
        items.append(job)
        if limit is not None and len(items) >= limit:
            break
    return items


def save_input_file(
    job_id: str,
    kind: str,
    filename: str,
    data: bytes,
    *,
    project_id: str | None = None,
) -> Path:
    ext = Path(filename).suffix.lower() or ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".wav", ".mp3", ".flac", ".m4a"}:
        ext = ".png"
    d = job_dir(job_id, project_id=project_id)
    (d / "inputs").mkdir(parents=True, exist_ok=True)
    dest = d / "inputs" / f"{kind}{ext}"
    dest.write_bytes(data)
    return dest


def save_output_file(
    job_id: str,
    key: str,
    filename: str,
    data: bytes,
    *,
    project_id: str | None = None,
) -> Path:
    ext = Path(filename).suffix.lower() or ".png"
    d = job_dir(job_id, project_id=project_id)
    (d / "outputs").mkdir(parents=True, exist_ok=True)
    dest = d / "outputs" / f"{key}{ext}"
    dest.write_bytes(data)
    return dest


def build_output_slots(
    job_id: str,
    files: dict[str, Path],
    labels: dict[str, str] | None = None,
) -> dict[str, OutputSlot]:
    labels = labels or {}
    slots: dict[str, OutputSlot] = {}
    for key, path in files.items():
        slots[key] = OutputSlot(
            key=key,
            label=labels.get(key, key),
            path=str(path),
            filename=path.name,
            # Stable URL — resolver finds project or global job folder
            url=f"/api/files/jobs/{job_id}/outputs/{path.name}",
        )
    return slots


def input_preview_urls(job_id: str) -> dict[str, str]:
    d = find_job_dir(job_id)
    if d is None:
        return {}
    inputs = d / "inputs"
    urls: dict[str, str] = {}
    if not inputs.exists():
        return urls
    for p in inputs.iterdir():
        if p.is_file():
            urls[p.stem] = f"/api/files/jobs/{job_id}/inputs/{p.name}"
    return urls


def enrich_job_urls(job: JobRecord, labels: dict[str, str] | None = None) -> JobRecord:
    job.input_previews = input_preview_urls(job.id)
    d = find_job_dir(job.id)
    if d is None:
        return job
    out_dir = d / "outputs"
    if out_dir.exists():
        files: dict[str, Path] = {}
        for p in out_dir.iterdir():
            if p.is_file():
                files[p.stem] = p
        if files:
            job.outputs = build_output_slots(job.id, files, labels=labels)
    return job
