"""Runtime setup API for portable H3 Ref2AV workflow profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..core.jobs import create_job, start_pipeline_job
from ..core.library.images import resolve_asset_image
from ..core.library.store import asset_dir, load_asset
from ..core.paths import LIBRARY_KINDS
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
from ..workflow_profiles.h3.agent import (
    AgentConfigurationError,
    ContractError,
)
from ..workflow_profiles.h3.agent import (
    propose_h3_mapping as _propose_h3_mapping,
)
from ..workflow_profiles.h3.inspector import MAX_WORKFLOW_BYTES, inspect_h3_workflow
from ..workflow_profiles.h3.validator import validate_h3_contract

router = APIRouter(prefix="/workflow-profiles/h3", tags=["h3-workflow-profiles"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectProfileRequest(_StrictModel):
    profile_id: StrictStr = Field(pattern=r"[a-z0-9][a-z0-9-]{0,63}")


class TestProfileRequest(_StrictModel):
    picture_asset_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    audio_asset_id: StrictStr | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
    )


_TEST_PROMPT = """subject_definitions:
<Picture 1> defines the subject and visual identity for the whole clip.{audio_binding}
summary:
A neutral workflow setup test with natural, stable motion.
retention_analysis:
Preserve the subject identity, proportions, clothing, lighting, and background.
detailed_description:
The subject remains composed while the camera makes a slow camera push.
overall_soundscape:
Normal ambient audio at a natural level with no sudden or exaggerated sounds.
non_diegetic_music:
No music."""


def _resolve_picture_asset(asset_id: str) -> tuple[str, bytes] | None:
    role_for_kind = {
        "actors": "actor",
        "costumes": "costume",
        "scenes": "scene",
        "props": "prop",
        "layouts": "layout_ref_frame",
    }
    for kind in LIBRARY_KINDS:
        asset = load_asset(kind, asset_id)
        if asset is None:
            continue
        resolved = resolve_asset_image(asset, role=role_for_kind.get(kind, "other"))
        if resolved is not None:
            filename, data, _file_key = resolved
            return filename, data
    return None


def _resolve_voice_asset(asset_id: str) -> tuple[str, bytes] | None:
    asset = load_asset("voices", asset_id)
    if asset is None or not bool((asset.meta or {}).get("h3_ready")):
        return None
    filename = (asset.files or {}).get("reference")
    if not isinstance(filename, str) or Path(filename).name != filename:
        return None
    directory = asset_dir("voices", asset.id, project_id=asset.project_id)
    path = (directory / filename).resolve()
    try:
        path.relative_to(directory.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path.name, path.read_bytes()


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
    payload["lifecycle"] = store.import_lifecycle(import_id)
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


@router.post("/imports/{import_id:path}/propose-mapping", response_model=None)
async def propose_h3_mapping(import_id: str) -> dict[str, Any] | JSONResponse:
    """Return untrusted mapping advice without accepting or activating it."""
    try:
        proposal = await _propose_h3_mapping(import_id)
    except ProfileStorageError as exc:
        return _store_error(exc)
    except AgentConfigurationError as exc:
        return _error(503, "director_model_not_configured", str(exc))
    except ContractError as exc:
        return _error(422, "contract_error", str(exc), {"import_id": import_id})
    return {
        "import_id": import_id,
        **proposal.model_dump(mode="json"),
    }


@router.post("/imports/{import_id:path}/validate", response_model=None)
async def validate_h3_import(import_id: str) -> dict[str, Any] | JSONResponse:
    store = H3ProfileStore()
    try:
        graph, workflow_sha256 = store.load_import_workflow_snapshot(import_id)
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
        mapping_sha256 = store.mapping_sha256(mapping)
        store.save_import_mapping(import_id, mapping)
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


@router.post("/imports/{import_id:path}/test", status_code=202, response_model=None)
async def test_h3_import(
    import_id: str,
    body: TestProfileRequest,
) -> dict[str, Any] | JSONResponse:
    """Start an isolated local test against a validated, unactivated import."""
    store = H3ProfileStore()
    try:
        workflow_sha256, mapping_sha256 = store.testable_import_identity(import_id)
        mapping = store.load_import_mapping(import_id)
    except ProfileStorageError as exc:
        return _store_error(exc)

    picture = _resolve_picture_asset(body.picture_asset_id)
    if picture is None:
        return _error(
            404,
            "picture_asset_not_found",
            "A readable Picture asset is required for the H3 profile test",
            {"picture_asset_id": body.picture_asset_id},
        )

    inputs = {"picture_1": picture}
    audio_keys: list[str] = []
    audio_binding = ""
    if body.audio_asset_id is not None:
        if mapping is None or mapping.audio_input_pattern is None:
            return _error(
                422,
                "audio_not_supported",
                "This H3 workflow profile does not support reference Audio",
                {"import_id": import_id},
            )
        audio = _resolve_voice_asset(body.audio_asset_id)
        if audio is None:
            return _error(
                404,
                "audio_asset_not_found",
                "A readable H3-ready Voice asset is required",
                {"audio_asset_id": body.audio_asset_id},
            )
        inputs["audio_1"] = audio
        audio_keys.append("audio_1")
        audio_binding = " <Audio 1> defines the optional voice reference."

    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="H3 workflow profile test",
        notes="Isolated setup test; output is not attached to a shot or Asset Library.",
        params={
            "h3_provider": "local",
            "h3_profile_test": True,
            "h3_profile_import_id": import_id,
            "h3_profile_test_workflow_sha256": workflow_sha256,
            "h3_profile_test_mapping_sha256": mapping_sha256,
            "prompt": _TEST_PROMPT.format(audio_binding=audio_binding),
            "dialogue": [],
            "frames": 56,
            "width": 864,
            "height": 480,
            "image_keys": ["picture_1"],
            "audio_keys": audio_keys,
        },
        seed=42,
        fixed_seed=True,
    )
    try:
        await start_pipeline_job(job, images=inputs)
    except ProfileStorageError as exc:
        return _store_error(exc)
    except (TypeError, ValueError) as exc:
        return _error(
            422,
            "test_job_invalid",
            str(exc),
            {"import_id": import_id},
        )
    return {
        "import_id": import_id,
        "job_id": job.id,
        "job_url": f"/api/h3-ref2va/jobs/{job.id}",
        "workflow_sha256": workflow_sha256,
        "mapping_sha256": mapping_sha256,
        "status": "queued",
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
