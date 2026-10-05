"""Attach a finished video to an H3 graph and keep the delivered length."""

from __future__ import annotations

import copy
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...core.h3.frames import MAX_FRAMES

TARGET_FPS = 24.0
ASPECT_TOLERANCE = 0.01
LEGAL_CONTEXT_FRAMES = (5, 22, 39, 56)
VIDEO_FILE_FIELDS = {"LoadVideo": "file", "VHS_LoadVideo": "video"}


def context_runtime_options(config, profile=None) -> dict[str, Any]:
    """Read custom Motion Context settings; the file mapping does not own them."""
    if profile is not None and profile.source == "custom":
        motions = [n for n in profile.workflow.values() if n.get("class_type") == "MiniMaxH3MotionContext"]
        if len(motions) != 1:
            raise VideoContextGraphError("Only a single Ref2AV Motion Context variation is certified for video continuation")
        mapping = getattr(profile, "mapping", None)
        video_mapping = getattr(mapping, "context_video", None)
        _validate_context_sampling_chain(profile.workflow,
            h3_node_id=_require_role(profile.workflow, "MiniMaxH3ReferenceToVideo"),
            context_node_id=video_mapping.node_id if video_mapping else None)
        inputs = motions[0]["inputs"]
        window = int(inputs.get("context_length", 22))
        if window not in LEGAL_CONTEXT_FRAMES:
            raise VideoContextGraphError("Custom Motion Context window must be 5, 22, 39 or 56 frames")
        for node in profile.workflow.values():
            if node.get("class_type") in {"CreateVideo", "MiniMaxH3MotionContextTrim"}:
                if float(node["inputs"].get("fps", 24)) != TARGET_FPS:
                    raise VideoContextGraphError("Video continuation is certified only at 24 fps")
        audio_window = int(inputs.get("audio_context_length", 0))
        carry = "context_audio" in inputs and audio_window > 0
        for field, actual in (("context_frames", window), ("carry_audio", carry), ("audio_context_frames", audio_window)):
            value = getattr(config, field, None)
            if value is not None and value != actual:
                raise VideoContextGraphError(f"Custom workflow owns {field}; keep its uploaded value")
        return {"context_frames": window, "audio_context_frames": audio_window, "carry_audio": carry}
    carry = bool(config.carry_audio)
    return {"context_frames": config.context_frames or 22,
            "audio_context_frames": config.audio_context_frames if config.audio_context_frames is not None else (24 if carry else 0),
            "carry_audio": carry}


def fit_mapped_context_delivery(graph, *, h3_node_id, delivered_frames, context_node_id=None):
    """Budget a certified imported Motion Context graph without changing its windows."""
    motions = [key for key,n in graph.items() if n.get("class_type") == "MiniMaxH3MotionContext"]
    if not motions:
        return  # Other file-input profiles remain opaque; no continuation certification.
    _validate_context_sampling_chain(graph, h3_node_id=h3_node_id, context_node_id=context_node_id)
    motion_id = _require_role(graph, "MiniMaxH3MotionContext")
    trim_id = _require_role(graph, "MiniMaxH3MotionContextTrim")
    create_id = _require_role(graph, "CreateVideo")
    window = int(_inputs(graph, motion_id)["context_length"])
    trim = _inputs(graph, trim_id)
    create = _inputs(graph, create_id)
    if float(trim.get("fps", 24)) != TARGET_FPS or float(create.get("fps", 24)) != TARGET_FPS:
        raise VideoContextGraphError("Video continuation is certified only at 24 fps")
    if trim.get("trim_frames") not in ([motion_id, 1], window):
        raise VideoContextGraphError("Custom overlap trim must match its Motion Context window")
    _inputs(graph, h3_node_id)["length"] = context_generation_frames(delivered_frames, window, max_frames=MAX_FRAMES)
    for field, class_type, source_field, output_index, extra in (
        ("images", "ImageFromBatch", "image", 0, {"batch_index": 0, "length": int(delivered_frames)}),
        ("audio", "TrimAudioDuration", "audio", 1, {"start_index": 0.0, "duration": delivered_frames / TARGET_FPS}),
    ):
        link = create.get(field)
        if link == [trim_id, output_index]:
            node_id = _add(graph, class_type, {source_field: link, **extra}, "Delivered " + field)
            create[field] = [node_id, 0]
        elif (isinstance(link, list) and len(link) == 2 and link[1] == 0
              and graph.get(str(link[0]), {}).get("class_type") == class_type
              and _inputs(graph, str(link[0])).get(source_field) == [trim_id, output_index]):
            _inputs(graph, str(link[0])).update(extra)
        else:
            raise VideoContextGraphError("Unsupported custom continuation delivery path; use direct overlap trim or delivery crop")


def _validate_context_sampling_chain(graph, *, h3_node_id, context_node_id=None):
    """Certify that the mapped video conditions the samples used by both decoders."""
    try:
        motion_id = _require_role(graph, "MiniMaxH3MotionContext")
        guider_id = _require_role(graph, "BasicGuider")
        sampler_id = _require_role(graph, "SamplerCustomAdvanced")
        decode_id = _require_role(graph, "VAEDecode")
        audio_decode_id = _require_role(graph, "VAEDecodeAudio")
        trim_id = _require_role(graph, "MiniMaxH3MotionContextTrim")
        components_id = _require_role(graph, "GetVideoComponents")
        video_id = context_node_id or _require_role(graph, "LoadVideo")
        if graph.get(video_id, {}).get("class_type") != "LoadVideo":
            raise ValueError("certified input must be LoadVideo")
        required = [
            (motion_id, "conditioning", [h3_node_id, 0]),
            (motion_id, "latent", [h3_node_id, 1]),
            (motion_id, "context_frames", [components_id, 0]),
            (components_id, "video", [video_id, 0]),
            (guider_id, "conditioning", [motion_id, 0]),
            (sampler_id, "guider", [guider_id, 0]),
            (sampler_id, "latent_image", [h3_node_id, 1]),
            (trim_id, "images", [decode_id, 0]),
            (trim_id, "audio", [audio_decode_id, 0]),
        ]
        for node_id, field, expected in required:
            if _inputs(graph, node_id).get(field) != expected:
                raise ValueError(f"{node_id}.{field} bypasses the certified path")
        video_samples = _inputs(graph, decode_id).get("samples")
        audio_samples = _inputs(graph, audio_decode_id).get("samples")
        if video_samples not in ([sampler_id, 0], [sampler_id, 1]) or audio_samples != video_samples:
            raise ValueError("video and audio decoders must use the same conditioned samples")
    except ValueError as exc:
        raise VideoContextGraphError(f"Unsupported continuation sampling chain: {exc}") from exc


class VideoContextGraphError(ValueError):
    """The graph cannot carry a finished-video context window."""


def context_generation_frames(
    delivered_frames: int,
    context_frames: int,
    *,
    max_frames: int,
) -> int:
    """Return the shortest legal sample length that can still deliver F frames."""
    delivered = int(delivered_frames)
    context = int(context_frames)
    if context not in LEGAL_CONTEXT_FRAMES:
        raise ValueError(
            f"context frames must be one of {', '.join(str(item) for item in LEGAL_CONTEXT_FRAMES)}"
        )
    if delivered <= 0:
        raise ValueError("delivered frame count must be positive")
    minimum = delivered + context
    sampled = minimum + ((5 - minimum) % 17)
    if sampled > int(max_frames):
        raise ValueError("video_context_length_exceeds_limit")
    return sampled


def attach_video_context(
    graph: dict[str, Any],
    *,
    uploaded_video: str,
    delivered_frames: int,
    context_frames: int,
    audio_context_frames: int,
    carry_audio: bool,
) -> dict[str, Any]:
    """Return a new graph that samples the overlap and delivers the original length.

    The input template is not modified. Node ids come from the graph itself.
    """
    if not str(uploaded_video or "").strip():
        raise VideoContextGraphError("A context video filename is required")
    filled = copy.deepcopy(graph)
    h3_id = _require_role(filled, "MiniMaxH3ReferenceToVideo")
    h3_inputs = _inputs(filled, h3_id)
    if "length" not in h3_inputs or "vae" not in h3_inputs:
        raise VideoContextGraphError(
            "MiniMaxH3ReferenceToVideo is missing its length or VAE input"
        )
    guider_id = _consumer(filled, h3_id, 0, "BasicGuider", "conditioning")
    decode_id = _require_role(filled, "VAEDecode")
    audio_decode_id = _require_role(filled, "VAEDecodeAudio")
    create_id = _consumer(filled, decode_id, 0, "CreateVideo", "images")
    _consumer(filled, create_id, 0, "SaveVideo", "video")

    sampled = context_generation_frames(
        delivered_frames,
        context_frames,
        max_frames=MAX_FRAMES,
    )
    h3_inputs["length"] = sampled
    load_id = _add(
        filled,
        "LoadVideo",
        {"file": str(uploaded_video)},
        "Context Video",
    )
    components_id = _add(
        filled,
        "GetVideoComponents",
        {"video": [load_id, 0]},
        "Context Components",
    )
    motion_inputs: dict[str, Any] = {
        "conditioning": [h3_id, 0],
        "vae": _copy_link(h3_inputs["vae"]),
        "latent": [h3_id, 1],
        "context_length": str(int(context_frames)),
        "audio_context_length": int(audio_context_frames) if carry_audio else 0,
        "context_frames": [components_id, 0],
    }
    if carry_audio:
        if "audio_vae" not in h3_inputs:
            raise VideoContextGraphError(
                "Context audio requires the H3 audio VAE input"
            )
        motion_inputs["audio_vae"] = _copy_link(h3_inputs["audio_vae"])
        motion_inputs["context_audio"] = [components_id, 1]
    motion_id = _add(filled, "MiniMaxH3MotionContext", motion_inputs, "Motion Context")
    _inputs(filled, guider_id)["conditioning"] = [motion_id, 0]
    trim_id = _add(
        filled,
        "MiniMaxH3MotionContextTrim",
        {
            "images": [decode_id, 0],
            "audio": [audio_decode_id, 0],
            "trim_frames": [motion_id, 1],
            "fps": TARGET_FPS,
            "match_tail": True,
        },
        "Trim Context Overlap",
    )
    pictures_id = _add(
        filled,
        "ImageFromBatch",
        {
            "image": [trim_id, 0],
            "batch_index": 0,
            "length": int(delivered_frames),
        },
        "Delivered Frames",
    )
    sound_id = _add(
        filled,
        "TrimAudioDuration",
        {
            "audio": [trim_id, 1],
            "start_index": 0.0,
            "duration": int(delivered_frames) / TARGET_FPS,
        },
        "Delivered Audio",
    )
    create_inputs = _inputs(filled, create_id)
    create_inputs["images"] = [pictures_id, 0]
    create_inputs["audio"] = [sound_id, 0]
    return filled


def prepare_context_bytes(
    data: bytes,
    *,
    media_width: int,
    media_height: int,
    media_fps: float,
    media_duration_s: float,
    has_audio: bool,
    width: int,
    height: int,
    context_frames: int,
    carry_audio: bool,
    filename: str = "context.mp4",
) -> tuple[bytes, dict[str, Any] | None]:
    """Copy matching media. Normalize fps or size without stretching a new aspect."""
    from ...core.projects.video_context import VideoContextError

    if width <= 0 or height <= 0 or media_width <= 0 or media_height <= 0:
        raise VideoContextError("Target resolution is invalid")
    source_aspect = media_width / media_height
    target_aspect = width / height
    if abs(source_aspect - target_aspect) / target_aspect > ASPECT_TOLERANCE:
        raise VideoContextError(
            "Video context aspect ratio differs from the shot by more than 1%"
        )
    available_frames = int(media_duration_s * TARGET_FPS + 1e-4)
    if available_frames < int(context_frames):
        raise VideoContextError("Context video is shorter than the selected window")
    if carry_audio and not has_audio:
        raise VideoContextError(
            "Context audio was requested but the source video has no audio"
        )
    same_rate = abs(float(media_fps) - TARGET_FPS) <= 0.01
    if same_rate and media_width == width and media_height == height:
        return data, None
    converted = _transcode_context_video(
        data,
        filename=filename,
        width=width,
        height=height,
        keep_audio=has_audio,
    )
    return converted, {
        "from_width": media_width,
        "from_height": media_height,
        "to_width": width,
        "to_height": height,
        "from_fps": media_fps,
        "to_fps": TARGET_FPS,
    }


def _transcode_context_video(
    data: bytes,
    *,
    filename: str,
    width: int,
    height: int,
    keep_audio: bool,
) -> bytes:
    from ...core.projects.video_context import VideoContextError

    binary = shutil.which("ffmpeg")
    if not binary:
        raise VideoContextError("ffmpeg is required to normalize a context video")
    suffix = Path(filename).suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".webm"}:
        suffix = ".mp4"
    with tempfile.TemporaryDirectory(prefix="director-studio-context-") as temporary:
        root = Path(temporary)
        source = root / f"source{suffix}"
        target = root / "context.mp4"
        source.write_bytes(data)
        scale = (
            f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,"
            f"pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2,fps={int(TARGET_FPS)}"
        )
        command = [
            binary, "-y", "-v", "error",
            "-i", str(source),
            "-vf", scale,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
        ]
        if keep_audio:
            command.extend(["-c:a", "aac"])
        else:
            command.append("-an")
        command.append(str(target))
        try:
            subprocess.run(command, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise VideoContextError("Unable to normalize the context video") from exc
        if not target.is_file():
            raise VideoContextError("Unable to normalize the context video")
        return target.read_bytes()


def _require_role(graph: dict[str, Any], class_type: str) -> str:
    found = [
        str(node_id)
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]
    if len(found) != 1:
        raise VideoContextGraphError(
            f"video context requires exactly one {class_type}; found {len(found)}"
        )
    return found[0]


def _consumer(
    graph: dict[str, Any],
    source_id: str,
    output_index: int,
    class_type: str,
    input_name: str,
) -> str:
    found: list[str] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict) or node.get("class_type") != class_type:
            continue
        value = (node.get("inputs") or {}).get(input_name)
        if (
            isinstance(value, list)
            and len(value) >= 2
            and str(value[0]) == source_id
            and value[1] == output_index
        ):
            found.append(str(node_id))
    if len(found) != 1:
        raise VideoContextGraphError(
            f"video context requires one {class_type} {input_name} "
            f"from {source_id}; found {len(found)}"
        )
    return found[0]


def _inputs(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    node = graph.get(node_id)
    if not isinstance(node, dict):
        raise VideoContextGraphError(f"node {node_id} is missing")
    inputs = node.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise VideoContextGraphError(f"node {node_id} inputs are invalid")
    return inputs


def _add(graph: dict[str, Any], class_type: str, inputs: dict[str, Any], title: str) -> str:
    node_id = str(_next_node_id(graph))
    graph[node_id] = {
        "class_type": class_type,
        "inputs": inputs,
        "_meta": {"title": title},
    }
    return node_id


def _next_node_id(graph: dict[str, Any]) -> int:
    numeric: list[int] = []
    for key in graph:
        try:
            numeric.append(int(key))
        except (TypeError, ValueError):
            continue
    return (max(numeric) + 1) if numeric else 1


def _copy_link(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    return value
