"""Runtime setup API for portable H3 Ref2AV workflow profiles."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..integrations.comfy_mcp import ComfyMcpClient, ComfyMcpError
from ..pipelines.h3_ref2va.workflow import fill_profile_graph
from ..workflow_profiles.h3 import (
    H3BoundaryMapping,
    H3ProfileStore,
    ProfileChangedError,
    ProfileStateError,
    ProfileStorageError,
    ResolvedH3Profile,
)
from ..workflow_profiles.h3.inspector import MAX_WORKFLOW_BYTES, inspect_h3_workflow
from ..workflow_profiles.h3.validator import validate_h3_contract

router = APIRouter(prefix="/workflow-profiles/h3", tags=["h3-workflow-profiles"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectProfileRequest(_StrictModel):
    profile_id: StrictStr = Field(pattern=r"[a-z0-9][a-z0-9-]{0,63}")


def _error(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": details or {}},
    )


def _store_error(exc: ProfileStorageError) -> JSONResponse:
    if isinstance(exc, ProfileStateError):
        status = 422 if exc.code == "contract_validation_failed" else 409
        return _error(status, exc.code, str(exc), exc.details)
    if isinstance(exc, ProfileChangedError):
        return _error(409, "profile_changed", str(exc))
    message = str(exc)
    code = (
        "invalid_import_id"
        if message == "Invalid workflow import ID"
        else "profile_storage_error"
    )
    return _error(400, code, message)


def _active_payload(store: H3ProfileStore) -> dict[str, Any]:
    resolved = store.resolve_active()
    return {
        "profile_id": resolved.profile_id,
        "display_name": (
            "Built-in Official H3"
            if resolved.source == "builtin"
            else resolved.profile_id.replace("-", " ").title()
        ),
        "source": resolved.source,
        "workflow_sha256": resolved.workflow_sha256,
        "contract_version": 1,
        "warning": (
            {
                "code": resolved.warning.code,
                "message": resolved.warning.message,
                "details": resolved.warning.details,
            }
            if resolved.warning
            else None
        ),
    }


@router.get("")
def list_h3_profiles() -> dict[str, Any]:
    store = H3ProfileStore()
    active = _active_payload(store)
    profiles: list[dict[str, Any]] = [
        {
            "profile_id": "builtin-official-h3",
            "display_name": "Built-in Official H3",
            "source": "builtin",
            "status": (
                "active"
                if active["profile_id"] == "builtin-official-h3"
                else "available"
            ),
            "workflow_sha256": store.resolve_builtin().workflow_sha256,
        }
    ]
    for profile in store.list_installed_profiles():
        profiles.append(
            {
                "profile_id": profile.id,
                "display_name": profile.id.replace("-", " ").title(),
                "source": "custom",
                "status": "active" if profile.id == active["profile_id"] else "tested",
                "workflow_sha256": profile.workflow_sha256,
            }
        )
    return {"active": active, "profiles": profiles}


@router.post("/imports", status_code=201, response_model=None)
async def import_h3_workflow(
    workflow: Annotated[UploadFile, File()],
) -> dict[str, Any] | JSONResponse:
    raw = await workflow.read(MAX_WORKFLOW_BYTES + 1)
    if len(raw) > MAX_WORKFLOW_BYTES:
        return _error(
            400,
            "workflow_too_large",
            f"Workflow API JSON exceeds {MAX_WORKFLOW_BYTES // 1024 // 1024} MiB",
        )
    try:
        graph = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(
            400, "invalid_workflow_json", "Workflow must contain valid UTF-8 JSON"
        )
    if not isinstance(graph, dict):
        return _error(400, "invalid_workflow_json", "Workflow JSON must be an object")
    try:
        inspect_h3_workflow(graph)
        store = H3ProfileStore()
        import_id = store.create_import(graph)
        workflow_sha256 = store.import_workflow_sha256(import_id)
    except (TypeError, ValueError) as exc:
        return _error(400, "invalid_workflow", str(exc))
    except ProfileStorageError as exc:
        return _store_error(exc)
    return {
        "import_id": import_id,
        "workflow_sha256": workflow_sha256,
        "filename": workflow.filename or "workflow.api.json",
    }


@router.get("/imports/{import_id:path}/analysis", response_model=None)
def analyze_h3_import(import_id: str) -> dict[str, Any] | JSONResponse:
    store = H3ProfileStore()
    try:
        graph = store.load_import_workflow(import_id)
        analysis = inspect_h3_workflow(graph)
        accepted_mapping = store.load_import_mapping(import_id)
    except (TypeError, ValueError) as exc:
        return _error(400, "invalid_workflow", str(exc), {"import_id": import_id})
    except ProfileStorageError as exc:
        return _store_error(exc)
    payload = analysis.model_dump(mode="json")
    if accepted_mapping is not None:
        payload["mapping"] = accepted_mapping.model_dump(mode="json")
    payload["import_id"] = import_id
    payload["workflow_sha256"] = store.import_workflow_sha256(import_id)
    return payload


@router.put("/imports/{import_id:path}/mapping", response_model=None)
def save_h3_import_mapping(
    import_id: str,
    body: H3BoundaryMapping,
) -> dict[str, Any] | JSONResponse:
    store = H3ProfileStore()
    try:
        store.save_import_mapping(import_id, body)
    except ProfileStorageError as exc:
        return _store_error(exc)
    return {"import_id": import_id, "mapping": body.model_dump(mode="json")}


@router.post("/imports/{import_id:path}/validate", response_model=None)
async def validate_h3_import(import_id: str) -> dict[str, Any] | JSONResponse:
    store = H3ProfileStore()
    try:
        graph = store.load_import_workflow(import_id)
        analysis = inspect_h3_workflow(graph)
        mapping = store.load_import_mapping(import_id) or analysis.mapping
        if mapping is None:
            return _error(
                422,
                "mapping_required",
                "A compatible workflow mapping is required before validation",
                {
                    "import_id": import_id,
                    "compatibility": analysis.compatibility,
                    "issues": [
                        issue.model_dump(mode="json") for issue in analysis.issues
                    ],
                },
            )
        report = validate_h3_contract(graph, mapping)
        if not report.valid:
            return _error(
                422,
                "contract_validation_failed",
                "The workflow does not satisfy the H3 Ref2AV contract",
                {
                    "import_id": import_id,
                    "issues": [
                        issue.model_dump(mode="json") for issue in report.issues
                    ],
                    "fixed_dependencies": [
                        item.model_dump(mode="json")
                        for item in report.fixed_dependencies
                    ],
                },
            )
        store.save_import_mapping(import_id, mapping)
        workflow_sha256 = store.import_workflow_sha256(import_id)
        filled = fill_profile_graph(
            ResolvedH3Profile(
                profile_id="validation-import",
                workflow=graph,
                mapping=mapping,
                workflow_sha256=workflow_sha256,
                source="custom",
            ),
            {
                "prompt": "Neutral H3 workflow validation",
                "images": ["contract-picture.png"],
                "audios": [],
                "frames": 56,
                "width": 864,
                "height": 480,
                "seed": 42,
                "output_prefix": "director-studio/h3/contract-validation",
            },
        )
        workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    except (TypeError, ValueError) as exc:
        return _error(
            422, "contract_validation_failed", str(exc), {"import_id": import_id}
        )
    except ProfileStorageError as exc:
        return _store_error(exc)

    client = ComfyMcpClient()
    try:
        comfy_payload = await client.validate_workflow(filled)
    except ComfyMcpError as exc:
        return _error(
            422,
            "dependency_validation_failed",
            str(exc),
            {"import_id": import_id},
        )
    finally:
        await client.aclose()

    try:
        record = store.record_validation_success(
            import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
            report=report.model_dump(mode="json"),
            comfy_payload=comfy_payload,
        )
    except ProfileStorageError as exc:
        return _store_error(exc)
    return {
        **report.model_dump(mode="json"),
        "import_id": import_id,
        "workflow_sha256": record["workflow_sha256"],
        "validated_at": record["validated_at"],
        "comfy": comfy_payload,
    }


@router.post("/imports/{import_id:path}/activate", response_model=None)
def activate_h3_import(import_id: str) -> dict[str, Any] | JSONResponse:
    store = H3ProfileStore()
    try:
        profile = store.activate_import(import_id)
    except ProfileStorageError as exc:
        return _store_error(exc)
    return {
        "import_id": import_id,
        "profile_id": profile.id,
        "active": _active_payload(store),
    }


@router.post("/select", response_model=None)
def select_h3_profile(body: SelectProfileRequest) -> dict[str, Any] | JSONResponse:
    store = H3ProfileStore()
    try:
        store.select_profile(body.profile_id)
    except ProfileStorageError as exc:
        return _store_error(exc)
    return {"active": _active_payload(store)}


__all__ = ["router"]
