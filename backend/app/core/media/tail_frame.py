"""Extract the last decoded H3 clip frame into a pending Layout."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..jobs.store import job_dir, load_job
from ..library.store import new_asset_id, write_asset
from ..paths import asset_write_dir
from ..projects.layouts import (
    ClipTailFrameOrigin,
    LayoutReference,
    LayoutReviewStatus,
    mirror_legacy_layout_fields,
)
from ..projects.models import Shot, ShotStatus
from ..projects.store import list_projects, load_shot, save_shot
from ..schemas import JobStatus, LibraryAsset
from .clip_generations import ClipGenerationError, resolve_source_clip

_H3_PIPELINE_ID = "h3_ref2va"
_TAIL_WINDOW_S = 2.0
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ACTIVE_H3_JOB = frozenset(
    {JobStatus.queued, JobStatus.uploading, JobStatus.running}
)
_ACTIVE_H3_SHOT = frozenset({ShotStatus.queued, ShotStatus.running})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_binary(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise ValueError(f"{name} is required to extract a tail frame")
    return resolved


def _run_checked(command: list[str], *, error: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(error) from exc


def extract_clip_tail_frame(
    *,
    project_id: str,
    source_shot_id: str,
    target_shot_id: str,
    source_version: str | None = None,
    source_job_id: str | None = None,
    output_kind: Literal["enhanced", "raw"] | None = None,
) -> dict[str, Any]:
    if source_shot_id == target_shot_id:
        raise ValueError("source and target are the same shot")

    _load_shot(project_id, source_shot_id, role="source")
    target = _load_shot(project_id, target_shot_id, role="target")
    _reject_active_h3(target)

    clip = resolve_source_clip(
        project_id=project_id,
        source_shot_id=source_shot_id,
        source_version=source_version,
        source_job_id=source_job_id,
        output_kind=output_kind,
    )
    _assert_path_inside_job_outputs(clip.path, clip.source_job_id, project_id)

    with tempfile.TemporaryDirectory(prefix="ds_tail_frame_") as raw_tmp:
        tmp_png = Path(raw_tmp) / "layout.png"
        duration_s, timestamp_s = _extract_last_frame(clip.path, tmp_png)
        origin = ClipTailFrameOrigin(
            source_shot_id=clip.source_shot_id,
            source_job_id=clip.source_job_id,
            source_generation=clip.source_generation,
            output_kind=clip.output_kind,
            output_key=clip.output_key,
            source_filename=clip.source_filename,
            source_duration_s=duration_s,
            extracted_timestamp_s=timestamp_s,
        )
        return _persist_pending_layout(
            project_id=project_id,
            target=target,
            png_path=tmp_png,
            origin=origin,
        )


def _load_shot(project_id: str, shot_id: str, *, role: str) -> Shot:
    shot = load_shot(project_id, shot_id)
    if shot is not None:
        return shot
    for project in list_projects():
        if project.id == project_id:
            continue
        other = load_shot(project.id, shot_id)
        if other is not None:
            raise ValueError("source and target are not in the same project")
    raise ValueError(f"{role} shot not found: {shot_id}")


def _reject_active_h3(target: Shot) -> None:
    if target.status in _ACTIVE_H3_SHOT:
        raise ValueError(
            f"target shot has an active H3 job ({target.status.value})"
        )
    if not target.h3_job_id:
        return
    job = load_job(target.h3_job_id)
    if job is not None and job.status in _ACTIVE_H3_JOB:
        raise ValueError(
            f"target shot has an active H3 job ({job.status.value})"
        )


def _assert_path_inside_job_outputs(
    path: Path, job_id: str, project_id: str
) -> None:
    out_dir = (job_dir(job_id, project_id=project_id) / "outputs").resolve()
    try:
        path.resolve().relative_to(out_dir)
    except ValueError as exc:
        raise ClipGenerationError(
            f"job {job_id} output path escapes outputs directory: {path.name}"
        ) from exc
    if not path.is_file():
        raise ClipGenerationError(f"job {job_id} output file missing: {path.name}")


def _probe_duration(path: Path) -> float | None:
    try:
        result = _run_checked(
            [
                _require_binary("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            error="unable to probe video duration",
        )
        payload = json.loads(result.stdout)
        duration = float((payload.get("format") or {}).get("duration"))
    except (ValueError, TypeError, json.JSONDecodeError, KeyError):
        return None
    return duration if duration > 0 else None


def _probe_last_timestamp(path: Path) -> float | None:
    try:
        result = _run_checked(
            [
                _require_binary("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "csv=p=0",
                str(path),
            ],
            error="unable to probe frame timestamps",
        )
    except ValueError:
        return None
    times: list[float] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            times.append(float(text))
        except ValueError:
            continue
    return times[-1] if times else None


def _is_valid_png(path: Path) -> bool:
    if not path.is_file():
        return False
    data = path.read_bytes()
    return len(data) >= 24 and data[:8] == _PNG_MAGIC


def _ffmpeg_last_frame(
    video: Path,
    dest: Path,
    *,
    tail_window: bool,
    duration_s: float | None,
) -> None:
    cmd = [
        _require_binary("ffmpeg"),
        "-y",
        "-hide_banner",
        "-v",
        "error",
    ]
    if tail_window:
        window = _TAIL_WINDOW_S
        if duration_s is not None and 0 < duration_s < window:
            window = max(duration_s, 0.05)
        cmd.extend(["-sseof", f"-{window}"])
    cmd.extend(
        [
            "-i",
            str(video),
            "-an",
            "-update",
            "1",
            str(dest),
        ]
    )
    _run_checked(cmd, error="unable to decode video tail frame")
    if not _is_valid_png(dest):
        raise ValueError("video has no decodable frame")


def _extract_last_frame(
    video: Path, dest: Path
) -> tuple[float | None, float | None]:
    _require_binary("ffmpeg")
    _require_binary("ffprobe")
    duration_s = _probe_duration(video)
    try:
        _ffmpeg_last_frame(
            video, dest, tail_window=True, duration_s=duration_s
        )
    except ValueError:
        if dest.exists():
            dest.unlink()
        _ffmpeg_last_frame(
            video, dest, tail_window=False, duration_s=duration_s
        )
    timestamp_s = _probe_last_timestamp(video)
    return duration_s, timestamp_s


def _persist_pending_layout(
    *,
    project_id: str,
    target: Shot,
    png_path: Path,
    origin: ClipTailFrameOrigin,
) -> dict[str, Any]:
    if not _is_valid_png(png_path):
        raise ValueError("video has no decodable frame")

    target = _load_shot(project_id, target.id, role="target")
    _reject_active_h3(target)

    asset_id = new_asset_id("layouts")
    layout_ref_id = f"lref_{uuid.uuid4().hex[:12]}"
    adir = asset_write_dir("layouts", asset_id, project_id=project_id)
    origin_payload = origin.model_dump(mode="json")
    asset = LibraryAsset(
        id=asset_id,
        kind="layouts",
        name=(
            f"Tail frame {origin.source_shot_id} v{origin.source_generation} "
            f"for {target.id}"
        ),
        notes=(
            "Extracted continuity candidate from a source clip tail frame. "
            "Pending human review."
        ),
        pipeline_id=_H3_PIPELINE_ID,
        job_id=origin.source_job_id,
        seed=None,
        created_at=_now(),
        files={"layout": "layout.png"},
        meta={
            "review_status": LayoutReviewStatus.pending_review.value,
            "origin": origin_payload,
        },
        project_id=project_id,
    )
    new_ref = LayoutReference(
        id=layout_ref_id,
        asset_id=asset_id,
        purpose=f"cross-shot visual continuity from {origin.source_shot_id}",
        state_description=(
            f"final visible state of {origin.source_shot_id} "
            f"for continuity into {target.id}"
        ),
        time_hint=f"transition from {origin.source_shot_id} into this shot",
        source_refs=[],
        review_status=LayoutReviewStatus.pending_review,
        selected_for_h3=False,
        created_at=_now(),
        origin=origin,
    )
    updated = mirror_legacy_layout_fields(
        target.model_copy(
            update={
                "layout_refs": [*target.layout_refs, new_ref],
                "status": ShotStatus.needs_review,
            }
        )
    )
    image_url = f"/api/files/library/layouts/{asset_id}/layout.png"
    created_dir = False
    try:
        adir.mkdir(parents=True, exist_ok=False)
        created_dir = True
        shutil.copy2(png_path, adir / "layout.png")
        if not _is_valid_png(adir / "layout.png"):
            raise ValueError("video has no decodable frame")
        write_asset(asset)
        save_shot(updated)
    except Exception:
        if created_dir and adir.exists():
            shutil.rmtree(adir, ignore_errors=True)
        raise

    return {
        "source_shot_id": origin.source_shot_id,
        "target_shot_id": target.id,
        "source_job_id": origin.source_job_id,
        "source_version": origin.source_generation,
        "output_kind": origin.output_kind,
        "layout_ref_id": layout_ref_id,
        "layout_asset_id": asset_id,
        "image_url": image_url,
    }
