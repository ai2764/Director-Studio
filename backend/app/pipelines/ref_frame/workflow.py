"""ComfyUI graph fill for Qwen 2511 layout reference frames.

The Qwen VL encoder sees up to three images for semantic understanding while
full-resolution VAE latents are attached separately through ReferenceLatent.
This avoids the encoder node's fixed ~1MP AREA-resized latent path.
"""

from __future__ import annotations

import copy
import json
import random
from typing import Any

from ...config import settings
from ...core.schemas import ComfyImageRef

WORKFLOW_FILENAME = "ref_frame_layout.api.json"

MAX_REF_IMAGES = 3

# workflows/ref_frame_layout.api.json
NODE_CLIP = "2"
NODE_UNET = "3"
NODE_VAE = "4"
NODE_MODEL_SAMPLING = "5"
NODE_EMPTY_LATENT = "6"
NODE_REF_IMAGE_1 = "7"
NODE_REF_IMAGE_2 = "8"
NODE_REF_IMAGE_3 = "9"
NODE_DESCRIPTION = "10"  # positive TextEncodeQwenImageEditPlus
NODE_SAMPLER = "11"
NODE_DECODE = "12"
NODE_SAVE = "13"
NODE_NEGATIVE = "14"
NODE_SCENE_ENCODE = "15"  # VAEEncode of scene (image1) → lock environment
NODE_SCENE_SCALE = "16"  # ImageScale scene plate → target res before encode
NODE_LIGHTNING_LORA = "17"

NODE_REF_METHOD = "20"
NODE_ZERO_NEGATIVE = "21"
NODE_REF_ENCODE_BASE = 30
NODE_REF_FIRST_PASS_BASE = 40
NODE_REF_SECOND_PASS_BASE = 50

REF_IMAGE_NODES = (NODE_REF_IMAGE_1, NODE_REF_IMAGE_2, NODE_REF_IMAGE_3)

# Fixed quality canvas. Both dimensions are multiples of 32, as required by the
# Qwen image latent path. Only the scene plate is fitted to this canvas; actor
# and prop references retain their native geometry.
LAYOUT_WIDTH = 1728
LAYOUT_HEIGHT = 960
PORTRAIT_LAYOUT_WIDTH = LAYOUT_HEIGHT
PORTRAIT_LAYOUT_HEIGHT = LAYOUT_WIDTH

# Legacy overrides retained for job compatibility. Quality reference mode uses
# the first full-resolution reference latent at denoise 1.0 for every ref count.
SCENE_ONLY_DENOISE = 0.55
SCENE_WITH_CHARACTER_DENOISE = 1.0
EMPTY_LATENT_DENOISE = 1.0
SCENE_INIT_DENOISE = SCENE_ONLY_DENOISE

# LightX2V's official 2511 Lightning recipe: 4-step distilled LoRA, CFG 1.0.
DEFAULT_SAMPLER = "euler"
DEFAULT_SCHEDULER = "simple"
DEFAULT_STEPS = 4
DEFAULT_CFG = 1.0
DEFAULT_SHIFT = 3.1
LIGHTNING_LORA_NAME = (
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
)

OUTPUT_LABELS = {
    "layout": "01 · Layout Reference Frame",
}


def minimal_graph() -> dict[str, Any]:
    """Stub for unit tests — mirrors production node ids."""
    return load_base_prompt()


def workflow_path():
    return settings.workflows_dir / WORKFLOW_FILENAME


def workflow_file_valid() -> bool:
    path = workflow_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or not data:
        return False
    for nid in (NODE_DESCRIPTION, NODE_SAMPLER, NODE_SAVE):
        node = data.get(nid)
        if not isinstance(node, dict) or not node.get("class_type"):
            return False
    return True


def load_base_prompt() -> dict[str, Any]:
    path = workflow_path()
    if not path.exists():
        raise FileNotFoundError(f"Workflow API JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fill_layout_graph(graph: dict[str, Any], job_params: dict[str, Any]) -> dict[str, Any]:
    description = (job_params.get("description") or "").strip()
    if not description:
        raise ValueError("description is required")

    images = list(job_params.get("images") or [])
    if len(images) > MAX_REF_IMAGES:
        raise ValueError(f"at most {MAX_REF_IMAGES} reference images allowed")

    filled = copy.deepcopy(graph)
    for nid in (NODE_DESCRIPTION, NODE_SAMPLER, NODE_SAVE):
        if nid not in filled:
            raise RuntimeError(f"workflow missing node {nid}")

    ref_labels = job_params.get("ref_labels") or []
    # The visual Agent owns the positive prompt. Keep negatives and graph wiring separate.
    filled[NODE_DESCRIPTION].setdefault("inputs", {})["prompt"] = description
    filled[NODE_DESCRIPTION]["inputs"]["clip"] = [NODE_CLIP, 0]
    # Keep the VL image path for semantic understanding, but do not let this node
    # build its own ~1MP AREA-resized reference latents. Full-resolution latents
    # are attached below with VAEEncode + ReferenceLatent.
    filled[NODE_DESCRIPTION]["inputs"].pop("vae", None)

    # Text-only fallback for the unsupported no-reference diagnostic path. Real
    # reference-frame jobs use ConditioningZeroOut with the same ref latents.
    neg = (
        "character sheet, turnaround, three-view, multi-panel, triptych, front side back, "
        "model sheet, same person repeated, split screen, contact sheet, "
        "poster on glass, sticker on door, cutout pasted on wall, floating person, "
        "person as reflection only, flat cardboard cutout, billboard on door, "
        "pure black void, studio seamless, product photography, catalog shot, "
        "melted walls, wavy texture, oily artifacts, plastic skin, oversmoothed, "
        "blurry, low quality, soft focus, muddy colors, jpeg artifacts, noise banding, "
        "deformed anatomy, extra limbs, watermark, text overlay, UI, logo"
    )
    if NODE_NEGATIVE in filled:
        filled[NODE_NEGATIVE].setdefault("inputs", {})["prompt"] = neg
        # Negative should not receive reference images (avoids locking sheet layout)
        for key in ("image1", "image2", "image3"):
            filled[NODE_NEGATIVE]["inputs"].pop(key, None)
        filled[NODE_NEGATIVE]["inputs"]["clip"] = [NODE_CLIP, 0]
        filled[NODE_NEGATIVE]["inputs"]["vae"] = [NODE_VAE, 0]

    # Wire up to 3 refs into EditPlus image1/2/3; drop unused LoadImage nodes
    # Order is caller-defined: prefer scene, then actor identity stills (not prop product shots).
    pos_inputs = filled[NODE_DESCRIPTION]["inputs"]
    for key in ("image1", "image2", "image3"):
        pos_inputs.pop(key, None)

    used_ref_nids: set[str] = set()
    for i, image_name in enumerate(images[:3]):
        ref_nid = REF_IMAGE_NODES[i]
        used_ref_nids.add(ref_nid)
        title = (
            str(ref_labels[i])
            if isinstance(ref_labels, list) and i < len(ref_labels)
            else f"Ref Image {i + 1}"
        )
        filled[ref_nid] = {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
            "_meta": {"title": title[:80]},
        }
        pos_inputs[f"image{i + 1}"] = [ref_nid, 0]

    for ref_nid in REF_IMAGE_NODES:
        if ref_nid not in used_ref_nids:
            filled.pop(ref_nid, None)

    # Sampling defaults are shared by both the quality path and no-ref fallback.
    sampler_in = filled[NODE_SAMPLER].setdefault("inputs", {})
    # Reference frames are a fixed quality tier. Legacy width/height job values
    # are intentionally ignored so old clients cannot silently lower resolution.
    aspect_ratio = str(job_params.get("aspect_ratio") or "").strip().lower()
    if aspect_ratio in {"9:16", "portrait", "vertical"}:
        width = PORTRAIT_LAYOUT_WIDTH
        height = PORTRAIT_LAYOUT_HEIGHT
    else:
        width = LAYOUT_WIDTH
        height = LAYOUT_HEIGHT
    sampler_in["steps"] = int(job_params.get("steps", DEFAULT_STEPS))
    sampler_in["cfg"] = float(job_params.get("cfg", DEFAULT_CFG))
    sampler_in["sampler_name"] = str(
        job_params.get("sampler_name", DEFAULT_SAMPLER)
    )
    sampler_in["scheduler"] = str(job_params.get("scheduler", DEFAULT_SCHEDULER))

    if NODE_MODEL_SAMPLING in filled:
        filled[NODE_MODEL_SAMPLING].setdefault("inputs", {})["shift"] = float(
            job_params.get("shift", DEFAULT_SHIFT)
        )

    filled[NODE_LIGHTNING_LORA] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": [NODE_MODEL_SAMPLING, 0],
            "lora_name": LIGHTNING_LORA_NAME,
            "strength_model": 1.0,
        },
        "_meta": {"title": "Qwen Image Edit 2511 Lightning · 4 steps"},
    }
    sampler_in["model"] = [NODE_LIGHTNING_LORA, 0]

    if images:
        filled.pop(NODE_NEGATIVE, None)
        filled.pop(NODE_EMPTY_LATENT, None)
        filled.pop(NODE_SCENE_ENCODE, None)

        filled[NODE_SCENE_SCALE] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": [NODE_REF_IMAGE_1, 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
            "_meta": {"title": f"Output Canvas · {width}x{height}"},
        }

        encode_ids: list[str] = []
        for i in range(len(images)):
            encode_id = str(NODE_REF_ENCODE_BASE + i)
            encode_ids.append(encode_id)
            filled[encode_id] = {
                "class_type": "VAEEncode",
                "inputs": {
                    "pixels": (
                        [NODE_SCENE_SCALE, 0]
                        if i == 0
                        else [REF_IMAGE_NODES[i], 0]
                    ),
                    "vae": [NODE_VAE, 0],
                },
                "_meta": {"title": f"Full Resolution Reference Latent {i + 1}"},
            }

        filled[NODE_REF_METHOD] = {
            "class_type": "FluxKontextMultiReferenceLatentMethod",
            "inputs": {
                "conditioning": [NODE_DESCRIPTION, 0],
                "reference_latents_method": "index_timestep_zero",
            },
            "_meta": {"title": "Qwen 2511 Reference Method"},
        }

        # Negative gets every reference once. The positive quality path repeats
        # them once more, matching the tested community double-reference graph.
        current = NODE_REF_METHOD
        for i, encode_id in enumerate(encode_ids):
            node_id = str(NODE_REF_FIRST_PASS_BASE + i)
            filled[node_id] = {
                "class_type": "ReferenceLatent",
                "inputs": {
                    "conditioning": [current, 0],
                    "latent": [encode_id, 0],
                },
                "_meta": {"title": f"Reference {i + 1} · Pass 1"},
            }
            current = node_id

        filled[NODE_ZERO_NEGATIVE] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": [current, 0]},
            "_meta": {"title": "Zero Prompt, Preserve Reference Latents"},
        }

        for i, encode_id in enumerate(encode_ids):
            node_id = str(NODE_REF_SECOND_PASS_BASE + i)
            filled[node_id] = {
                "class_type": "ReferenceLatent",
                "inputs": {
                    "conditioning": [current, 0],
                    "latent": [encode_id, 0],
                },
                "_meta": {"title": f"Reference {i + 1} · Quality Pass 2"},
            }
            current = node_id

        sampler_in["positive"] = [current, 0]
        sampler_in["negative"] = [NODE_ZERO_NEGATIVE, 0]
        sampler_in["latent_image"] = [encode_ids[0], 0]
        sampler_in["denoise"] = float(
            job_params.get("reference_denoise", EMPTY_LATENT_DENOISE)
        )
    else:
        filled.pop(NODE_SCENE_ENCODE, None)
        filled.pop(NODE_SCENE_SCALE, None)
        if NODE_EMPTY_LATENT not in filled:
            filled[NODE_EMPTY_LATENT] = {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                },
                "_meta": {"title": f"Empty Latent {width}x{height}"},
            }
        else:
            filled[NODE_EMPTY_LATENT].setdefault("inputs", {})
            filled[NODE_EMPTY_LATENT]["inputs"]["width"] = width
            filled[NODE_EMPTY_LATENT]["inputs"]["height"] = height
        sampler_in["latent_image"] = [NODE_EMPTY_LATENT, 0]
        sampler_in["denoise"] = float(
            job_params.get("empty_denoise", EMPTY_LATENT_DENOISE)
        )

    seed = job_params.get("seed")
    if seed is not None:
        sampler_in["seed"] = int(seed)

    output_prefix = job_params.get("output_prefix")
    if output_prefix:
        filled[NODE_SAVE].setdefault("inputs", {})["filename_prefix"] = str(output_prefix)

    return filled


def build_layout_prompt(
    *,
    description: str,
    image_names: list[str],
    seed: int | None = None,
    output_prefix: str | None = None,
    job_id: str | None = None,
    ref_labels: list[str] | None = None,
    aspect_ratio: str | None = None,
) -> tuple[dict[str, Any], int]:
    resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
    if output_prefix is None and job_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:32]
        output_prefix = f"director-studio/{safe}/ref_frame"
    elif output_prefix is None:
        output_prefix = "director-studio/ref_frame"

    base = load_base_prompt()
    filled = fill_layout_graph(
        base,
        {
            "description": description,
            "images": list(image_names),
            "seed": resolved_seed,
            "output_prefix": output_prefix,
            "ref_labels": list(ref_labels or []),
            "aspect_ratio": aspect_ratio,
        },
    )
    return filled, resolved_seed


def map_history_outputs(history: dict[str, Any]) -> dict[str, ComfyImageRef]:
    outputs = history.get("outputs") or {}
    candidates: list[str] = []
    if NODE_SAVE in outputs or str(NODE_SAVE) in outputs:
        candidates.append(NODE_SAVE)
    candidates.extend(str(k) for k in outputs.keys() if str(k) not in candidates)

    for nid in candidates:
        node_out = outputs.get(nid) or outputs.get(str(nid)) or {}
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images") or []
        if not images:
            continue
        img = images[-1]
        return {
            "layout": ComfyImageRef(
                filename=img.get("filename") or "",
                subfolder=img.get("subfolder") or "",
                type=img.get("type") or "output",
            )
        }
    return {}
