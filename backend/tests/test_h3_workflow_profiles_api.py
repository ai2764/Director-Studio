"""HTTP lifecycle coverage for H3 workflow profile setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.workflow_profiles.h3 import H3ProfileStore, ProfileChangedError


@pytest.fixture
def sample_api_json() -> bytes:
    return (settings.workflows_dir / "h3_ref2va.api.json").read_bytes()


@pytest.fixture
def profile_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")

    class ValidatingClient:
        async def validate_workflow(self, graph):
            assert graph["136"]["class_type"] == "MiniMaxH3ReferenceToVideo"
            return {"valid": True, "error_count": 0, "warnings": []}

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.ComfyMcpClient",
        ValidatingClient,
    )
    with TestClient(create_app()) as client:
        yield client


def _import(profile_client: TestClient, workflow: bytes) -> str:
    response = profile_client.post(
        "/api/workflow-profiles/h3/imports",
        files={"workflow": ("custom.api.json", workflow, "application/json")},
    )
    assert response.status_code == 201, response.text
    return response.json()["import_id"]


def _mark_test_succeeded(import_id: str) -> None:
    store = H3ProfileStore()
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    store.record_test_success(
        import_id,
        workflow_sha256=workflow_sha256,
        mapping_sha256=mapping_sha256,
        job_id="job_profile_test",
    )


def test_import_analyze_validate_activate_and_select_builtin(
    profile_client: TestClient,
    sample_api_json: bytes,
) -> None:
    import_id = _import(profile_client, sample_api_json)

    analysis = profile_client.get(
        f"/api/workflow-profiles/h3/imports/{import_id}/analysis"
    )
    assert analysis.status_code == 200
    assert analysis.json()["compatibility"] == "auto_compatible"

    validated = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/validate"
    )
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    _mark_test_succeeded(import_id)
    activated = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/activate"
    )
    assert activated.status_code == 200
    custom_id = activated.json()["profile_id"]
    assert custom_id.startswith("custom-")
    assert H3ProfileStore().resolve_active().profile_id == custom_id

    selected = profile_client.post(
        "/api/workflow-profiles/h3/select",
        json={"profile_id": "builtin-official-h3"},
    )
    assert selected.status_code == 200
    assert selected.json()["active"]["profile_id"] == "builtin-official-h3"


def test_activate_rejects_profile_without_successful_test(
    profile_client: TestClient,
    sample_api_json: bytes,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    assert (
        profile_client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/validate"
        ).status_code
        == 200
    )

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/activate"
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "test_required",
        "message": "A successful test for this workflow is required before activation",
        "details": {"import_id": import_id},
    }


def test_import_rejects_non_json_and_does_not_accept_paths(
    profile_client: TestClient,
) -> None:
    response = profile_client.post(
        "/api/workflow-profiles/h3/imports",
        files={"workflow": ("bad.api.json", b"not-json", "application/json")},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_workflow_json"
    assert (
        profile_client.post(
            "/api/workflow-profiles/h3/imports",
            json={"workflow_path": "C:/private/workflow.api.json"},
        ).status_code
        == 422
    )


def test_routes_reject_non_opaque_ids_with_structured_errors(
    profile_client: TestClient,
) -> None:
    response = profile_client.get(
        "/api/workflow-profiles/h3/imports/..%2Foutside/analysis"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_import_id"


def test_mapping_body_is_strict_and_contract_failures_are_structured(
    profile_client: TestClient,
    sample_api_json: bytes,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    mapping = profile_client.get(
        f"/api/workflow-profiles/h3/imports/{import_id}/analysis"
    ).json()["mapping"]
    mapping["seed_node_id"] = "missing"

    saved = profile_client.put(
        f"/api/workflow-profiles/h3/imports/{import_id}/mapping",
        json=mapping,
    )
    assert saved.status_code == 200

    invalid = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/validate"
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "contract_validation_failed"
    assert invalid.json()["details"]["issues"]

    mapping["workflow_path"] = "C:/private/workflow.api.json"
    strict = profile_client.put(
        f"/api/workflow-profiles/h3/imports/{import_id}/mapping",
        json=mapping,
    )
    assert strict.status_code == 422


def test_activation_rejects_test_evidence_for_changed_workflow(
    profile_client: TestClient,
    sample_api_json: bytes,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    assert (
        profile_client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/validate"
        ).status_code
        == 200
    )
    _mark_test_succeeded(import_id)
    workflow_path = H3ProfileStore().import_workflow_path(import_id)
    graph = json.loads(workflow_path.read_text(encoding="utf-8"))
    graph["136"]["inputs"]["prompt"] = "changed after test"
    workflow_path.write_text(json.dumps(graph), encoding="utf-8")

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/activate"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "profile_changed"


def test_validation_rejects_workflow_changed_during_live_check(
    profile_client: TestClient,
    sample_api_json: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_id = _import(profile_client, sample_api_json)

    class MutatingValidationClient:
        async def validate_workflow(self, _graph):
            path = H3ProfileStore().import_workflow_path(import_id)
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["136"]["inputs"]["prompt"] = "changed during validation"
            path.write_text(json.dumps(changed), encoding="utf-8")
            return {"valid": True, "error_count": 0, "warnings": []}

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.ComfyMcpClient",
        MutatingValidationClient,
    )

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/validate"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "profile_changed"


def test_test_success_rejects_mapping_changed_since_job_snapshot(
    profile_client: TestClient,
    sample_api_json: bytes,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    analysis = profile_client.get(
        f"/api/workflow-profiles/h3/imports/{import_id}/analysis"
    ).json()
    mapping = analysis["mapping"]
    assert (
        profile_client.put(
            f"/api/workflow-profiles/h3/imports/{import_id}/mapping",
            json=mapping,
        ).status_code
        == 200
    )
    store = H3ProfileStore()
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    store.save_import_mapping(
        import_id,
        store.load_import_mapping(import_id).model_copy(
            update={"output_fields": ("files",)}
        ),
    )

    with pytest.raises(ProfileChangedError, match="changed during test"):
        store.record_test_success(
            import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
            job_id="job_profile_test",
        )


def test_validation_uses_graph_hash_captured_before_mapping_persistence(
    profile_client: TestClient,
    sample_api_json: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    original = H3ProfileStore.save_import_mapping

    def mutate_workflow_after_mapping_save(self, requested_id, mapping):
        original(self, requested_id, mapping)
        path = self.import_workflow_path(requested_id)
        changed = json.loads(path.read_text(encoding="utf-8"))
        changed["136"]["inputs"]["prompt"] = "changed before MCP validation"
        path.write_text(json.dumps(changed), encoding="utf-8")

    monkeypatch.setattr(
        H3ProfileStore,
        "save_import_mapping",
        mutate_workflow_after_mapping_save,
    )

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/validate"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "profile_changed"


def test_validation_uses_mapping_hash_from_mapping_snapshot(
    profile_client: TestClient,
    sample_api_json: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    original = H3ProfileStore.save_import_mapping

    def replace_mapping_after_save(self, requested_id, mapping):
        original(self, requested_id, mapping)
        replacement = mapping.model_copy(update={"output_fields": ("files",)})
        mapping_path = self.import_workflow_path(requested_id).parent / "mapping.json"
        mapping_path.write_text(replacement.model_dump_json(), encoding="utf-8")

    monkeypatch.setattr(
        H3ProfileStore,
        "save_import_mapping",
        replace_mapping_after_save,
    )

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/validate"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "profile_changed"


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param("mapping", id="mapping"),
        pytest.param("status", id="status"),
    ],
)
def test_active_custom_profile_falls_back_when_installed_metadata_changes(
    profile_client: TestClient,
    sample_api_json: bytes,
    tamper: str,
) -> None:
    import_id = _import(profile_client, sample_api_json)
    assert (
        profile_client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/validate"
        ).status_code
        == 200
    )
    _mark_test_succeeded(import_id)
    activated = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/activate"
    ).json()
    store = H3ProfileStore()
    profile_path = store.profile_path(activated["profile_id"])
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if tamper == "mapping":
        profile["mapping"]["output_fields"] = ["files"]
    else:
        profile["status"] = "tested"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    resolved = store.resolve_active()

    assert resolved.source == "builtin"
    assert resolved.warning is not None
    assert resolved.warning.code == "profile_changed"


def test_profile_listing_reports_active_and_installed_builtin(
    profile_client: TestClient,
) -> None:
    response = profile_client.get("/api/workflow-profiles/h3")

    assert response.status_code == 200
    body = response.json()
    assert body["active"]["profile_id"] == "builtin-official-h3"
    assert body["profiles"] == [
        {
            "profile_id": "builtin-official-h3",
            "display_name": "Built-in Official H3",
            "source": "builtin",
            "status": "active",
            "workflow_sha256": body["active"]["workflow_sha256"],
        }
    ]
