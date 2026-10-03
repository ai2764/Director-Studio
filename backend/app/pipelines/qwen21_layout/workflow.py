"""Qwen Image 2.1 text-to-image and reference-to-image Layout graph."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

from ...config import settings
from ...core.schemas import ComfyImageRef

WORKFLOW_FILENAME = "qwen_image_21_layout.api.json"
MAX_REF_IMAGES = 3

NODE_UNET = "1"
NODE_CLIP = "2"
NODE_VAE = "3"
NODE_TEXT_ENCODE = "10"
NODE_SAVE = "14"
NODE_KSAMPLER = "12"
NODE_CACHE = "11"
NODE_DECODE = "13"
NODE_TEXT_ONLY_LATENT = "15"

LANDSCAPE_SIZE = (1536, 864)
PORTRAIT_SIZE = (864, 1536)

OUTPUT_LABELS = {"layout": "01 · Layout Reference Frame"}


def workflow_path() -> Path:
    return settings.workflows_dir / WORKFLOW_FILENAME


def minimal_graph() -> dict[str, Any]:
    return load_base_prompt()


def load_base_prompt() -> dict[str, Any]:
    path = workflow_path()
    if not path.exists():
        raise FileNotFoundError(f"Workflow API JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def workflow_file_valid() -> bool:
    try:
        graph = load_base_prompt()
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        return False
    expected = {
        NODE_UNET: "UNETLoader",
        NODE_CLIP: "CLIPLoader",
        NODE_VAE: "VAELoader",
        NODE_TEXT_ENCODE: "TextEncodeQwenImage21",
        NODE_CACHE: "QwenImage21Cache",
        NODE_KSAMPLER: "KSampler",
        NODE_DECODE: "VAEDecode",
        NODE_SAVE: "SaveImage",
    }
    return all(
        isinstance(graph.get(node_id), dict)
        and graph[node_id].get("class_type") == class_type
        for node_id, class_type in expected.items()
    )


def fill_layout_graph(graph: dict[str, Any], job_params: dict[str, Any]) -> dict[str, Any]:
    prompt = str(job_params.get("description") or "").strip()
    if not prompt:
        raise ValueError("description is required")
    images = list(job_params.get("images") or [])
    if len(images) > MAX_REF_IMAGES:
        raise ValueError(f"at most {MAX_REF_IMAGES} reference images allowed")

    filled = copy.deepcopy(graph)
    encoder = filled[NODE_TEXT_ENCODE].setdefault("inputs", {})
    encoder["prompt"] = prompt
    encoder["negative_prompt"] = str(job_params.get("negative_prompt") or "")
    encoder["clip"] = [NODE_CLIP, 0]
    encoder["vae"] = [NODE_VAE, 0]
    for key in list(encoder):
        if key.startswith("images.image_"):
            encoder.pop(key)

    for index, filename in enumerate(images, start=1):
        node_id = str(3 + index)
        filled[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": filename},
            "_meta": {"title": f"Layout Reference {index}"},
        }
        encoder[f"images.image_{index}"] = [node_id, 0]

    sampler = filled[NODE_KSAMPLER].setdefault("inputs", {})
    aspect_ratio = str(job_params.get("aspect_ratio") or "16:9").strip().lower()
    width, height = (
        PORTRAIT_SIZE
        if aspect_ratio in {"9:16", "portrait", "vertical"}
        else LANDSCAPE_SIZE
    )
    # Qwen's text encoder derives its latent size from the first reference.
    # Use the requested output canvas even when references are supplied.
    filled[NODE_TEXT_ONLY_LATENT] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": width, "height": height, "batch_size": 1},
        "_meta": {"title": f"Layout Canvas · {width}x{height}"},
    }
    latent_source: list[str | int] = [NODE_TEXT_ONLY_LATENT, 0]
    sampler.update(
        {
            "model": [NODE_CACHE, 0],
            "positive": [NODE_TEXT_ENCODE, 0],
            "negative": [NODE_TEXT_ENCODE, 1],
            "latent_image": latent_source,
            "steps": int(job_params.get("steps", 40)),
            "cfg": float(job_params.get("cfg", 1.0)),
            "sampler_name": str(job_params.get("sampler_name", "euler")),
            "scheduler": str(job_params.get("scheduler", "simple")),
            "denoise": float(job_params.get("denoise", 1.0)),
        }
    )
    if job_params.get("seed") is not None:
        sampler["seed"] = int(job_params["seed"])
    if job_params.get("output_prefix"):
        filled[NODE_SAVE].setdefault("inputs", {})["filename_prefix"] = str(
            job_params["output_prefix"]
        )
    return filled


def build_layout_prompt(
    *, description: str, image_names: list[str], seed: int | None = None,
    output_prefix: str | None = None, job_id: str | None = None,
    aspect_ratio: str | None = None,
) -> tuple[dict[str, Any], int]:
    resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
    if output_prefix is None and job_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:32]
        output_prefix = f"director-studio/{safe}/layout"
    filled = fill_layout_graph(
        load_base_prompt(),
        {"description": description, "images": image_names, "seed": resolved_seed,
         "output_prefix": output_prefix, "aspect_ratio": aspect_ratio},
    )
    return filled, resolved_seed


def map_history_outputs(history: dict[str, Any]) -> dict[str, ComfyImageRef]:
    outputs = history.get("outputs") or {}
    node_out = outputs.get(NODE_SAVE) or outputs.get(str(NODE_SAVE)) or {}
    images = node_out.get("images") or []
    if not images:
        return {}
    image = images[-1]
    return {"layout": ComfyImageRef(
        filename=image.get("filename") or "",
        subfolder=image.get("subfolder") or "",
        type=image.get("type") or "output",
    )}
