"""Director-facing library inventory and asset-file resolution rules."""

from __future__ import annotations

import hashlib
from typing import Any

from ...core.library.store import list_assets
from ...core.projects.models import RefRole
from ...core.schemas import LibraryAsset


LIBRARY_KINDS = ("actors", "costumes", "scenes", "props", "layouts", "voices")


def _script_hash(script_text: str) -> str:
    return hashlib.sha256((script_text or "").encode("utf-8")).hexdigest()[:16]


def _inventory(project_id: str | None = None) -> list[dict[str, Any]]:
    """Casting pool: project-owned assets plus the unassigned pool."""
    items: list[dict[str, Any]] = []
    for kind in LIBRARY_KINDS:
        if kind == "layouts":
            continue
        for asset in list_assets(kind, project_id=project_id, include_unassigned=True):
            files = dict(asset.files or {})
            file_keys = [key for key, value in files.items() if value]
            filenames = [str(value) for value in files.values() if value]
            meta = asset.meta or {}
            source_filename = str(meta.get("source_filename") or "")
            if source_filename and source_filename not in filenames:
                filenames.append(source_filename)
            description = str(meta.get("description") or asset.notes or "")[:240]
            items.append(
                {
                    "id": asset.id,
                    "kind": asset.kind,
                    "name": asset.name,
                    "notes": (asset.notes or "")[:240],
                    "description": description,
                    "source_filename": source_filename,
                    "filenames": filenames[:8],
                    "tags": meta.get("tags") or [],
                    "file_keys": file_keys,
                    "external": bool(meta.get("external"))
                    or (asset.pipeline_id or "") == "external",
                    "owned_by_project": bool(
                        project_id and (asset.project_id or None) == project_id
                    ),
                    "project_id": asset.project_id,
                    "duration_s": (
                        meta.get("duration_s") if kind == "voices" else None
                    ),
                    "h3_ready": bool(meta.get("h3_ready"))
                    if kind == "voices"
                    else None,
                }
            )
    return items


def _asset_index(project_id: str | None = None) -> dict[str, LibraryAsset]:
    index: dict[str, LibraryAsset] = {}
    for kind in LIBRARY_KINDS:
        for asset in list_assets(kind, project_id=project_id, include_unassigned=True):
            index[asset.id] = asset
        if project_id is not None:
            for asset in list_assets(kind):
                index.setdefault(asset.id, asset)
    return index


def _default_file_key(
    role: RefRole,
    asset: LibraryAsset | None = None,
) -> str | None:
    if role == RefRole.actor:
        if asset and asset.files:
            for key in (
                "fullbody_threeview",
                "bust_threeview",
                "asset_sheet",
                "master",
            ):
                if asset.files.get(key):
                    return key
        return "fullbody_threeview"
    if role == RefRole.scene:
        if asset and asset.files:
            for key in ("angle_00", "angle_0", "plate", "master", "scene", "image"):
                if asset.files.get(key):
                    return key
            for key in sorted(asset.files):
                if asset.files.get(key) and not str(key).startswith("input_"):
                    return key
        return "angle_00"
    if role in (RefRole.prop, RefRole.costume, RefRole.other):
        if asset and asset.files:
            for key in ("master", "image", "plate", "asset_sheet"):
                if asset.files.get(key):
                    return key
            for key in sorted(asset.files):
                if asset.files.get(key) and not str(key).startswith("input_"):
                    return key
        return "master"
    return None


def _repair_unique_file_key_typo(
    requested_key: str | None,
    asset: LibraryAsset | None,
) -> str | None:
    """Repair only an unambiguous single-character Agent typo."""
    if not requested_key or asset is None or not asset.files:
        return requested_key
    if asset.files.get(requested_key):
        return requested_key

    candidates: list[str] = []
    for candidate, path in asset.files.items():
        if not path or len(candidate) != len(requested_key):
            continue
        differences = sum(a != b for a, b in zip(candidate, requested_key))
        if differences == 1:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else requested_key


def _read_asset_image_bytes(
    asset: LibraryAsset,
    *,
    role: str | None = None,
    file_key: str | None = None,
) -> tuple[str, bytes] | None:
    """Return ``(filename, bytes)`` using shared library image resolution."""
    from ...core.library.images import resolve_asset_image

    hit = resolve_asset_image(asset, role=role, file_key=file_key)
    if not hit:
        return None
    name, data, _key = hit
    return name, data
