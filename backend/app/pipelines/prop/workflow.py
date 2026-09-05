"""Qwen Image Edit prep: one uploaded prop photo → multi-view H3 reference sheet."""

from __future__ import annotations

import copy
import json
import random
from typing import Any

from ...config import settings
from ...core.schemas import ComfyImageRef

WORKFLOW_FILENAME = "qwen_prop_master.api.json"
VISUAL_WORKFLOW_FILENAME = "DS_qwen_prop_master_visual.json"

NODE_CLIP = "2"
NODE_UNET = "3"
NODE_VAE = "4"
NODE_MULTI_ANGLE = "17"
NODE_LIGHTNING = "18"
NODE_MODEL_SAMPLING = "5"
NODE_EMPTY_LATENT = "6"
NODE_REF_IMAGE_1 = "7"
NODE_DESCRIPTION = "10"
NODE_FRONT_DESCRIPTION = "20"
NODE_SIDE_DESCRIPTION = "21"
NODE_SAMPLER = "11"
NODE_FRONT_SAMPLER = "23"
NODE_SIDE_SAMPLER = "24"
NODE_DECODE = "12"
NODE_SAVE = "13"
NODE_NEGATIVE = "14"
# Reference-frame leftovers; dedicated prop graph must not include these.
NODE_SCENE_ENCODE = "15"
NODE_SCENE_SCALE = "16"
NODE_REF_IMAGE_2 = "8"
NODE_REF_IMAGE_3 = "9"

DEFAULT_STEPS = 4
DEFAULT_CFG = 1.0
DEFAULT_SAMPLER = "euler"
DEFAULT_SCHEDULER = "simple"
DEFAULT_SHIFT = 3.1
EMPTY_LATENT_DENOISE = 1.0

PROP_WIDTH = 1536
PROP_HEIGHT = 1536

OUTPUT_LABELS = {
    "master": "01 · Prop Reference Sheet",
}

REF_IMAGE_NODES = (NODE_REF_IMAGE_1, NODE_REF_IMAGE_2, NODE_REF_IMAGE_3)
DESCRIPTION_NODES = (NODE_DESCRIPTION, NODE_FRONT_DESCRIPTION, NODE_SIDE_DESCRIPTION)
SAMPLER_NODES = (NODE_SAMPLER, NODE_FRONT_SAMPLER, NODE_SIDE_SAMPLER)


def workflow_path():
    return settings.workflows_dir / WORKFLOW_FILENAME


def visual_workflow_path():
    return settings.workflows_dir / VISUAL_WORKFLOW_FILENAME


def load_base_prompt() -> dict[str, Any]:
    path = workflow_path()
    if not path.exists():
        raise FileNotFoundError(f"Workflow API JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def compile_prop_instruction(*, name: str = "", notes: str = "", view: str = "") -> str:
    label = (name or "").strip() or "the uploaded object"
    extra = (notes or "").strip()
    notes_line = f" Extra identity notes: {extra}." if extra else ""
    return (
        f"Image 1 is a photograph of {label}.{notes_line} "
        f"Create ONE isolated product-reference image in a {view or 'hero three-quarter view'} "
        "for video generation. This single view will be composited downstream into a final "
        "Prop Reference Sheet. Do not create a sheet, collage, multi-panel image, labels, "
        "or multiple views in this image. "
        "Keep the exact same object in every view: identical silhouette, proportions, "
        "materials, colors, logos, labels, wear, and distinctive details; never create a "
        "different version of the object. Show the complete object where practical, sharp, "
        "and photoreal. Use a seamless warm-neutral studio "
        "background with soft even product lighting. No environment, no unrelated objects, "
        "no hands unless they are part of the object's identity, and no text labels. "
        "FORBIDDEN: different props, altered "
        "logos, watermark, text overlay, busy table, cropped-away logos, blurry, deformed, "
        "or duplicate mismatched objects."
    )


def fill_prop_graph(graph: dict[str, Any], job_params: dict[str, Any]) -> dict[str, Any]:
    image_name = str(job_params.get("image") or "").strip()
    if not image_name:
        raise ValueError("prop reference image is required")

    filled = copy.deepcopy(graph)
    for nid in (*DESCRIPTION_NODES, *SAMPLER_NODES, NODE_SAVE):
        if nid not in filled:
            raise RuntimeError(f"workflow missing node {nid}")

    instruction = (job_params.get("instruction") or "").strip()
    if not instruction:
        instruction = compile_prop_instruction(
            name=str(job_params.get("name") or ""),
            notes=str(job_params.get("notes") or ""),
        )

    view_instructions = (
        instruction,
        compile_prop_instruction(
            name=str(job_params.get("name") or ""),
            notes=str(job_params.get("notes") or ""),
            view="front view",
        ),
        compile_prop_instruction(
            name=str(job_params.get("name") or ""),
            notes=str(job_params.get("notes") or ""),
            view="side or rear view",
        ),
    )
    for nid, view_instruction in zip(DESCRIPTION_NODES, view_instructions):
        pos_inputs = filled[nid].setdefault("inputs", {})
        pos_inputs["prompt"] = view_instruction
        for key in ("image1", "image2", "image3"):
            pos_inputs.pop(key, None)

    filled[NODE_REF_IMAGE_1] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
        "_meta": {"title": "Prop photo"},
    }
    for nid in DESCRIPTION_NODES:
        filled[nid]["inputs"]["image1"] = [NODE_REF_IMAGE_1, 0]
    filled.pop(NODE_REF_IMAGE_2, None)
    filled.pop(NODE_REF_IMAGE_3, None)
    filled.pop(NODE_SCENE_ENCODE, None)
    filled.pop(NODE_SCENE_SCALE, None)

    neg = (
        "contact sheet, collage, multi-panel image, multiple views in one image, text labels, "
        "different props, inconsistent object versions, "
        "mismatched logos, mismatched labels, busy table, environment set, unrelated "
        "objects, watermark, text overlay, panel borders, logo bug, hands holding unless "
        "present in source, cropped logo, blurry, low quality, deformed objects"
    )
    if NODE_NEGATIVE in filled:
        filled[NODE_NEGATIVE].setdefault("inputs", {})["prompt"] = neg
        for key in ("image1", "image2", "image3"):
            filled[NODE_NEGATIVE]["inputs"].pop(key, None)

    width = int(job_params.get("width") or PROP_WIDTH)
    height = int(job_params.get("height") or PROP_HEIGHT)
    if NODE_EMPTY_LATENT not in filled:
        filled[NODE_EMPTY_LATENT] = {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
            "_meta": {"title": f"Empty Latent {width}x{height}"},
        }
    else:
        filled[NODE_EMPTY_LATENT].setdefault("inputs", {})
        filled[NODE_EMPTY_LATENT]["inputs"]["width"] = width
        filled[NODE_EMPTY_LATENT]["inputs"]["height"] = height

    seed = job_params.get("seed")
    for nid in SAMPLER_NODES:
        sampler_in = filled[nid].setdefault("inputs", {})
        sampler_in["latent_image"] = [NODE_EMPTY_LATENT, 0]
        sampler_in["denoise"] = float(job_params.get("denoise") or EMPTY_LATENT_DENOISE)
        sampler_in["steps"] = int(job_params.get("steps") or DEFAULT_STEPS)
        sampler_in["cfg"] = float(job_params.get("cfg") or DEFAULT_CFG)
        sampler_in["sampler_name"] = str(job_params.get("sampler_name") or DEFAULT_SAMPLER)
        sampler_in["scheduler"] = str(job_params.get("scheduler") or DEFAULT_SCHEDULER)
        if seed is not None:
            sampler_in["seed"] = int(seed)
    if NODE_MODEL_SAMPLING in filled:
        filled[NODE_MODEL_SAMPLING].setdefault("inputs", {})["shift"] = float(
            job_params.get("shift") or DEFAULT_SHIFT
        )
    output_prefix = job_params.get("output_prefix")
    if output_prefix:
        filled[NODE_SAVE].setdefault("inputs", {})["filename_prefix"] = str(output_prefix)
    return filled


def build_prop_prompt(
    *,
    image_name: str,
    name: str = "",
    notes: str = "",
    seed: int | None = None,
    output_prefix: str | None = None,
    job_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    image_name = (image_name or "").strip()
    if not image_name:
        raise ValueError("prop reference image is required")
    resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
    if output_prefix is None and job_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:32]
        output_prefix = f"director-studio/{safe}/master"
    elif output_prefix is None:
        output_prefix = "director-studio/prop/master"

    filled = fill_prop_graph(
        load_base_prompt(),
        {
            "image": image_name,
            "name": name,
            "notes": notes,
            "instruction": compile_prop_instruction(name=name, notes=notes),
            "seed": resolved_seed,
            "output_prefix": output_prefix,
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
            "master": ComfyImageRef(
                filename=img.get("filename") or "",
                subfolder=img.get("subfolder") or "",
                type=img.get("type") or "output",
            )
        }
    return {}
