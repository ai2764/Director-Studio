"""Pack library / layout stills as Ollama vision inputs (base64 JPEG thumbnails)."""

from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any

from ...core.library.store import load_asset
from ...core.projects.layouts import LayoutReviewStatus
from ...core.projects.models import RefRole, Shot
from ...core.projects.store import load_shot
from ...core.schemas import LibraryAsset

logger = logging.getLogger("director_studio.director.vision")

# User phrases that mean "look at the picture(s)"
_VISION_HINTS = (
    "看图",
    "看看图",
    "看一下图",
    "看一下参考",
    "看一下参考帧",
    "看这张",
    "看下图",
    "看图片",
    "看图像",
    "看参考帧",
    "看参考",
    "看layout",
    "看 layout",
    "看构图",
    "审图",
    "过目",
    "描述图",
    "图里",
    "图片里",
    "这张图",
    "参考帧怎么样",
    "构图怎么样",
    "look at",
    "see the image",
    "see this image",
    "what do you see",
    "describe the image",
    "describe this",
)

MAX_VISION_IMAGES = 4
THUMB_MAX_SIDE = 512


def wants_vision(message: str) -> bool:
    raw = (message or "").strip()
    if not raw:
        return False
    lower = raw.lower()
    if lower in {"review", "visual review", "review layout"}:
        return True
    for h in _VISION_HINTS:
        if h.lower() in lower or h in raw:
            return True
    return False


def image_bytes_to_b64_jpeg(data: bytes, *, max_side: int = THUMB_MAX_SIDE) -> str | None:
    """Downscale and JPEG-encode for multimodal chat (keeps tokens/VRAM low)."""
    if not data or len(data) < 32:
        return None
    try:
        from PIL import Image
    except ImportError:
        # Fallback: send original as base64 (may be large)
        return base64.b64encode(data).decode("ascii")
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            im = im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        logger.exception("thumbnail encode failed")
        return None


def _read_layout_bytes(layout_asset_id: str) -> tuple[str, bytes] | None:
    asset = load_asset("layouts", layout_asset_id)
    if not asset:
        return None
    from .service import _read_asset_image_bytes

    pair = _read_asset_image_bytes(asset, role="layout_ref_frame", file_key="layout")
    if pair:
        return pair
    pair = _read_asset_image_bytes(asset, role="layout_ref_frame", file_key="master")
    return pair


def _read_ref_bytes(shot: Shot, ref) -> tuple[str, bytes, str] | None:
    from .planner import role_to_library_kind
    from .service import (
        _actor_image_for_ref_frame,
        _read_asset_image_bytes,
        _scene_image_for_ref_frame,
    )

    role_s = ref.role.value if hasattr(ref.role, "value") else str(ref.role)
    kind = role_to_library_kind(role_s)
    asset: LibraryAsset | None = None
    if kind:
        asset = load_asset(kind, ref.asset_id)
    if not asset:
        return None
    role = ref.role if isinstance(ref.role, RefRole) else RefRole(str(ref.role))
    if role == RefRole.actor:
        packed = _actor_image_for_ref_frame(asset, preferred_key=ref.file_key)
        if packed:
            name, data, used = packed
            return name, data, f"actor:{asset.name or asset.id} ({used})"
    if role == RefRole.scene:
        packed = _scene_image_for_ref_frame(asset, preferred_key=ref.file_key)
        if packed:
            name, data, used = packed
            return name, data, f"scene:{asset.name or asset.id} ({used})"
    pair = _read_asset_image_bytes(asset, role=role.value, file_key=ref.file_key)
    if pair:
        return pair[0], pair[1], f"{role.value}:{asset.name or asset.id}"
    return None


def _shot_index_from_message(message: str, shots: list[Shot]) -> int | None:
    m = re.search(r"(?:第\s*)?(\d+)\s*镜|(?:shot|SHOT)\s*#?\s*(\d+)|#(\d+)", message or "")
    if not m:
        return None
    n = int(next(g for g in m.groups() if g))
    if 1 <= n <= len(shots):
        return n - 1
    return None


def layout_reference_ids_from_message(
    message: str,
    shots: list[Shot],
) -> list[str]:
    """Return boundary-matched Layout ids that exist on the current shots."""
    raw = message or ""
    if not raw:
        return []
    found: list[str] = []
    for shot in shots:
        for layout in shot.layout_refs:
            pattern = (
                rf"(?<![A-Za-z0-9_-]){re.escape(layout.id)}"
                rf"(?![A-Za-z0-9_-])"
            )
            if re.search(pattern, raw, flags=re.I):
                found.append(layout.id)
    return list(dict.fromkeys(found))


def collect_vision_attachments(
    *,
    project_id: str,
    shots: list[Shot],
    message: str,
    max_images: int = MAX_VISION_IMAGES,
    layout_ref_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Pick layout + agent-cast refs for the user message.

    Returns {images_b64: [...], captions: [...], note: str}
    """
    if not shots:
        return {
            "images_b64": [],
            "captions": [],
            "note": "The project has no shots, so there are no images to review.",
        }

    all_layouts = [
        (shot, layout)
        for shot in shots
        for layout in shot.layout_refs
    ]
    requested_ids = list(dict.fromkeys(layout_ref_ids or []))
    if not requested_ids:
        requested_ids = layout_reference_ids_from_message(message, shots)

    generic_review = (message or "").strip().lower() in {
        "review",
        "visual review",
        "review layout",
    }
    if not requested_ids and generic_review:
        reviewable = [
            layout
            for _shot, layout in all_layouts
            if layout.asset_id
            and layout.review_status == LayoutReviewStatus.pending_review
        ]
        if len(reviewable) == 1:
            requested_ids = [reviewable[0].id]
        elif len(reviewable) > 1:
            return {
                "images_b64": [],
                "captions": [],
                "note": "More than one Layout is pending review. Name the Layout id to review.",
            }

    if requested_ids:
        images_b64: list[str] = []
        captions: list[str] = []
        by_id = {layout.id: (shot, layout) for shot, layout in all_layouts}
        for layout_ref_id in requested_ids:
            hit = by_id.get(layout_ref_id)
            if hit is None or len(images_b64) >= max_images:
                continue
            shot, layout = hit
            if not layout.asset_id:
                continue
            pair = _read_layout_bytes(layout.asset_id)
            if not pair:
                continue
            b64 = image_bytes_to_b64_jpeg(pair[1])
            if not b64:
                continue
            images_b64.append(b64)
            captions.append(
                f"Image {len(images_b64)}: Shot {shots.index(shot) + 1} · "
                f"{shot.title} · Layout {layout.id}; "
                f"purpose: {layout.purpose or '—'}; "
                f"state_description: {layout.state_description or '—'}; "
                f"time_hint: {layout.time_hint or '—'}"
            )
        if not images_b64:
            return {
                "images_b64": [],
                "captions": [],
                "note": "No readable layout.png was found for the requested Layout id(s).",
            }
        note = (
            f"Attached {len(images_b64)} Layout image"
            f"{'s' if len(images_b64) != 1 else ''} for visual review:\n"
            + "\n".join(f"- {caption}" for caption in captions)
        )
        return {"images_b64": images_b64, "captions": captions, "note": note}

    idx = _shot_index_from_message(message, shots)
    # Default: first shot with a layout, else first shot
    if idx is None:
        for i, s in enumerate(shots):
            if s.layout_asset_id:
                idx = i
                break
        if idx is None:
            idx = 0

    targets = [shots[idx]]
    # "所有" / all → include up to 2 shots with layouts
    if re.search(r"全部|所有|每一镜|all shots", message or "", flags=re.I):
        targets = [s for s in shots if s.layout_asset_id][:2] or shots[:2]

    images_b64: list[str] = []
    captions: list[str] = []

    for si, shot in enumerate(targets):
        label_prefix = f"Shot {shots.index(shot) + 1} · {shot.title}"
        # 1) layout / reference frame first (what user usually means by 看图)
        if shot.layout_asset_id:
            pair = _read_layout_bytes(shot.layout_asset_id)
            if pair and len(images_b64) < max_images:
                b64 = image_bytes_to_b64_jpeg(pair[1])
                if b64:
                    images_b64.append(b64)
                    captions.append(f"Image {len(images_b64)}: {label_prefix} reference frame/layout")

        # 2) agent-cast refs (scene / actor / prop) so agent can compare
        ordered = sorted(shot.refs or [], key=lambda r: r.picture_index)
        for ref in ordered:
            if len(images_b64) >= max_images:
                break
            if ref.role == RefRole.layout_ref_frame:
                continue
            hit = _read_ref_bytes(shot, ref)
            if not hit:
                continue
            _name, data, cap = hit
            b64 = image_bytes_to_b64_jpeg(data)
            if not b64:
                continue
            images_b64.append(b64)
            captions.append(f"Image {len(images_b64)}: {label_prefix} agent-ref {cap}")

    if not images_b64:
        return {
            "images_b64": [],
            "captions": [],
            "note": "No readable reference frame or Asset Library image was found. Generate a reference frame or confirm that the library asset has an image.",
        }

    note = (
        f"Attached {len(images_b64)} thumbnail image{'s' if len(images_b64) != 1 else ''} for visual review:\n"
        + "\n".join(f"- {c}" for c in captions)
    )
    return {"images_b64": images_b64, "captions": captions, "note": note}
