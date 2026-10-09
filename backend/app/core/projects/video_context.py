"""Save and resolve a shot's finished-video continuation source."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ...config import settings
from ..jobs.store import job_dir, load_job
from ..schemas import JobStatus
from .models import Project, Shot, ShotVideoContext
from .store import load_project, load_shot, save_shot

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_DURATION_S = 60.0
ALLOWED_SUFFIXES = {".mp4", ".mov", ".webm"}
VIDEO_OUTPUT_KEYS = {"video", "video_raw"}


class VideoContextError(ValueError):
    """The requested continuation cannot be saved or resolved."""


class VideoContextPending(VideoContextError):
    """A valid earlier-shot dependency is waiting for its video."""


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: float
    duration_s: float
    has_audio: bool
    has_video: bool


@dataclass(frozen=True)
class ResolvedVideoContext:
    filename: str
    data: bytes
    provenance: dict


def configure_video_context(
    project_id: str, shot_id: str, config: ShotVideoContext
) -> dict:
    """Validate and store continuation settings. Failures leave the shot unchanged."""
    project = load_project(project_id)
    shot = load_shot(project_id, shot_id)
    if project is None or shot is None or shot.project_id != project_id:
        raise VideoContextError("Shot not found")
    prepared = _prepare_config(project, shot, config)
    if prepared.mode != "off":
        try:
            _runtime_options(prepared)
        except ValueError as exc:
            raise VideoContextError(str(exc)) from exc
    saved = shot.model_copy(update={"video_context": prepared})
    save_shot(saved)
    status = video_context_status(saved)
    return {"shot_id": shot.id, "video_context": prepared.model_dump(),
            "state": status["state"], "ready": status["ready"]}


def resolve_video_context(
    shot: Shot, *, width: int, height: int
) -> ResolvedVideoContext | None:
    """Read the configured source once and freeze its bytes and provenance."""
    config = shot.video_context
    if config is None or config.mode == "off":
        return None
    project = load_project(shot.project_id)
    if project is None:
        raise VideoContextError("Project not found")
    if config.mode == "external_upload":
        record, path = _require_upload(shot.project_id, config.upload_id)
        source_job_id = None
        source_shot_id = None
        output_key = None
    else:
        source_shot, job, output_key, path = _require_previous_video(project, shot, config)
        record = None
        source_job_id = job.id
        source_shot_id = source_shot.id
    data = path.read_bytes()
    media = probe_video(path)
    if config.mode == "previous_shot" and (width, height) != (media.width, media.height):
        raise VideoContextError(
            f"Shot continuation requires the source resolution {media.width}×{media.height}"
        )
    _require_compatible_frame(media, width=width, height=height)
    digest = hashlib.sha256(data).hexdigest()
    from ...pipelines.h3_ref2va.video_context import prepare_context_bytes, context_runtime_options
    from ...workflow_profiles.h3.store import resolve_active_h3_profile
    try:
        runtime = context_runtime_options(config, resolve_active_h3_profile())
    except ValueError as exc:
        raise VideoContextError(str(exc)) from exc
    carry_audio = runtime["carry_audio"]

    data, conversion = prepare_context_bytes(
        data,
        media_width=media.width,
        media_height=media.height,
        media_fps=media.fps,
        media_duration_s=media.duration_s,
        has_audio=media.has_audio,
        width=width,
        height=height,
        context_frames=runtime["context_frames"],
        carry_audio=carry_audio,
        filename=path.name,
    )
    provenance = {
        "mode": config.mode,
        "source_shot_id": source_shot_id,
        "source_job_id": source_job_id,
        "source_output_key": output_key,
        "upload_id": None if record is None else record["upload_id"],
        "sha256": digest,
        "media": asdict(media),
        "context_frames": runtime["context_frames"],
        "audio_context_frames": runtime["audio_context_frames"] if carry_audio else None,
        "carry_audio": carry_audio,
        "conversion": conversion,
    }
    return ResolvedVideoContext(filename=path.name, data=data, provenance=provenance)


def submission_video_context(
    shot: Shot, *, width: int, height: int, delivered_frames: int | None = None
) -> dict | None:
    """Params and bytes for the canonical H3 submit. Disabled experiments add nothing."""
    if not settings.video_context_enabled:
        return None
    resolved = resolve_video_context(shot, width=width, height=height)
    if resolved is None:
        return None
    if delivered_frames is not None:
        from ...pipelines.h3_ref2va.video_context import context_generation_frames
        from ..h3.frames import MAX_FRAMES
        try:
            context_generation_frames(delivered_frames, resolved.provenance["context_frames"], max_frames=MAX_FRAMES)
        except ValueError as exc:
            raise VideoContextError(str(exc)) from exc
    return {
        "params": {
            "context_video_key": "context_video",
            "video_context_source": resolved.provenance,
        },
        "filename": resolved.filename,
        "data": resolved.data,
    }


def video_context_prompt_signature(shot: Shot) -> str:
    """Hash settings and the actual source that the writer must observe."""
    config = shot.video_context
    payload = (
        {"mode": "off"}
        if config is None or config.mode == "off"
        else dict(config.model_dump(mode="json"))
    )
    if config is not None and config.mode != "off":
        payload["writer_contract_version"] = 3
        try:
            if config.mode == "external_upload":
                _record, path = _require_upload(shot.project_id, config.upload_id)
                source_job_id = None
                output_key = None
            else:
                project = load_project(shot.project_id)
                if project is None:
                    raise VideoContextError("Project not found")
                _source, job, output_key, path = _require_previous_video(project, shot, config)
                source_job_id = job.id
            payload["resolved_source"] = {
                "job_id": source_job_id, "output_key": output_key,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            payload["runtime"] = _runtime_options(config)
        except (ValueError, OSError) as exc:
            # Missing/running sources must invalidate an earlier prompt stamp.
            payload["source_error"] = str(exc)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def video_context_prompt_is_stale(shot: Shot) -> bool:
    """Active context, or a previously stamped shot, must match the stored signature."""
    config = shot.video_context
    active = config is not None and config.mode != "off"
    meta = shot.meta or {}
    if not active and "prompt_video_context_signature" not in meta:
        return False
    stored = str(meta.get("prompt_video_context_signature") or "")
    return stored != video_context_prompt_signature(shot)


def video_context_status(shot: Shot) -> dict:
    """Resolved source and block reasons for status reads. Paths stay private."""
    config = shot.video_context
    if config is None or config.mode == "off":
        return {
            "mode": "off",
            "state": "off",
            "ready": True,
            "source_job_id": None,
            "context_frames": None,
            "carry_audio": False,
            "blocked_reasons": [],
        }
    blocked: list[str] = []
    state = "ready"
    source_job_id = config.source_job_id
    runtime = {"context_frames": config.context_frames or 22, "carry_audio": bool(config.carry_audio)}
    try:
        runtime = _runtime_options(config)
    except ValueError as exc:
        blocked.append(str(exc))
        state = "blocked"
    if config.mode == "previous_shot":
        project = load_project(shot.project_id)
        try:
            if project is None:
                raise VideoContextError("Project not found")
            _source, job, _key, _path = _require_previous_video(project, shot, config)
            source_job_id = job.id
            probe_video(_path)
        except VideoContextPending as exc:
            blocked.append(str(exc))
            if state != "blocked":
                state = "waiting"
        except VideoContextError as exc:
            blocked.append(str(exc))
            state = "blocked"
    else:
        try:
            _record, path = _require_upload(shot.project_id, config.upload_id)
            probe_video(path)
        except VideoContextError as exc:
            blocked.append(str(exc))
            state = "blocked"
    return {
        "mode": config.mode,
        "state": state,
        "ready": state == "ready",
        "source_job_id": source_job_id,
        "context_frames": runtime["context_frames"],
        "carry_audio": runtime["carry_audio"],
        "blocked_reasons": blocked,
    }


def video_context_resolution(shot: Shot) -> dict | None:
    """Only previous-shot handoffs lock size, using actual selected video bytes."""
    config = shot.video_context
    if config is None or config.mode != "previous_shot":
        return None
    project = load_project(shot.project_id)
    if project is None:
        raise VideoContextError("Project not found")
    _source, _job, _key, path = _require_previous_video(project, shot, config)
    media = probe_video(path)
    return {"width": media.width, "height": media.height}


def _runtime_options(config):
    from ...pipelines.h3_ref2va.video_context import context_runtime_options
    from ...workflow_profiles.h3.store import resolve_active_h3_profile
    return context_runtime_options(config, resolve_active_h3_profile())


def upload_video_context(project_id: str, filename: str, data: bytes) -> dict:
    """Store one project video. A failed probe leaves no upload record."""
    if load_project(project_id) is None:
        raise VideoContextError("Project not found")
    suffix = _safe_suffix(filename)
    if len(data) > MAX_UPLOAD_BYTES:
        raise VideoContextError("Video upload exceeds 200 MiB")
    upload_id = f"vup_{uuid.uuid4().hex[:12]}"
    directory = _upload_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{upload_id}{suffix}"
    path = (directory / stored_name).resolve()
    path.relative_to(directory.resolve())
    path.write_bytes(data)
    try:
        media = probe_video(path)
        if not media.has_video:
            raise VideoContextError("Upload has no video stream")
        if media.duration_s > MAX_DURATION_S:
            raise VideoContextError("Video upload exceeds 60 seconds")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    record = {
        "upload_id": upload_id,
        "filename": stored_name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "media": asdict(media),
        "size_bytes": len(data),
    }
    _record_path(project_id, upload_id).write_text(
        json.dumps(record), encoding="utf-8"
    )
    return record


def load_video_context_upload(project_id: str, upload_id: str) -> tuple[dict, bytes]:
    """Read one project upload record and its bytes. The record has no host path."""
    record, path = _require_upload(project_id, upload_id)
    return record, path.read_bytes()


def ensure_video_context_order(shots: list[Shot]) -> None:
    """Keep every source earlier than its target; unrelated shots may move freely."""
    ids = [shot.id for shot in shots]
    for index, shot in enumerate(shots):
        config = shot.video_context
        if config is None or config.mode != "previous_shot":
            continue
        if config.source_shot_id not in ids[:index]:
            raise VideoContextError(
                f"Video context source for {shot.id} must remain an earlier shot on the storyboard"
            )


def probe_video(path: Path) -> MediaInfo:
    binary = shutil.which("ffprobe")
    if not binary:
        raise VideoContextError("ffprobe is required to inspect a context video")
    try:
        result = subprocess.run(
            [
                binary, "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate",
                "-of", "json", str(path),
            ],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise VideoContextError("Unable to inspect the video") from exc
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise VideoContextError("Upload has no video stream")
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        duration_s = float((payload.get("format") or {}).get("duration") or 0)
        fps = _frame_rate(str(video.get("avg_frame_rate") or "0/1"))
    except (TypeError, ValueError) as exc:
        raise VideoContextError("Unable to inspect the video") from exc
    if width <= 0 or height <= 0 or duration_s <= 0 or fps <= 0:
        raise VideoContextError("Unable to inspect the video")
    return MediaInfo(
        width=width, height=height, fps=fps, duration_s=duration_s,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        has_video=True,
    )


def _prepare_config(project: Project, shot: Shot, config: ShotVideoContext) -> ShotVideoContext:
    if config.audio_context_frames is not None and not 1 <= config.audio_context_frames <= 240:
        raise VideoContextError("Audio context length is out of range")
    if config.mode == "off":
        return ShotVideoContext(mode="off")
    if config.mode == "external_upload":
        _require_upload(project.id, config.upload_id)
        return config.model_copy(update={"source_shot_id": None, "source_job_id": None})
    source = _previous_shot(project, shot, config.source_shot_id)
    prepared = config.model_copy(update={"source_shot_id": source.id, "upload_id": None})
    try:
        _source, _job, output_key, _path = _require_previous_video(project, shot, prepared)
    except VideoContextPending:
        # Structural validity is already checked. Do not invent a Job or select
        # an output that does not exist; resolution freezes the actual bytes later.
        return prepared
    return prepared.model_copy(update={"source_output_key": output_key})


def _previous_shot(project: Project, shot: Shot, requested_id: str | None) -> Shot:
    """Resolve a selected earlier shot, defaulting to the adjacent predecessor."""
    ids = list(project.shot_ids)
    if shot.id not in ids:
        raise VideoContextError("Shot is not on the storyboard")
    index = ids.index(shot.id)
    if index == 0:
        raise VideoContextError("This shot has no previous shot")
    source_id = requested_id or ids[index - 1]
    if source_id not in ids:
        raise VideoContextError("Video context source was not found on this project's storyboard")
    if ids.index(source_id) >= index:
        raise VideoContextError("Video context source must be an earlier shot; self and future dependencies are not allowed")
    source = load_shot(project.id, source_id)
    if source is None or source.project_id != project.id:
        raise VideoContextError("Source shot was not found in this project")
    return source


def _require_previous_video(project: Project, shot: Shot, config: ShotVideoContext):
    source = _previous_shot(project, shot, config.source_shot_id)
    job_id = config.source_job_id or source.h3_job_id
    if not job_id:
        raise VideoContextPending("Source shot has no H3 job")
    job = load_job(job_id)
    if job is None or job.project_id != project.id or str((job.params or {}).get("shot_id") or "") != source.id:
        raise VideoContextError("Context job does not belong to the source shot")
    if job.status in {JobStatus.queued, JobStatus.uploading, JobStatus.running}:
        raise VideoContextPending(f"Source shot job is {job.status.value}")
    if job.status != JobStatus.succeeded:
        raise VideoContextError(f"Source shot job is {job.status.value}")
    output_key = _selected_output_key(job, config)
    return source, job, output_key, _output_file(job, output_key)


def _selected_output_key(job, config: ShotVideoContext) -> str:
    candidates = [
        key for key, slot in (job.outputs or {}).items()
        if key in VIDEO_OUTPUT_KEYS or _slot_suffix(slot) in ALLOWED_SUFFIXES
    ]
    if not candidates:
        raise VideoContextError("Source shot has no video artifact")
    explicit = bool(config.source_output_key)
    if len(candidates) > 1 and not explicit:
        raise VideoContextError("Select one video artifact from the source shot")
    chosen = config.source_output_key if explicit else candidates[0]
    if chosen not in candidates:
        raise VideoContextError("Selected video artifact was not found")
    return chosen


def _output_file(job, key: str) -> Path:
    slot = (job.outputs or {}).get(key)
    if slot is None:
        raise VideoContextError("Selected video artifact was not found")
    name = slot.filename or (Path(slot.path).name if slot.path else "")
    if not name or Path(name).name != name:
        raise VideoContextError("Selected video artifact was not found")
    out_dir = (job_dir(job.id, project_id=job.project_id) / "outputs").resolve()
    path = (out_dir / name).resolve()
    try:
        path.relative_to(out_dir)
    except ValueError as exc:
        raise VideoContextError("Selected video artifact escapes the job output") from exc
    if not path.is_file():
        raise VideoContextError("Source shot video file is missing")
    return path


def _require_upload(project_id: str, upload_id: str | None):
    if not upload_id:
        raise VideoContextError("External video upload is missing")
    record_path = _record_path(project_id, upload_id)
    if not record_path.is_file():
        raise VideoContextError("External video upload was not found")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    path = _stored_upload_path(project_id, str(record.get("filename") or ""))
    if not path.is_file():
        raise VideoContextError("External video file is missing")
    return record, path


def _stored_upload_path(project_id: str, filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise VideoContextError("External video upload was not found")
    directory = _upload_dir(project_id).resolve()
    path = (directory / filename).resolve()
    path.relative_to(directory)
    return path


def _upload_dir(project_id: str) -> Path:
    return settings.projects_dir / project_id / "video_context_uploads"


def _record_path(project_id: str, upload_id: str) -> Path:
    if not upload_id or Path(upload_id).name != upload_id:
        raise VideoContextError("External video upload was not found")
    return _upload_dir(project_id) / f"{upload_id}.json"


def _safe_suffix(filename: str) -> str:
    suffix = Path(str(filename).replace("\\", "/")).name
    suffix = Path(suffix).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise VideoContextError("Unsupported video type")
    return suffix


def _slot_suffix(slot) -> str:
    name = slot.filename or slot.path or ""
    return Path(str(name)).suffix.lower()


def _require_compatible_frame(media: MediaInfo, *, width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise VideoContextError("Target resolution is invalid")
    source_aspect = media.width / media.height
    target_aspect = width / height
    if abs(source_aspect - target_aspect) / target_aspect > 0.01:
        raise VideoContextError(
            "Video context aspect ratio differs from the shot by more than 1%"
        )


def _frame_rate(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return 0
        return float(numerator) / denominator_value
    return float(value)
