from __future__ import annotations

import base64
import binascii
from typing import Any

from ....core.library.store import create_external_asset, write_asset

_LIBRARY_KINDS = frozenset({"actors", "costumes", "scenes", "props", "layouts"})
_MIN_IMPORT_CONFIDENCE = 0.65


async def handle_library_tool(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    actions: list[str],
    notes: list[str],
    result_payloads: list[dict[str, Any]] | None,
    user_uploads: list[dict[str, Any]] | None,
) -> bool:
    if name != "classify_chat_image":
        return False

    try:
        image_index = int(args.get("image_index"))
    except (TypeError, ValueError) as exc:
        raise ValueError("image_index must be an integer") from exc
    uploads = user_uploads or []
    if image_index < 1 or image_index > len(uploads):
        raise ValueError(
            f"image_index must identify one of the {len(uploads)} current chat uploads"
        )
    upload = uploads[image_index - 1]
    prior = upload.get("classification")
    if isinstance(prior, dict):
        if result_payloads is not None:
            result_payloads.append(dict(prior))
        notes.append(f"Image {image_index} was already classified in this turn.")
        return True

    kind = str(args.get("kind") or "").strip().lower()
    if kind not in _LIBRARY_KINDS | {"chat_only"}:
        raise ValueError(f"unsupported chat image classification: {kind or '(missing)'}")
    asset_name = str(args.get("name") or "").strip()[:120]
    asset_notes = str(args.get("notes") or "").strip()[:2000]
    if not asset_name:
        raise ValueError("name is required")
    if not asset_notes:
        raise ValueError("notes are required")
    try:
        confidence = float(args.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be a number from 0 to 1") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be a number from 0 to 1")

    if kind == "chat_only" or confidence < _MIN_IMPORT_CONFIDENCE:
        result = {
            "ok": True,
            "imported": False,
            "image_index": image_index,
            "kind": "chat_only",
            "name": asset_name,
            "notes": asset_notes,
            "confidence": confidence,
            "reason": "low_confidence" if confidence < _MIN_IMPORT_CONFIDENCE else "chat_only",
        }
        upload["classification"] = result
        actions.append("classify_chat_image:chat_only")
        notes.append(
            f"Kept Image {image_index} as a chat attachment only because its Library classification was uncertain."
        )
        if result_payloads is not None:
            result_payloads.append(result)
        return True

    try:
        image_bytes = base64.b64decode(
            str(upload.get("data_b64") or ""),
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Image {image_index} upload data is invalid") from exc
    if not image_bytes:
        raise ValueError(f"Image {image_index} upload data is empty")

    filename = str(upload.get("filename") or f"image-{image_index}.png")
    file_key = "layout" if kind == "layouts" else "master"
    asset = create_external_asset(
        kind=kind,
        name=asset_name,
        notes=asset_notes,
        project_id=project_id,
        image_bytes=image_bytes,
        image_filename=filename,
        file_key=file_key,
        source_filename=filename,
    )
    asset.meta.update(
        {
            "source": "director_chat_upload",
            "classification_confidence": confidence,
            "chat_image_index": image_index,
        }
    )
    asset = write_asset(asset)
    result = {
        "ok": True,
        "imported": True,
        "image_index": image_index,
        "kind": kind,
        "name": asset.name,
        "notes": asset.notes,
        "confidence": confidence,
        "asset_id": asset.id,
        "file_key": file_key,
        "url": asset.urls.get(file_key, ""),
    }
    upload["classification"] = result
    actions.append(f"import_chat_image:{asset.id}")
    notes.append(
        f"Imported Image {image_index} as {kind}/{asset.id} named {asset.name!r} with file_key {file_key}."
    )
    if result_payloads is not None:
        result_payloads.append(result)
    return True
