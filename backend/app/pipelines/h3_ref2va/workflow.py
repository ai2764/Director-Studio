"""Fill the official ComfyUI MiniMax H3 Ref2AV workflow for one job."""

from __future__ import annotations

import copy
import json
import random
import re
from typing import Any

from ...config import settings
from ...core.h3.frames import validate_frame_count
from ...core.h3.prompt import validate_h3_prompt
from ...core.schemas import ComfyImageRef
from ...workflow_profiles.h3 import (
    H3BoundaryMapping,
    ResolvedH3Profile,
    resolve_active_h3_profile,
)

H3_REF_NODE = "MiniMaxH3ReferenceToVideo"
H3_I2V_NODE = "MiniMaxH3ImageToVideo"
WORKFLOW_FILENAME = "h3_ref2va.api.json"

MAX_REF_IMAGES = 9
MAX_REF_AUDIOS = 3
DEFAULT_STEPS = 20
DEFAULT_SCHEDULER = "simple"
DEFAULT_SAMPLER = "res_multistep"
DEFAULT_REF_IMAGE_SIZE = "match"
DEFAULT_WIDTH = 864
DEFAULT_HEIGHT = 480
MAX_COMFY_SEED = 2**64 - 1

OUTPUT_LABELS = {"video": "H3 Ref2AV Video"}

# Node ids in Comfy-Org's published video_minimax_h3_r2v template. Runtime
# injection discovers boundary nodes by class type; the saver id is retained
# only so completed Comfy history can be mapped back to the UI deterministically.
NODE_H3 = "136"
NODE_SAVE = "92"
NODE_NOISE = "129"


def minimal_graph() -> dict[str, Any]:
    """Small official-shaped graph used by unit tests."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "2": {"class_type": "CLIPLoader", "inputs": {}},
        "3": {"class_type": "VAELoader", "inputs": {}},
        "4": {"class_type": "VAELoader", "inputs": {}},
        "10": {
            "class_type": H3_REF_NODE,
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "audio_vae": ["4", 0],
                "ref_image_size": DEFAULT_REF_IMAGE_SIZE,
            },
        },
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        "13": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": DEFAULT_SAMPLER},
        },
        "14": {
            "class_type": "BasicScheduler",
            "inputs": {
                "scheduler": DEFAULT_SCHEDULER,
                "steps": DEFAULT_STEPS,
                "denoise": 1.0,
            },
        },
        "19": {"class_type": "SaveVideo", "inputs": {}},
    }


def load_base_prompt() -> dict[str, Any]:
    path = settings.workflows_dir / WORKFLOW_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"Workflow API JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _node_ids_by_class(graph: dict[str, Any], class_type: str) -> list[str]:
    return [
        str(node_id)
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


def _require_unique_node_id(graph: dict[str, Any], class_type: str) -> str:
    node_ids = _node_ids_by_class(graph, class_type)
    if len(node_ids) != 1:
        raise RuntimeError(
            f"official H3 workflow must contain exactly one {class_type} node; "
            f"found {len(node_ids)}"
        )
    return node_ids[0]


def _next_node_id(graph: dict[str, Any]) -> int:
    ids: list[int] = []
    for key in graph:
        try:
            ids.append(int(key))
        except (TypeError, ValueError):
            continue
    return (max(ids) + 1) if ids else 1


def _assert_pure_ref2va(graph: dict[str, Any]) -> None:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == H3_I2V_NODE:
            raise ValueError(
                f"{H3_I2V_NODE} is not allowed on pure Ref2AV pipeline "
                "(no first/last frame I2V path)"
            )
        inputs = node.get("inputs") or {}
        if "ref_frame" in inputs or "last_frame" in inputs:
            raise ValueError(
                "ref_frame/last_frame sockets are not allowed on pure Ref2AV pipeline"
            )


def _dynamic_input_regex(pattern: str) -> re.Pattern[str]:
    if pattern.count("{index}") != 1:
        raise ValueError("dynamic input pattern must contain exactly one {index}")
    before, after = pattern.split("{index}")
    return re.compile(rf"{re.escape(before)}\d+{re.escape(after)}\Z")


def fill_profile_graph(
    profile: ResolvedH3Profile,
    job_params: dict[str, Any],
) -> dict[str, Any]:
    """Fill only the application-owned boundary declared by an H3 profile."""
    images = list(job_params.get("images") or [])
    audios = list(job_params.get("audios") or [])
    if len(images) > MAX_REF_IMAGES:
        raise ValueError(f"at most {MAX_REF_IMAGES} images allowed for H3 Ref2VA")
    if len(audios) > MAX_REF_AUDIOS:
        raise ValueError(f"at most {MAX_REF_AUDIOS} audios allowed for H3 Ref2VA")
    if str(job_params.get("native_audio") or "").strip():
        raise ValueError(
            "native audio lock is not part of the official ComfyUI workflow"
        )

    prompt = job_params.get("prompt") or ""
    if "dialogue" in job_params:
        dialogue = job_params.get("dialogue") or []
        if not isinstance(dialogue, list):
            raise ValueError("dialogue must be a list of strings")
        validate_h3_prompt(
            prompt,
            [str(item) for item in dialogue],
            audio_count=len(audios),
            submitted_picture_indices=range(1, len(images) + 1),
        )

    frames = job_params.get("frames")
    if frames is None:
        raise ValueError("frames is required")
    frames = validate_frame_count(int(frames))

    filled = copy.deepcopy(profile.workflow)
    _assert_pure_ref2va(filled)
    binding = profile.mapping
    h3_inputs = filled[binding.h3_node_id].setdefault("inputs", {})
    dynamic_patterns = [_dynamic_input_regex(binding.picture_input_pattern)]
    dynamic_patterns.append(_dynamic_input_regex("ref_audios.ref_audio_{index}"))
    if binding.audio_input_pattern is not None:
        dynamic_patterns.append(_dynamic_input_regex(binding.audio_input_pattern))
    for key in list(h3_inputs):
        if any(
            pattern.fullmatch(key) for pattern in dynamic_patterns
        ) or key.startswith(("ref_videos.", "ref_video_audios.")):
            del h3_inputs[key]

    width = int(job_params.get("width") or DEFAULT_WIDTH)
    height = int(job_params.get("height") or DEFAULT_HEIGHT)
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    seed = job_params.get("seed")
    if seed is not None:
        seed = int(seed)
        if seed < 0 or seed > MAX_COMFY_SEED:
            raise ValueError(f"seed must be in [0, {MAX_COMFY_SEED}]")

    h3_inputs[binding.prompt_input] = prompt
    h3_inputs[binding.width_input] = width
    h3_inputs[binding.height_input] = height
    h3_inputs[binding.frames_input] = frames

    next_id = _next_node_id(filled)
    for index, image_name in enumerate(images):
        node_id = str(next_id)
        next_id += 1
        filled[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
            "_meta": {"title": f"Ref Image {index}"},
        }
        input_name = binding.picture_input_pattern.format(index=index)
        h3_inputs[input_name] = [node_id, 0]

    if audios and binding.audio_input_pattern is None:
        raise ValueError("active H3 workflow profile does not support reference audio")
    for index, audio_name in enumerate(audios):
        node_id = str(next_id)
        next_id += 1
        filled[node_id] = {
            "class_type": "LoadAudio",
            "inputs": {"audio": audio_name},
            "_meta": {"title": f"Ref Audio {index}"},
        }
        input_name = binding.audio_input_pattern.format(index=index)
        h3_inputs[input_name] = [node_id, 0]

    if seed is not None:
        filled[binding.seed_node_id].setdefault("inputs", {})[binding.seed_input] = seed
    output_prefix = job_params.get("output_prefix")
    if output_prefix:
        filled[binding.saver_node_id].setdefault("inputs", {})[
            binding.output_prefix_input
        ] = str(output_prefix)

    return filled


def fill_ref2va_graph(
    graph: dict[str, Any], job_params: dict[str, Any]
) -> dict[str, Any]:
    """Backward-compatible fill for an official-shaped Ref2AV graph."""
    mapping = H3BoundaryMapping(
        h3_node_id=_require_unique_node_id(graph, H3_REF_NODE),
        prompt_input="prompt",
        width_input="width",
        height_input="height",
        frames_input="length",
        picture_input_pattern="ref_images.ref_image_{index}",
        audio_input_pattern="ref_audios.ref_audio_{index}",
        seed_node_id=_require_unique_node_id(graph, "RandomNoise"),
        seed_input="noise_seed",
        saver_node_id=_require_unique_node_id(graph, "SaveVideo"),
        output_prefix_input="filename_prefix",
    )
    return fill_profile_graph(
        ResolvedH3Profile(
            profile_id="legacy-official-graph",
            workflow=graph,
            mapping=mapping,
            workflow_sha256="",
            source="builtin",
        ),
        job_params,
    )


def build_ref2va_prompt(
    *,
    prompt: str,
    dialogue: list[str] | None = None,
    image_names: list[str],
    audio_names: list[str] | None = None,
    native_audio_name: str | None = None,
    frames: int,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    seed: int | None = None,
    output_prefix: str | None = None,
    job_id: str | None = None,
    profile: ResolvedH3Profile | None = None,
) -> tuple[dict[str, Any], int]:
    """Fill a resolved profile graph and return it with the job's concrete seed."""
    resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
    if output_prefix is None and job_id:
        safe = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in job_id
        )[:32]
        output_prefix = f"director-studio/{safe}/h3_ref2va"
    elif output_prefix is None:
        output_prefix = "director-studio/h3_ref2va"

    filled = fill_profile_graph(
        profile or resolve_active_h3_profile(),
        {
            "prompt": prompt,
            "dialogue": list(dialogue or []),
            "images": list(image_names),
            "audios": list(audio_names or []),
            "native_audio": native_audio_name,
            "frames": frames,
            "width": width,
            "height": height,
            "seed": resolved_seed,
            "output_prefix": output_prefix,
        },
    )
    return filled, resolved_seed


def map_history_outputs(
    history: dict[str, Any],
    *,
    profile: ResolvedH3Profile | None = None,
) -> dict[str, ComfyImageRef]:
    """Map the profile's declared saver output to logical key ``video``."""
    outputs = history.get("outputs") or {}
    saver_id = profile.mapping.saver_node_id if profile else NODE_SAVE
    output_fields = profile.mapping.output_fields if profile else ("videos",)
    node_output = outputs.get(saver_id) or outputs.get(str(saver_id))
    if not isinstance(node_output, dict):
        return {}
    for field in output_fields:
        items = node_output.get(field) or []
        if items:
            media = items[-1]
            return {
                "video": ComfyImageRef(
                    filename=media.get("filename") or "",
                    subfolder=media.get("subfolder") or "",
                    type=media.get("type") or "output",
                )
            }
    return {}
