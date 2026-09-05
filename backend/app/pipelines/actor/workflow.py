"""ComfyUI graph fill for Qwen actor asset workbench.

Policy (simple workbench path):
- Optional **actor reference image** is fed into **master (人像)** and **full-body three-view**.
- One description text (no separate body/hair authority tracks).
- Three-view = workbench multipanel once (image1=master, image2=actor ref).
- Bust three-view = crop of the multipanel sheet (no second KSampler).
"""

from __future__ import annotations

import copy
import json
import random
from typing import Any

from ...config import settings
from ...core.schemas import ComfyImageRef

# --- Inputs (user-facing) ---
NODE_DESCRIPTION = "58"  # Actor Description
NODE_BODY = "59"  # Body Description (optional)
NODE_HAIR = "60"  # Hairstyle description (optional, text authority)
NODE_ACTOR_IMAGE = "15"  # optional; blank 1x1 → text path
NODE_WARDROBE_IMAGE = "23"  # optional; blank 1x1 → keep original wardrobe
NODE_WARDROBE_EXTRACT_PROMPT = "50"
NODE_NEGATIVE = "11"
NODE_REF_BASE_PROMPT = "63"  # actor ref → master base

# Auto switches are driven by image size (width > 1); do not set manually:
# 22 actor source, 30 wardrobe apply, 56 extract wardrobe

# Bust sampler chain removed at fill time (no secondary sampling)
SEED_NODES = ("13", "20", "28", "44", "54")  # no "37" bust sampler
BUST_SAMPLER_NODES = ("33", "34", "35", "36", "37", "38")

SAVE_NODES = {
    "57": "wardrobe_ref",
    "31": "master",
    "39": "bust_threeview",
    "46": "fullbody_threeview",
    "48": "asset_sheet",
}

OUTPUT_LABELS = {
    "wardrobe_ref": "00 · Wardrobe Reference",
    "master": "01 · Actor Master",
    "bust_threeview": "02 · Bust Three-view",
    "fullbody_threeview": "03 · Full-body Three-view",
    "asset_sheet": "04 · Asset Sheet",
}

# ComfyUI input folder placeholder (1×1). Presence routing: width > 1 ⇒ real upload.
BLANK_IMAGE = "qwen_actor_asset_blank.ppm"

# Baseline negative from working job 008126d9, minus "side view" (kills 3/4 panels)
# and without footwear bans — shoes vs bare feet come from user description / master text.
DEFAULT_NEGATIVE = (
    "cropped body, multiple people, duplicate body, extra limbs, missing limbs, "
    "deformed hands, malformed fingers, deformed feet, "
    "dramatic pose, text, watermark, collage, cluttered background, blur, "
    "inconsistent hairstyle across panels, wrong rear hairstyle, "
    "inventing a bun when hair is described as loose, converting loose hair to updo"
)

DEFAULT_DESCRIPTION = (
    "Photorealistic professional actor casting reference, one adult, "
    "front-facing complete full-body studio portrait, eye-level camera, "
    "standing upright in a neutral relaxed symmetrical pose, both arms naturally at sides, "
    "entire body visible head to toe, plain seamless white studio background, "
    "soft even lighting, exactly one person, no text, no collage. "
    "Specify age, face vibe, hair, outfit, footwear, and body build in this text."
)

# Node ids for three-view base prompts in qwen_actor_asset_workbench.api.json
NODE_FULLBODY_THREEVIEW_PROMPT = "66"
NODE_BUST_THREEVIEW_PROMPT = "68"  # unused when bust is crop-only; kept for compatibility

# Workbench multipanel three-view (nodes 40–45, prompts 66–67). Kept intact.
# Dynamic per-view node ids (200–281) are stripped if present so we never dual-run.
_DYNAMIC_PER_VIEW_IDS = tuple(str(i) for i in range(200, 282))

# Workbench full-body three-view canvas (3 equal panels)
_TV_SHEET_W = 2880
_TV_SHEET_H = 1920
_TV_BUST_H = 960

# Master base (ref path). User Description is appended at build time — it controls
# outfit/footwear when stated (e.g. bare feet vs shoes). Do not hardcode footwear here.
REF_ACTOR_MASTER_PROMPT = (
    "Image 1 is the actor REFERENCE photo. Preserve the same person: face identity, "
    "hairstyle, hair color/length, body build as visible, age and overall look. "
    "Normalize into a photorealistic professional actor casting master: front-facing complete "
    "full-body studio portrait, eye-level, upright symmetrical stance, arms at sides, "
    "head to toe with margin, plain seamless white studio background, soft even lighting. "
    "If the reference is a close-up face only, invent a natural full body consistent with that "
    "face and the written description (do not invent a different person). "
    "Outfit and footwear: follow the USER DESCRIPTION below when it specifies clothing or "
    "bare feet / shoes; otherwise keep clear clothes from image 1, else simple neutral studio wear. "
    "Exactly one person, no text, no collage, no props."
)

# Alias kept for older tests/imports
REF_FACE_ONLY_MASTER_PROMPT = REF_ACTOR_MASTER_PROMPT

# Three-view: multipanel (008126d9 baseline). Footwear follows master (no forced shoes).
_FULLBODY_THREEVIEW_PROMPT_TEMPLATE = (
    "Image 1 is the actor MASTER (full-body front). "
    "Image 2 is the original actor REFERENCE photo — keep the same person identity "
    "(face, hair, body vibe) consistent with image 1 and image 2. "
    "Create one wide professional actor turnaround sheet: exactly THREE equal vertical panels "
    "with clean white separators — and ONLY three figures total (one per panel). "
    "FORBIDDEN: more than three people, extra clones, a row of extra backs, six-panel grids, "
    "duplicated bodies, or extra mini-figures between panels. "
    "Every panel: complete body head to toe, upright neutral pose, arms at sides. "
    "Keep the same hairstyle, clothing, body, and feet/footwear as the master across all panels. "
    "{headwear_instruction} "
    "LEFT: exact front view. CENTER: right-facing 45-degree three-quarter. "
    "RIGHT: exact back view. "
    "Same scale, eye-level, light-gray studio background, accurate hands and feet. "
    "No text, labels, crop, or clothing changes between panels."
)


def _fullbody_threeview_prompt(*, include_headwear: bool) -> str:
    headwear_instruction = (
        "The same hat or headwear visible on the master must appear in all three panels, "
        "with identical shape, color, trim, and placement"
        if include_headwear
        else "No hat or headwear in any panel"
    )
    return _FULLBODY_THREEVIEW_PROMPT_TEMPLATE.format(
        headwear_instruction=headwear_instruction
    )


FULLBODY_THREEVIEW_PROMPT = _fullbody_threeview_prompt(include_headwear=False)
BUST_THREEVIEW_PROMPT = (
    "Bust three-view is produced by cropping the full-body three-view (no second sample). "
    "Upper strip of the three panels: front | three-quarter | back head-and-shoulders."
)


def _strip_dynamic_per_view_nodes(prompt: dict[str, Any]) -> None:
    """Remove experimental per-view/hair-fix nodes if a graph was previously patched."""
    for nid in _DYNAMIC_PER_VIEW_IDS:
        prompt.pop(nid, None)


def _use_workbench_multipanel_threeview(
    prompt: dict[str, Any], *, include_headwear: bool
) -> None:
    """
    Keep qwen_actor_asset_workbench multipanel three-view:
      encode 40 (image1=master 30, image2=actor ref 15) → sample → decode 45 → save 46
    Bust = crop 32 from 45; no second bust sampler.
    """
    _strip_dynamic_per_view_nodes(prompt)
    for nid in BUST_SAMPLER_NODES:
        prompt.pop(nid, None)

    if NODE_FULLBODY_THREEVIEW_PROMPT in prompt:
        prompt[NODE_FULLBODY_THREEVIEW_PROMPT]["inputs"]["value"] = (
            _fullbody_threeview_prompt(include_headwear=include_headwear)
        )
    if NODE_BUST_THREEVIEW_PROMPT in prompt:
        prompt[NODE_BUST_THREEVIEW_PROMPT]["inputs"]["value"] = BUST_THREEVIEW_PROMPT

    # Master + original reference into three-view
    if "40" in prompt:
        prompt["40"]["inputs"]["image1"] = ["30", 0]
        prompt["40"]["inputs"]["image2"] = [NODE_ACTOR_IMAGE, 0]
        if "67" in prompt:
            prompt["40"]["inputs"]["prompt"] = ["67", 0]

    if "46" in prompt:
        prompt["46"]["inputs"]["images"] = ["45", 0]

    if "32" in prompt:
        prompt["32"]["inputs"]["image"] = ["45", 0]
        prompt["32"]["inputs"]["width"] = _TV_SHEET_W
        prompt["32"]["inputs"]["height"] = _TV_BUST_H
        prompt["32"]["inputs"]["x"] = 0
        prompt["32"]["inputs"]["y"] = 0
    if "39" in prompt:
        prompt["39"]["inputs"]["images"] = ["32", 0]
        prompt["39"]["_meta"] = {
            **(prompt["39"].get("_meta") or {}),
            "title": "Save Bust Three-View (crop, no resample)",
        }
    if "47" in prompt:
        prompt["47"]["inputs"]["image_1"] = ["32", 0]
        prompt["47"]["inputs"]["image_2"] = ["45", 0]


WARDROBE_EXTRACT_PROMPT = (
    "Extract the complete coordinated outfit from the person in the source image and present it "
    "as a clean product-style wardrobe reference on a plain neutral background. Preserve garment "
    "silhouette, construction, layers, material, colors, trim, patterns, closures, and visible wear. "
    "Exclude the person's face, hair, body, pose, hands, jewelry, handheld objects, and background. "
    "{headwear} {footwear} Exactly one coordinated wardrobe set, no person, no text, no collage."
)


def _wardrobe_extract_prompt(
    *, include_headwear: bool, include_footwear: bool
) -> str:
    headwear = (
        "Include clearly visible hat or headwear as part of the coordinated look; preserve its exact "
        "type, silhouette, material, color, trim, and placement."
        if include_headwear
        else "Exclude headwear."
    )
    footwear = (
        "Include clearly visible shoes or boots as one matched pair; preserve their exact category, "
        "shape, color, material, sole, heel, closures, and distinctive wear."
        if include_footwear
        else "Exclude shoes and other footwear."
    )
    return WARDROBE_EXTRACT_PROMPT.format(headwear=headwear, footwear=footwear)


def _wardrobe_transfer_prompt(
    *, include_headwear: bool, include_footwear: bool
) -> str:
    headwear = (
        "Apply the hat or headwear from image 2, preserving its exact design and fit; keep the actor's "
        "identity and visible hair consistent around and beneath it."
        if include_headwear
        else "Preserve the master hairstyle and do not add headwear."
    )
    footwear = (
        "Apply the exact footwear from image 2 as a matched pair, preserving its design, "
        "material, and color."
        if include_footwear
        else "Preserve image 1's feet and footwear; if the master is barefoot, keep it "
        "barefoot, and if it wears shoes, keep equivalent shoes."
    )
    return (
        "Image 1 is the target actor master — LOCK its face, identity, body proportions, and pose. "
        "Image 2 is a wardrobe reference containing garments and only the explicitly selected "
        "wearable accessories. Image 3 is the original actor reference; use it for identity only, "
        "preserving the same facial features and distinctive identity. Do not copy clothing, pose, "
        "framing, or background from image 3. Re-dress image 1 with the garments from image 2. "
        f"{headwear} {footwear} Ignore the clothing model's face, hair, body, and pose. "
        "Exactly one front-facing complete full-body actor, plain white studio background, no text."
    )

WORKFLOW_FILENAME = "qwen_actor_asset_workbench.api.json"

LEAF_PREFIX = {
    "wardrobe_ref": "00_wardrobe_reference",
    "master": "01_actor_master",
    "bust_threeview": "02_bust_threeview",
    "fullbody_threeview": "03_fullbody_threeview",
    "asset_sheet": "04_actor_asset_sheet",
}


def load_base_prompt() -> dict[str, Any]:
    path = settings.workflows_dir / WORKFLOW_FILENAME
    if not path.exists():
        path = settings.workflow_path
    if not path.exists():
        raise FileNotFoundError(f"Workflow API JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_actor_prompt(
    *,
    description: str = "",
    body_description: str = "",
    hair_description: str = "",
    negative_prompt: str = "",
    actor_image_name: str | None = None,
    wardrobe_image_name: str | None = None,
    include_headwear: bool = False,
    include_footwear: bool = False,
    seed: int | None = None,
    job_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """
    Patch workbench prompt.

    - No actor image → text-to-actor master
    - Actor image → ref into master (node 15→16) and into three-view (image2)
    - Single description text (legacy body/hair fields ignored / cleared)
    - Full-body three-view: workbench multipanel once
    - Bust three-view: crop of that sheet
    """
    prompt = copy.deepcopy(load_base_prompt())
    resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)

    # Single description — also injected into ref-path master (node 63), not only text path 58
    desc = (description or "").strip() or DEFAULT_DESCRIPTION
    prompt[NODE_DESCRIPTION]["inputs"]["value"] = desc
    prompt[NODE_BODY]["inputs"]["value"] = ""
    prompt[NODE_HAIR]["inputs"]["value"] = ""
    prompt[NODE_NEGATIVE]["inputs"]["text"] = negative_prompt or DEFAULT_NEGATIVE

    # Master base + user description (outfit/footwear follow description when stated)
    if NODE_REF_BASE_PROMPT in prompt:
        prompt[NODE_REF_BASE_PROMPT]["inputs"]["value"] = (
            f"{REF_ACTOR_MASTER_PROMPT}\n\nUSER DESCRIPTION:\n{desc}"
        )

    # Neutral concat delimiters (body/hair nodes empty; description already in 63)
    for nid in ("61", "62", "64", "65", "67", "69"):
        if nid in prompt:
            prompt[nid]["inputs"]["delimiter"] = "\n\n"

    if "24" in prompt:
        prompt["24"]["inputs"]["prompt"] = _wardrobe_transfer_prompt(
            include_headwear=include_headwear,
            include_footwear=include_footwear,
        )
        prompt["24"]["inputs"]["image3"] = [NODE_ACTOR_IMAGE, 0]
    if NODE_WARDROBE_EXTRACT_PROMPT in prompt:
        prompt[NODE_WARDROBE_EXTRACT_PROMPT]["inputs"]["prompt"] = (
            _wardrobe_extract_prompt(
                include_headwear=include_headwear,
                include_footwear=include_footwear,
            )
        )

    # Reference image → master path (15) and three-view image2 (same load node)
    prompt[NODE_ACTOR_IMAGE]["inputs"]["image"] = actor_image_name or BLANK_IMAGE
    prompt[NODE_WARDROBE_IMAGE]["inputs"]["image"] = wardrobe_image_name or BLANK_IMAGE

    # Ensure master ref encode uses actor image
    if "16" in prompt:
        prompt["16"]["inputs"]["image1"] = [NODE_ACTOR_IMAGE, 0]

    _use_workbench_multipanel_threeview(
        prompt, include_headwear=include_headwear
    )

    for nid in SEED_NODES:
        if nid in prompt:
            prompt[nid]["inputs"]["seed"] = resolved_seed

    if job_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:32]
        for nid, key in SAVE_NODES.items():
            if nid in prompt:
                prompt[nid]["inputs"]["filename_prefix"] = (
                    f"director-studio/{safe}/{LEAF_PREFIX.get(key, key)}"
                )

    return prompt, resolved_seed


def map_history_outputs(history: dict[str, Any]) -> dict[str, ComfyImageRef]:
    outputs = history.get("outputs") or {}
    mapped: dict[str, ComfyImageRef] = {}
    for nid, key in SAVE_NODES.items():
        node_out = outputs.get(nid) or outputs.get(str(nid))
        if not node_out:
            continue
        images = node_out.get("images") or []
        if not images:
            continue
        img = images[-1]
        mapped[key] = ComfyImageRef(
            filename=img.get("filename") or "",
            subfolder=img.get("subfolder") or "",
            type=img.get("type") or "output",
        )
    return mapped


def derive_mode(*, has_actor_ref: bool, has_wardrobe_ref: bool) -> str:
    """Display helper only — not sent to Comfy switches."""
    if has_wardrobe_ref:
        return "wardrobe"
    if has_actor_ref:
        return "reference"
    return "text"
