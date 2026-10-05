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
    available_frames = int(media_duration_s * media_fps)
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
