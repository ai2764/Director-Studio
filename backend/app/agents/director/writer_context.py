"""Inference-only projections; authoritative source records remain untouched."""
from copy import deepcopy


def project_writer_context(context, intent, references):
    sources = {item["id"]: item for item in intent.get("directing_requests", [])
               if isinstance(item, dict) and "id" in item and "text" in item}

    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: project(item) for key, item in value.items()}
        source = sources.get(value.get("id")) if isinstance(value.get("id"), str) else None
        if source is not None and value.get("text") == source["text"]:
            result.pop("text", None)
            result["text_ref"] = f"intent.directing_requests[id={source['id']}].text"
        return result

    projected = project(deepcopy(context))
    target_id = intent.get("current_shot", {}).get("id")
    duration = intent.get("execution_duration_s")
    if target_id and duration is not None:
        # Only current target records, never historical quotes or neighboring shots.
        targets = list(projected.get("shot_summaries", []))
        canonical = projected.get("facts", {}).get("target")
        if isinstance(canonical, dict):
            targets.append(canonical)
        for target in targets:
            if isinstance(target, dict) and target.get("id") == target_id:
                if target.get("duration_s") != duration:
                    target["storyboard_duration_s"] = target.get("duration_s")
                target["duration_s"] = duration
    return projected, project(deepcopy(references))


def video_context_writer_view(shot):
    """Observation metadata for an active continuation. The file name stays out."""
    config = getattr(shot, "video_context", None)
    if config is None or getattr(config, "mode", "off") == "off":
        return None
    view = {
        "mode": config.mode,
        "source_shot_id": config.source_shot_id,
        "source_job_id": config.source_job_id,
        "context_frames": config.context_frames or 22,
        "carry_audio": bool(config.carry_audio),
        "role": "observation_only",
        "picture_slots": [],
        "audio_slots": [],
    }
    try:
        png, source_job_id = _context_tail_png(shot)
        view["tail_frame_png"] = png
        if source_job_id:
            view["source_job_id"] = source_job_id
    except Exception as exc:
        view["tail_frame_error"] = str(exc)
    return view


def _context_tail_png(shot):
    from ...core.projects.store import load_project
    from ...core.projects.video_context import (
        VideoContextError,
        _require_previous_video,
        _require_upload,
    )

    config = shot.video_context
    if config is None:
        raise VideoContextError("Video context is off")
    if config.mode == "external_upload":
        _record, path = _require_upload(shot.project_id, config.upload_id)
        source_job_id = None
    else:
        project = load_project(shot.project_id)
        if project is None:
            raise VideoContextError("Project not found")
        _source, job, _key, path = _require_previous_video(project, shot, config)
        source_job_id = job.id
    return _png_from_video(path), source_job_id


def _png_from_video(path):
    import tempfile
    from pathlib import Path

    from ...core.media.tail_frame import _ffmpeg_last_frame

    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / "tail.png"
        _ffmpeg_last_frame(path, dest, tail_window=False, duration_s=None)
        return dest.read_bytes()


async def complete_writer_prompt(provider, system: str, user: str, observation) -> str:
    """Send the tail frame only when the provider can see images."""
    import base64

    png = observation.get("tail_frame_png") if isinstance(observation, dict) else None
    visual = getattr(provider, "complete_with_images", None)
    if isinstance(png, (bytes, bytearray)) and png and callable(visual):
        encoded = base64.b64encode(bytes(png)).decode("ascii")
        return await visual(
            system, user, images=[encoded], guides=("h3-prompt-writing",),
        )
    return await provider.complete(system, user, guides=("h3-prompt-writing",))
