"""Resolve which image file to use from a library asset for Ref2AV / reference-frame.

Actors must prefer generated three-views (production identity sheets), not a
one-off master portrait alone and never blank wardrobe placeholders.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import LibraryAsset
from .store import asset_dir
from ..paths import find_asset_dir

# Role → preferred file keys (first hit wins)
ROLE_FILE_PREFERENCE: dict[str, tuple[str, ...]] = {
    "actor": (
        "fullbody_threeview",
        "bust_threeview",
        "asset_sheet",
        "master",
    ),
    "costume": (
        "master",
        "fullbody_threeview",
        "asset_sheet",
        "wardrobe_ref",
    ),
    "scene": (
        "angle_00",
        "angle_0",
        "plate",
        "master",
        "scene",
        "image",
    ),
    "layout_ref_frame": ("layout", "master", "image"),
    "prop": ("master", "image", "asset_sheet"),
    "other": ("master", "layout", "image", "asset_sheet"),
}

# Never use these as character/scene refs for video
SKIP_FILE_KEYS = frozenset(
    {
        "input_actor_ref",
        "input_wardrobe_ref",
        "wardrobe_ref",  # often 1x1 blank from casting
    }
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def preferred_file_keys(role: str | None, *, explicit: str | None = None) -> list[str]:
    if explicit:
        return [explicit]
    role_key = (role or "other").lower()
    prefs = ROLE_FILE_PREFERENCE.get(role_key, ROLE_FILE_PREFERENCE["other"])
    return list(prefs)


def resolve_asset_image(
    asset: LibraryAsset,
    *,
    role: str | None = None,
    file_key: str | None = None,
) -> tuple[str, bytes, str] | None:
    """
    Return (filename, bytes, file_key_used) for the best image on this asset.

    ``file_key`` forces a library files[] key when present.
    """
    # Locate on disk (project tree or global pool); fall back to write path
    adir = find_asset_dir(asset.kind, asset.id) or asset_dir(
        asset.kind, asset.id, project_id=asset.project_id
    )
    files = dict(asset.files or {})

    for key in preferred_file_keys(role, explicit=file_key):
        if key in SKIP_FILE_KEYS and not file_key:
            continue
        name = files.get(key)
        if not name:
            continue
        path = adir / name
        if path.is_file() and path.stat().st_size > 2048:  # skip tiny placeholders
            return path.name, path.read_bytes(), key

    # Fallback: any substantial image on disk, skipping known junk names
    skip_names = {files[k] for k in SKIP_FILE_KEYS if files.get(k)}
    candidates: list[Path] = []
    for p in sorted(adir.iterdir()) if adir.is_dir() else []:
        if not p.is_file():
            continue
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if p.name in skip_names:
            continue
        if p.stat().st_size <= 2048:
            continue
        # Prefer threeview filenames even if meta keys missing
        candidates.append(p)
    if not candidates:
        return None

    def rank(p: Path) -> tuple[int, str]:
        n = p.name.lower()
        if "fullbody" in n and "three" in n:
            return (0, n)
        if "bust" in n and "three" in n:
            return (1, n)
        if "sheet" in n:
            return (2, n)
        if "master" in n:
            return (3, n)
        if "angle" in n:
            return (4, n)
        return (9, n)

    best = sorted(candidates, key=rank)[0]
    # recover key if possible
    used_key = next((k for k, v in files.items() if v == best.name), best.stem)
    return best.name, best.read_bytes(), used_key
