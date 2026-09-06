"""HTTP coverage for deterministic output-first H3 workflow setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.workflow_profiles.h3 import H3ProfileStore


@pytest.fixture
def sample_api_json() -> bytes:
    return (settings.workflows_dir / "h3_ref2va.api.json").read_bytes()


@pytest.fixture
def profile_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")

    class MetadataClient:
        async def get_object_info(self):
            return {
                "MiniMaxH3ReferenceToVideo": {
                    "display_name": "MiniMax H3 Ref2AV",
                    "output": ["CONDITIONING"],
                    "output_node": False,
                },
                "SaveVideo": {
                    "display_name": "Save Video",
                    "output": ["VIDEO"],
                    "output_node": True,
                },
                "VHS_VideoCombine": {
                    "display_name": "Video Combine",
                    "output": ["VHS_FILENAMES"],
                    "output_node": True,
                },
            }

    class ValidatingClient:
        async def validate_workflow(self, graph):
            assert any(
                node.get("class_type") == "MiniMaxH3ReferenceToVideo"
                for node in graph.values()
            )
            return {"valid": True, "error_count": 0, "warnings": []}

        async def aclose(self):
            return None

    monkeypatch.setattr("app.api.h3_workflow_profiles.ComfyClient", MetadataClient)
    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.ComfyMcpClient", ValidatingClient
    )
    with TestClient(create_app()) as client:
        yield client


def _import(client: TestClient, workflow: bytes, name: str = "custom.api.json") -> str:
    response = client.post(
        "/api/workflow-profiles/h3/imports",
        files={"workflow": (name, workflow, "application/json")},
    )
    assert response.status_code == 201, response.text
    return response.json()["import_id"]


def _select_output_and_mapping(client: TestClient, import_id: str, node_id="92"):
    base = f"/api/workflow-profiles/h3/imports/{import_id}"
    selected = client.put(f"{base}/output", json={"node_id": node_id})
    assert selected.status_code == 200, selected.text
    mapping = selected.json()["mapping"]
    saved = client.put(f"{base}/mapping", json=mapping)
    assert saved.status_code == 200, saved.text
    return mapping


def test_import_lists_named_output_then_reverse_discovers_h3(
    profile_client: TestClient, sample_api_json: bytes
) -> None:
    import_id = _import(profile_client, sample_api_json, "Cinema H3.api.json")
    base = f"/api/workflow-profiles/h3/imports/{import_id}"

    analysis = profile_client.get(f"{base}/analysis")
    assert analysis.status_code == 200
    output = analysis.json()["output_candidates"][0]
    assert output["node_id"] == "92"
    assert output["display_name"] == "Save Video"
    assert output["class_type"] == "SaveVideo"
    assert analysis.json()["h3_candidates"] == []

    selected = profile_client.put(f"{base}/output", json={"node_id": "92"})
    assert selected.status_code == 200
    body = selected.json()
    assert body["h3_candidates"][0]["node_id"] == "136"
    assert body["mapping"]["inputs"]["h3_node_id"] == "136"
    assert body["mapping"]["output"] == {
        "node_id": "92",
        "artifact_index": None,
    }


def test_mapping_proposal_route_is_removed(
    profile_client: TestClient, sample_api_json: bytes
) -> None:
    import_id = _import(profile_client, sample_api_json)

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/propose-mapping"
    )

    assert response.status_code in {404, 405}


def test_output_change_invalidates_mapping_and_lifecycle(
    profile_client: TestClient, sample_api_json: bytes
) -> None:
    graph = json.loads(sample_api_json)
    graph["999"] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": graph["92"]["inputs"]["video"],
            "filename_prefix": "alternate",
        },
        "_meta": {"title": "Alternate final video"},
    }
    import_id = _import(profile_client, json.dumps(graph).encode())
    base = f"/api/workflow-profiles/h3/imports/{import_id}"
    _select_output_and_mapping(profile_client, import_id)
    assert profile_client.get(f"{base}/analysis").json()["lifecycle"]["status"] == "mapped"

    changed = profile_client.put(f"{base}/output", json={"node_id": "999"})

    assert changed.status_code == 200
    assert changed.json()["mapping"] is not None
    assert changed.json()["mapping"]["output"]["node_id"] == "999"
    assert changed.json()["lifecycle"]["status"] == "draft"
    assert H3ProfileStore().load_import_mapping(import_id) is None


def test_rejects_nonterminal_or_unknown_output_selection(
    profile_client: TestClient, sample_api_json: bytes
) -> None:
    import_id = _import(profile_client, sample_api_json)

    response = profile_client.put(
        f"/api/workflow-profiles/h3/imports/{import_id}/output",
        json={"node_id": "136"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "output_selection_error"
    assert "136" in response.json()["message"]


def test_confirmed_boundary_validates_without_agent(
    profile_client: TestClient, sample_api_json: bytes
) -> None:
    import_id = _import(profile_client, sample_api_json)
    _select_output_and_mapping(profile_client, import_id)

    response = profile_client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/validate"
    )

    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True
    assert response.json()["lifecycle"]["status"] == "validated"


def test_mapping_must_match_selected_output(
    profile_client: TestClient, sample_api_json: bytes
) -> None:
    import_id = _import(profile_client, sample_api_json)
    mapping = profile_client.put(
        f"/api/workflow-profiles/h3/imports/{import_id}/output",
        json={"node_id": "92"},
    ).json()["mapping"]
    mapping["output"]["node_id"] = "136"

    response = profile_client.put(
        f"/api/workflow-profiles/h3/imports/{import_id}/mapping", json=mapping
    )

    assert response.status_code == 422
    assert response.json()["code"] == "input_selection_error"


def test_import_reports_each_malformed_node_by_name(profile_client: TestClient) -> None:
    graph = {
        "58": {"inputs": {}, "_meta": {"title": "Broken Loader"}},
        "140": [],
    }

    response = profile_client.post(
        "/api/workflow-profiles/h3/imports",
        files={
            "workflow": ("bad.json", json.dumps(graph).encode(), "application/json")
        },
    )

    assert response.status_code == 400
    issues = response.json()["details"]["issues"]
    assert {(item["node_id"], item["node_name"]) for item in issues} == {
        ("58", "Broken Loader"),
        ("140", "Node 140"),
    }
    assert not H3ProfileStore().imports_dir.exists()


def test_routes_reject_non_opaque_ids_with_structured_errors(
    profile_client: TestClient,
) -> None:
    response = profile_client.get(
        "/api/workflow-profiles/h3/imports/..%2Foutside/analysis"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_import_id"


def test_profile_listing_starts_on_builtin_official(profile_client: TestClient) -> None:
    response = profile_client.get("/api/workflow-profiles/h3")

    assert response.status_code == 200
    body = response.json()
    assert body["active"]["profile_id"] == "builtin-official-h3"
    assert body["active"]["contract_version"] == 2


def test_analysis_survives_object_info_failure_with_warning(
    profile_client: TestClient,
    sample_api_json: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_id = _import(profile_client, sample_api_json)

    class OfflineMetadataClient:
        async def get_object_info(self):
            raise RuntimeError("offline")

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.ComfyClient", OfflineMetadataClient
    )

    response = profile_client.get(
        f"/api/workflow-profiles/h3/imports/{import_id}/analysis"
    )

    assert response.status_code == 200
    assert response.json()["output_candidates"]
    assert any(
        item["code"] == "object_info_unavailable"
        for item in response.json()["issues"]
    )
