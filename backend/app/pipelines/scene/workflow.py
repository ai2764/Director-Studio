from __future__ import annotations

import copy
import json
import random
import re
from typing import Any

from ...config import settings
from ...core.schemas import ComfyImageRef

NODE_SCENE_IMAGE = "41"
NODE_ANGLES = "121"  # StringConstantMultiline
NODE_PROMPT_LIST = "113"  # CR Prompt List
NODE_SAMPLER = "106"
NODE_SAVE = "9"
NODE_SCENE_ENCODE = "105"
NODE_SCENE_SCALE = "107"
NODE_NEGATIVE = "96"
NODE_LIGHTNING = "102"

WORKFLOW_FILENAME = "QwenEdit2511_MultiAngle_SceneRef.api.json"
VISUAL_WORKFLOW_FILENAME = "DS_qwen_scene_multiangle_visual.json"

DEFAULT_ANGLES = (
    "left side view, eye level, medium shot (horizontal: 270, vertical: 0, zoom: 5.0)\n"
    "back view, eye level, medium shot (horizontal: 180, vertical: 0, zoom: 5.0)\n"
    "right side view, eye level, medium shot (horizontal: 90, vertical: 0, zoom: 5.0)\n"
    "front-left view, eye level, medium shot (horizontal: 315, vertical: 0, zoom: 5.0)\n"
    "front-right view, eye level, medium shot (horizontal: 45, vertical: 0, zoom: 5.0)\n"
    "front view, bird's eye view, medium shot (horizontal: 0, vertical: 45, zoom: 5.0)\n"
    "front view, low angle, medium shot (horizontal: 0, vertical: -30, zoom: 5.0)"
)

SCENE_WIDTH = 1728
SCENE_HEIGHT = 960

DEFAULT_PREPEND = (
    "Keep the same location, architecture, materials, furniture layout, lighting, "
    "and time of day. Only change the camera. Do not redesign the set."
)

_H_RE = re.compile(r"horizontal:\s*(-?\d+(?:\.\d+)?)", re.I)
_V_RE = re.compile(r"vertical:\s*(-?\d+(?:\.\d+)?)", re.I)
_Z_RE = re.compile(r"zoom:\s*(-?\d+(?:\.\d+)?)", re.I)


def workflow_path():
    return settings.workflows_dir / WORKFLOW_FILENAME


def visual_workflow_path():
    return settings.workflows_dir / VISUAL_WORKFLOW_FILENAME


def load_base_prompt() -> dict[str, Any]:
    path = workflow_path()
    if not path.exists():
        raise FileNotFoundError(f"Workflow API JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_angle_lines(angle_prompts: str) -> list[str]:
    return [ln.strip() for ln in (angle_prompts or "").splitlines() if ln.strip()]


def _slug_token(text: str, *, max_len: int = 40, lower: bool = True) -> str:
    s = text.strip()
    if lower:
        s = s.lower()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s or "angle")[:max_len]


def scene_name_slug(name: str) -> str:
    """User-facing scene title → filesystem prefix (case-preserving)."""
    s = (name or "").strip()
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("._-")
    return (s or "scene")[:64]


def angle_view_name(line: str) -> str:
    """Human short view name from the first clause, e.g. 'left side view'."""
    return (line.split(",")[0].strip() or "angle")[:48]


def angle_view_suffix(line: str, index: int) -> str:
    """
    Viewpoint suffix only (no scene name).

    Examples:
      01_left_side_view_h270_v0
      06_front_view_birds_eye_h0_v45
      07_front_view_low_angle_h0_vm30
    """
    parts = [p.strip() for p in line.split(",") if p.strip()]
    name_bits: list[str] = []
    if parts:
        name_bits.append(parts[0])
    if len(parts) >= 2 and not parts[1].lower().startswith("medium") and "shot" not in parts[1].lower():
        second = parts[1].lower()
        if second not in {"eye level", "eye-level"}:
            name_bits.append(parts[1])

    view_slug = _slug_token(" ".join(name_bits), max_len=48)

    h = _H_RE.search(line)
    v = _V_RE.search(line)
    z = _Z_RE.search(line)

    def _num_tag(prefix: str, m: re.Match[str] | None) -> str | None:
        if not m:
            return None
        val = float(m.group(1))
        if val < 0:
            return f"{prefix}m{int(abs(val))}"
        if val == int(val):
            return f"{prefix}{int(val)}"
        return f"{prefix}{val}".replace(".", "p")

    tags = [f"{index + 1:02d}", view_slug]
    ht = _num_tag("h", h)
    vt = _num_tag("v", v)
    if ht:
        tags.append(ht)
    if vt:
        tags.append(vt)
    if z:
        zv = float(z.group(1))
        if abs(zv - 5.0) > 1e-6:
            tags.append(_num_tag("z", z) or f"z{zv}")

    stem = "_".join(tags)
    stem = re.sub(r"[^a-zA-Z0-9_\-]+", "_", stem)
    return stem[:100]


def unique_angle_stems(lines: list[str], *, scene_name: str = "") -> list[str]:
    """
    `{scene_name}_{view_suffix}` for each line.

    Example: Audition_Room_01_left_side_view_h270_v0
    """
    prefix = scene_name_slug(scene_name)
    stems: list[str] = []
    seen: dict[str, int] = {}
    for i, line in enumerate(lines):
        suffix = angle_view_suffix(line, i)
        base = f"{prefix}_{suffix}" if prefix else suffix
        base = base[:140]
        n = seen.get(base, 0) + 1
        seen[base] = n
        stems.append(base if n == 1 else f"{base}_{n}")
    return stems


def angle_output_labels(angle_prompts: str, *, scene_name: str = "") -> dict[str, str]:
    lines = parse_angle_lines(angle_prompts)
    return labels_for_lines(lines, scene_name=scene_name)


def labels_for_lines(lines: list[str], *, scene_name: str = "") -> dict[str, str]:
    stems = unique_angle_stems(lines, scene_name=scene_name)
    return {
        stem: f"{i + 1:02d} · {angle_view_name(line)}"
        for i, (stem, line) in enumerate(zip(stems, lines))
    }


def build_scene_prompt(
    *,
    scene_image_name: str,
    scene_name: str = "",
    angle_prompts: str = "",
    prepend_text: str = "",
    append_text: str = "",
    start_index: int = 0,
    max_rows: int | None = None,
    seed: int | None = None,
    job_id: str | None = None,
) -> tuple[dict[str, Any], int, list[str], list[str]]:
    """
    Returns (prompt, seed, angle_lines_used, output_stems).

    Output stems: `{scene_name}_{view}` e.g. Audition_Room_01_left_side_view_h270_v0
    CR Prompt List outputs a STRING list → Comfy runs one sample per angle line.
    """
    prompt = copy.deepcopy(load_base_prompt())
    resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)

    lines = parse_angle_lines(angle_prompts or DEFAULT_ANGLES)
    if not lines:
        raise ValueError("at least one angle prompt line is required")
    if not scene_image_name:
        raise ValueError("scene reference image is required")

    text = "\n".join(lines)
    prompt[NODE_SCENE_IMAGE]["inputs"]["image"] = scene_image_name
    prompt[NODE_ANGLES]["inputs"]["string"] = text
    prompt[NODE_ANGLES]["inputs"]["strip_newlines"] = False

    pl = prompt[NODE_PROMPT_LIST]["inputs"]
    pl["prepend_text"] = (prepend_text or "").strip() or DEFAULT_PREPEND
    pl["append_text"] = append_text or ""
    pl["start_index"] = int(start_index or 0)
    start = max(0, min(int(start_index or 0), len(lines) - 1))
    if max_rows is None:
        max_rows = max(1, len(lines) - start)
    end = min(start + int(max_rows), len(lines))
    used = lines[start:end]
    stems = unique_angle_stems(used, scene_name=scene_name)
    pl["max_rows"] = max(1, len(used))

    if NODE_SAMPLER in prompt:
        prompt[NODE_SAMPLER]["inputs"]["seed"] = resolved_seed

    if job_id and NODE_SAVE in prompt:
        # Comfy uses shared prefix; local files rename to scene_name + view on download.
        safe_job = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:32]
        safe_scene = scene_name_slug(scene_name)
        prompt[NODE_SAVE]["inputs"]["filename_prefix"] = (
            f"director-studio/{safe_job}/{safe_scene}"
        )

    return prompt, resolved_seed, used, stems


def map_history_outputs(
    history: dict[str, Any],
    *,
    angle_lines: list[str] | None = None,
    output_stems: list[str] | None = None,
    scene_name: str = "",
) -> dict[str, ComfyImageRef]:
    """Map multi-image SaveImage output → keys `{scene}_{view}`."""
    outputs = history.get("outputs") or {}
    node_out = outputs.get(NODE_SAVE) or outputs.get(str(NODE_SAVE)) or {}
    images = node_out.get("images") or []

    if output_stems is None and angle_lines is not None:
        output_stems = unique_angle_stems(list(angle_lines), scene_name=scene_name)
    if output_stems is None:
        prefix = scene_name_slug(scene_name)
        output_stems = [
            f"{prefix}_{i + 1:02d}_angle" if prefix else f"{i + 1:02d}_angle"
            for i in range(len(images))
        ]

    mapped: dict[str, ComfyImageRef] = {}
    for i, img in enumerate(images):
        if i < len(output_stems):
            key = output_stems[i]
        else:
            prefix = scene_name_slug(scene_name)
            key = f"{prefix}_{i + 1:02d}_angle_extra" if prefix else f"{i + 1:02d}_angle_extra"
        mapped[key] = ComfyImageRef(
            filename=img.get("filename") or "",
            subfolder=img.get("subfolder") or "",
            type=img.get("type") or "output",
        )
    return mapped
