"""Reference-frame briefs and deterministic reference image selection."""

from __future__ import annotations

import logging

from ...core.library.store import list_assets
from ...core.projects.layouts import LayoutBrief, LayoutReference, LayoutSourceRef
from ...core.projects.models import Project, RefRole, Shot
from ...core.schemas import LibraryAsset

logger = logging.getLogger("director_studio.director.reference")


_TAIL_FRAME_IMAGE1_NOTES = (
    "Preserve requested continuity as Image1. Apply only the user's stated changes. "
    "Do not recreate a contact sheet, introduce a new camera angle without instruction, "
    "or keep motion blur and compression artifacts as identity features."
)


def build_tail_frame_revision_brief(
    layout: LayoutReference,
    additional_source_refs: list[LayoutSourceRef] | None = None,
    *,
    feedback: str = "",
) -> LayoutBrief:
    """Build a Qwen LayoutBrief that uses an extracted tail frame as Image1."""
    extras = list(additional_source_refs or [])
    if len(extras) > 2:
        raise ValueError(
            "a tail-frame redraw accepts at most 3 Qwen source images "
            "(the extracted Layout plus up to 2 additional sources)"
        )
    asset_id = (layout.asset_id or "").strip()
    if not asset_id:
        raise ValueError(
            f"LayoutReference has no generated image to revise: {layout.id}"
        )
    notes = _TAIL_FRAME_IMAGE1_NOTES
    cleaned = feedback.strip()
    if cleaned:
        notes = f"{notes} User feedback: {cleaned}"
    return LayoutBrief(
        purpose=layout.purpose,
        state_description=layout.state_description,
        time_hint=layout.time_hint,
        source_refs=[
            LayoutSourceRef(
                role=RefRole.layout_ref_frame,
                asset_id=asset_id,
                file_key="layout",
                notes=notes,
            ),
            *extras,
        ],
    )


def _match_library_scene(project: Project, shot: Shot) -> LibraryAsset | None:
    """Best-effort scene asset from library by scene_id / title / script tokens."""
    scenes = list_assets("scenes", project_id=project.id, include_unassigned=True)
    if not scenes:
        return None
    needles = [
        (shot.scene_id or "").lower(),
        (shot.title or "").lower(),
        (shot.script_beat or "").lower()[:80],
        (project.script_text or "").lower()[:200],
    ]
    best: LibraryAsset | None = None
    best_score = 0
    for asset in scenes:
        hay = f"{asset.name} {asset.notes} {asset.id}".lower()
        score = 0
        if (asset.project_id or None) == project.id:
            score += 1
        for n in needles:
            if n and n in hay:
                score += 2
            for word in n.replace("_", " ").split():
                if len(word) > 3 and word in hay:
                    score += 1
        if score > best_score:
            best_score = score
            best = asset
    # If no textual match, still return the only scene (single-location shows)
    if best is None and len(scenes) == 1:
        return scenes[0]
    if best is not None and best_score > 0:
        return best
    owned = [s for s in scenes if (s.project_id or None) == project.id]
    if len(owned) == 1:
        return owned[0]
    return scenes[0] if len(scenes) == 1 else None


def _match_library_actor(project: Project, shot: Shot) -> LibraryAsset | None:
    """Best-effort actor from project pool by name tokens in title/beat/script."""
    actors = list_assets("actors", project_id=project.id, include_unassigned=True)
    if not actors:
        return None
    hay = " ".join(
        [
            shot.title or "",
            shot.script_beat or "",
            " ".join(shot.dialogue or []),
            (project.script_text or "")[:400],
        ]
    ).lower()
    best: LibraryAsset | None = None
    best_score = 0
    for asset in actors:
        score = 1 if (asset.project_id or None) == project.id else 0
        name = (asset.name or "").lower()
        if name and name in hay:
            score += 5
        for word in name.replace("_", " ").split():
            if len(word) > 2 and word in hay:
                score += 2
        files = asset.files or {}
        if files.get("fullbody_threeview") or files.get("bust_threeview"):
            score += 1
        if score > best_score:
            best_score = score
            best = asset
    if best is not None and best_score > 0:
        return best
    owned = [a for a in actors if (a.project_id or None) == project.id]
    if len(owned) == 1:
        return owned[0]
    return actors[0] if len(actors) == 1 else None


def build_ref_frame_brief(
    project: Project,
    shot: Shot,
    *,
    ref_labels: list[str] | None = None,
) -> str:
    """
    Script + shot beat + dialogue brief for layout reference-frame generation.

    Must ground the still in story action while ref images carry identity/set.
    Explicitly forbids turnaround / three-view sheet layouts (common Qwen Edit pollution).
    """
    dialogue = shot.dialogue or []
    if isinstance(dialogue, str):
        dialogue = [dialogue] if dialogue else []
    dial_block = "\n".join(f'- "{d}"' for d in dialogue if d) or "- (no spoken lines)"

    # Keep script context short but real — full screenplay can drown the beat
    script = (project.script_text or "").strip()
    if len(script) > 1200:
        script = script[:1200] + "\n…"

    labels = ref_labels or []
    label_block = "\n".join(f"- {lab}" for lab in labels) or "- (no refs resolved)"

    return (
        "=== STORY / SCRIPT CONTEXT ===\n"
        f"{script}\n\n"
        "=== THIS SHOT ===\n"
        f"scene_id: {shot.scene_id}\n"
        f"title: {shot.title}\n"
        f"duration_s: {shot.duration_s}\n"
        f"blocking / action: {shot.script_beat}\n"
        f"dialogue:\n{dial_block}\n\n"
        "=== REFERENCE ROLES ===\n"
        f"{label_block}\n\n"
        "=== TASK ===\n"
        "Generate ONE photoreal production still for this shot (blocking board for video).\n"
        "HARD RULES:\n"
        "- One continuous frame, natural camera, clean photoreal materials (no oily mush).\n"
        "- Character stands physically in the set: feet on floor, correct scale vs door/bench.\n"
        "- Blocking action is primary (pacing, walking, stopping) — match script_beat.\n"
        "- Scene ref = architecture/lighting; Character ref = face/hair/outfit only.\n"
        "- FORBIDDEN: poster/sticker of person on frosted glass, reflection-only person, "
        "floating cutout, character sheet / three-view, studio void, melted wall texture, "
        "empty corridor with no person.\n"
        "- No comic panels, no watermark, no UI text."
    )


def _crop_sheet_front_panel(
    data: bytes,
    *,
    force: bool = False,
) -> tuple[str, bytes] | None:
    """If ref is a multi-panel sheet, take the left (front) panel only.

    Fullbody three-views are often ~1.5:1 (three near-square panels), not 3:1.
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        w, h = im.size
        if w < 64 or h < 64:
            return None
        # Horizontal 2–3 panel sheets (common three-view ~1.45–3.0 aspect)
        if force or w >= int(h * 1.35):
            panel = im.crop((0, 0, max(w // 3, 1), h))
        # Vertical stacked sheets (rare)
        elif h >= int(w * 1.35):
            panel = im.crop((0, 0, w, max(h // 3, 1)))
        else:
            return None
        buf = io.BytesIO()
        panel.save(buf, format="PNG")
        return "actor_front_panel.png", buf.getvalue()
    except Exception:
        logger.exception("crop sheet front panel failed")
        return None


def _scene_image_for_ref_frame(
    asset: LibraryAsset,
    *,
    preferred_key: str | None = None,
) -> tuple[str, bytes, str] | None:
    """Use the Agent-selected scene angle, with neutral plates as fallback only."""
    from ...core.library.images import resolve_asset_image

    files = dict(asset.files or {})

    def _is_bad_layout_angle(key: str) -> bool:
        kl = (key or "").lower()
        return any(
            x in kl
            for x in (
                "left_side",
                "right_side",
                "back_view",
                "birds",
                "bird_eye",
                "low_angle",
                "h270",
                "h180",
                "h90",
                "vm30",
            )
        )

    ordered_keys: list[str] = []
    for key in (
        preferred_key,
        "input_scene",  # clean full plate — best for layout quality
        "master",
        "plate",
        "image",
        "scene",
        "angle_00",
        "angle_0",
    ):
        if key and key in files and files[key] and key not in ordered_keys:
            ordered_keys.append(key)
    frontal: list[str] = []
    rest: list[str] = []
    for k in sorted(files.keys()):
        if not files.get(k) or k in ordered_keys or str(k).startswith("input_"):
            continue
        if _is_bad_layout_angle(k):
            rest.append(k)  # last resort only
            continue
        kl = k.lower()
        if any(x in kl for x in ("front", "wide", "establishing")):
            frontal.append(k)
        else:
            rest.append(k)
    ordered_keys.extend(frontal)
    ordered_keys.extend(rest)

    for key in ordered_keys:
        hit = resolve_asset_image(asset, role="scene", file_key=key)
        if hit:
            name, data, used = hit
            return name, data, used
    return None


def _actor_image_for_ref_frame(
    asset: LibraryAsset,
    *,
    preferred_key: str | None = None,
) -> tuple[str, bytes, str] | None:
    """
    Identity still for layout reference-frame — prefer single portrait over three-view sheets.

    Qwen Image Edit Plus often copies multi-panel turnaround structure into the output
    when fullbody_threeview is fed as a ref (reference pollution). For reference-frame we:
    1) try master / non-sheet keys
    2) else crop the front panel from a three-view sheet
    3) last resort: full sheet (prompt must fight layout copy)
    """
    from ...core.library.images import resolve_asset_image

    # Explicit non-sheet keys first
    for key in (
        preferred_key,
        "master",
        "portrait",
        "hero",
        "input_actor",
    ):
        if not key:
            continue
        hit = resolve_asset_image(asset, role="actor", file_key=key)
        if hit:
            name, data, used = hit
            # If someone stored a sheet under master, still try crop
            cropped = _crop_sheet_front_panel(data)
            if cropped and used in ("fullbody_threeview", "bust_threeview", "asset_sheet"):
                return cropped[0], cropped[1], f"{used}->front_crop"
            return name, data, used

    # Sheet keys: always try front-panel crop (force — aspect may be only ~1.5)
    for key in ("fullbody_threeview", "bust_threeview", "asset_sheet"):
        hit = resolve_asset_image(asset, role="actor", file_key=key)
        if not hit:
            continue
        name, data, used = hit
        cropped = _crop_sheet_front_panel(data, force=True)
        if cropped:
            return cropped[0], cropped[1], f"{used}->front_crop"
        return name, data, used

    hit = resolve_asset_image(asset, role="actor")
    if not hit:
        return None
    name, data, used = hit
    if used in ("fullbody_threeview", "bust_threeview", "asset_sheet") or "three" in (
        used or ""
    ):
        cropped = _crop_sheet_front_panel(data, force=True)
        if cropped:
            return cropped[0], cropped[1], f"{used}->front_crop"
    return name, data, used
