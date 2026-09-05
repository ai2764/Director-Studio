"""Library ownership helpers + external asset import."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from ..core.library.store import (
    assign_asset_project,
    create_external_asset,
    create_external_voice_asset,
    delete_asset,
    list_assets,
    load_asset,
    write_asset,
)
from ..core.projects.store import list_projects, list_shots, save_shot
from ..core.schemas import LibraryAsset

router = APIRouter(tags=["library"])

KINDS = frozenset({"actors", "costumes", "scenes", "props", "layouts", "voices", "productions"})
IMPORT_KINDS = frozenset({"actors", "costumes", "scenes", "props", "layouts", "voices"})
_MAX_IMPORT_BYTES = 40 * 1024 * 1024  # 40 MB


class AssignProjectBody(BaseModel):
    project_id: str | None = Field(
        default=None,
        description="Target project id, or null to clear ownership",
    )


class BulkAssignBody(BaseModel):
    project_id: str
    kind: str | None = None  # if set, only this kind; else all known kinds
    only_unassigned: bool = True


class UpdateLibraryMetadataBody(BaseModel):
    name: str | None = None
    notes: str | None = None


def _safe_upload_name(name: str | None) -> str:
    base = Path(name or "image.png").name
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)
    return base[:120] or "image.png"


@router.get("/library", response_model=list[LibraryAsset])
async def list_library_assets(
    kind: str = Query(..., description="actors|costumes|scenes|props|layouts|…"),
    project_id: str | None = Query(None),
    include_unassigned: bool = Query(False),
) -> list[LibraryAsset]:
    if kind not in KINDS:
        raise HTTPException(400, f"unknown kind: {kind}")
    pid = (project_id or "").strip() or None
    return list_assets(kind, project_id=pid, include_unassigned=include_unassigned)


@router.post("/library/import", response_model=LibraryAsset)
async def import_external_asset(
    file: UploadFile = File(...),
    kind: str = Form(...),
    name: str = Form(""),
    notes: str = Form(""),
    project_id: str | None = Form(None),
    file_key: str | None = Form(None),
) -> LibraryAsset:
    """Import an external image or Voice reference as a library asset.

    Agent casting uses **name**, **notes/description**, and **source filename** only —
    full casting/set meta is not required.
    """
    kind_n = (kind or "").strip().lower()
    if kind_n not in IMPORT_KINDS:
        raise HTTPException(
            400,
            f"kind must be one of {sorted(IMPORT_KINDS)}, got {kind_n!r}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(400, f"file too large (max {_MAX_IMPORT_BYTES // (1024 * 1024)} MB)")

    src_name = _safe_upload_name(file.filename)
    label = (name or "").strip()
    if kind_n == "voices" and not label:
        raise HTTPException(400, "name is required for Voice assets")
    label = label or Path(src_name).stem
    pid = (project_id or "").strip() or None
    try:
        if kind_n == "voices":
            return create_external_voice_asset(
                name=label,
                notes=(notes or "").strip(),
                project_id=pid,
                audio_bytes=data,
                audio_filename=src_name,
                source_filename=file.filename or src_name,
            )
        return create_external_asset(
            kind=kind_n,
            name=label,
            notes=(notes or "").strip(),
            project_id=pid,
            image_bytes=data,
            image_filename=src_name,
            file_key=(file_key or None),
            source_filename=file.filename or src_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/library/{kind}/{asset_id}/project", response_model=LibraryAsset)
async def assign_one(kind: str, asset_id: str, body: AssignProjectBody) -> LibraryAsset:
    if kind not in KINDS:
        raise HTTPException(400, f"unknown kind: {kind}")
    try:
        return assign_asset_project(kind, asset_id, body.project_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/library/assign-project")
async def bulk_assign(body: BulkAssignBody) -> dict:
    """
    Assign unassigned (or all) library assets to a project.

    Useful to backfill assets created before project_id existed.
    """
    pid = body.project_id.strip()
    if not pid:
        raise HTTPException(400, "project_id required")
    kinds = [body.kind] if body.kind else sorted(KINDS)
    for k in kinds:
        if k not in KINDS:
            raise HTTPException(400, f"unknown kind: {k}")

    updated: list[str] = []
    skipped: list[str] = []
    for kind in kinds:
        for asset in list_assets(kind):  # all
            if body.only_unassigned and asset.project_id:
                skipped.append(asset.id)
                continue
            assign_asset_project(kind, asset.id, pid)
            updated.append(f"{kind}/{asset.id}")

    return {
        "project_id": pid,
        "updated": updated,
        "updated_count": len(updated),
        "skipped_count": len(skipped),
    }


@router.get("/library/{kind}/{asset_id}", response_model=LibraryAsset)
async def get_library_asset(kind: str, asset_id: str) -> LibraryAsset:
    if kind not in KINDS:
        raise HTTPException(400, f"unknown kind: {kind}")
    asset = load_asset(kind, asset_id)
    if asset is None:
        raise HTTPException(404, "not found")
    return asset


def _invalidate_shots_using_asset(asset: LibraryAsset) -> None:
    projects = [
        project
        for project in list_projects()
        if asset.project_id is None or project.id == asset.project_id
    ]
    change = {
        "kind": asset.kind,
        "asset_id": asset.id,
        "name": asset.name,
        "notes": asset.notes,
    }
    for project in projects:
        for shot in list_shots(project.id):
            picture_match = any(ref.asset_id == asset.id for ref in shot.refs)
            layout_match = any(ref.asset_id == asset.id for ref in shot.layout_refs)
            voice_match = any(ref.asset_id == asset.id for ref in shot.voice_refs)
            if not (picture_match or layout_match or voice_match):
                continue
            meta = dict(shot.meta or {})
            if picture_match or layout_match:
                meta["prompt_picture_signature"] = ""
                meta["prompt_layout_signature"] = ""
            if voice_match:
                meta["prompt_voice_signature"] = ""
            changes = dict(meta.get("material_changes") or {})
            metadata_updates = [
                item
                for item in list(changes.get("metadata_updated") or [])
                if item.get("asset_id") != asset.id
            ]
            changes["metadata_updated"] = [*metadata_updates, change]
            meta["material_changes"] = changes
            meta["material_review_pending"] = True
            save_shot(shot.model_copy(update={"meta": meta}))


@router.patch("/library/{kind}/{asset_id}", response_model=LibraryAsset)
async def update_library_asset_metadata(
    kind: str,
    asset_id: str,
    body: UpdateLibraryMetadataBody,
) -> LibraryAsset:
    if kind not in KINDS:
        raise HTTPException(400, f"unknown kind: {kind}")
    asset = load_asset(kind, asset_id)
    if asset is None:
        raise HTTPException(404, "not found")
    updates: dict[str, object] = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name cannot be empty")
        updates["name"] = name
    if body.notes is not None:
        notes = body.notes.strip()
        meta = dict(asset.meta or {})
        meta["description"] = notes
        updates["notes"] = notes
        updates["meta"] = meta
    if not updates:
        return asset
    updated = write_asset(asset.model_copy(update=updates))
    _invalidate_shots_using_asset(updated)
    return updated


@router.delete("/library/{kind}/{asset_id}")
async def delete_library_asset(kind: str, asset_id: str) -> dict:
    """Delete a library asset and all of its files (same-group outputs)."""
    if kind not in KINDS:
        raise HTTPException(400, f"unknown kind: {kind}")
    try:
        delete_asset(kind, asset_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True, "kind": kind, "id": asset_id}
