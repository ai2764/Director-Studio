"""Inference-only projections; authoritative source records remain untouched."""
from copy import deepcopy


VIDEO_CONTEXT_WRITER_INSTRUCTIONS = """
Active video continuation is a real, separate finished-video Motion Context input.
video_context_observation and the backend-owned continuation packet describe its
saved state. They do not create Picture or Audio slots. There is no Video tag syntax;
do not invent a <Video N> binding or describe the observation as <Picture N>.
If tail_frame_image_index is present, that Writer image shows the source video's
ending, for observation only. Ground the new opening in its visible crop, viewpoint,
pose, screen position, props and geography. Pictures still supply their declared
identity/design/set responsibilities; do not restart from their compositions.
Describe a credible action and camera path from the inherited state to the requested
next beat. A destination close-up need not be the opening: show the movement that
reaches it. Keep all six sections consistent. Do not silently replace required
continuation with a hard cut, reset, dissolve or transformation. Preserve explicit
authored constraints; ask about an irreconcilable choice rather than changing them.
A single tail image establishes visible state, not observed movement or speed.
Do not invent unseen tail facts when the image is not attached; use verified visual
descriptions when available and state an essential evidence gap honestly.
Action seconds refer to the new delivered shot, starting at zero, within its supplied
duration. The context window is an inherited prefix handled by the backend; do not
replay its action or add its seconds to the requested shot duration.
carry_audio=false disables source-video audio inheritance only. It does not mute the
new shot, remove its Audio references, suppress authored off-screen dialogue, or
change its song interval. When true, describe only the requested sound continuity;
do not invent words or sounds from a still image. Keep filenames and source IDs out
of the six H3 prompt fields. Saved configuration, not prompt prose, enables context.
"""


def video_context_prompt_input(system, user, observation, *, image_attached=False):
    """Label backend observation images without exposing bytes or host filenames."""
    import json

    if not isinstance(observation, dict) or observation.get("mode") not in {
        "previous_shot", "external_upload",
    }:
        return system, user
    packet = {key: observation.get(key) for key in (
        "mode", "source_shot_id", "source_job_id", "context_frames",
    )}
    packet.update(
        conditioning="finished_video_motion_context",
        role="observation_only",
        tail_frame_role="source_video_ending_observation",
        tail_frame_image_index=1 if image_attached else None,
        tail_frame_status="attached" if image_attached else "not_attached",
        carry_audio=bool(observation.get("carry_audio")),
        picture_slots=[], audio_slots=[],
    )
    # Bounded drafts/reviews use JSON envelopes; retain that dialect.
    try:
        body = json.loads(user)
    except (ValueError, TypeError):
        body = None
    if isinstance(body, dict):
        user = json.dumps({**body, "video_context_observation": packet}, ensure_ascii=False)
    else:
        user += "\nVideo continuation input (backend-owned observation):\n" + json.dumps(packet)
    return system + "\n" + VIDEO_CONTEXT_WRITER_INSTRUCTIONS, user


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
    from ...core.projects.video_context import video_context_status
    status = video_context_status(shot)
    view.update(context_frames=status["context_frames"], carry_audio=status["carry_audio"])
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

    active = isinstance(observation, dict) and observation.get("mode") in {"previous_shot", "external_upload"}
    png = observation.get("tail_frame_png") if active else None
    visual = getattr(provider, "complete_with_images", None)
    attached = isinstance(png, (bytes, bytearray)) and bool(png) and callable(visual)
    system, user = video_context_prompt_input(system, user, observation, image_attached=attached)
    if attached:
        encoded = base64.b64encode(bytes(png)).decode("ascii")
        return await visual(
            system, user, images=[encoded], guides=("h3-prompt-writing",),
        )
    return await provider.complete(system, user, guides=("h3-prompt-writing",))
