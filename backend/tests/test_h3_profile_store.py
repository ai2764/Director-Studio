"""Tests for durable H3 workflow-profile resolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import settings
from app.workflow_profiles.h3 import (
    H3BoundaryMapping,
    H3ProfileStore,
    H3WorkflowProfile,
    ProfileStorageError,
)


def _mapping() -> H3BoundaryMapping:
    return H3BoundaryMapping(
        h3_node_id="136",
        prompt_input="prompt",
        width_input="width",
        height_input="height",
        frames_input="length",
        picture_input_pattern="ref_images.ref_image_{index}",
        audio_input_pattern="ref_audios.ref_audio_{index}",
        seed_node_id="129",
        seed_input="noise_seed",
        saver_node_id="92",
        output_prefix_input="filename_prefix",
    )


def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> H3ProfileStore:
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    return H3ProfileStore()


def _install_custom_profile(
    store: H3ProfileStore,
    workflow: dict[str, object] | None = None,
    *,
    status: str = "active",
    with_evidence: bool = False,
) -> None:
    workflow = workflow or {
        "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}
    }
    workflow_bytes = json.dumps(
        workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    profile = H3WorkflowProfile(
        id="custom",
        workflow_sha256=hashlib.sha256(workflow_bytes).hexdigest(),
        mapping=_mapping(),
        status=status,
    )
    mapping_sha256 = store.mapping_sha256(profile.mapping)
    evidence = {
        "valid": True,
        "contract_version": 1,
        "workflow_sha256": profile.workflow_sha256,
        "mapping_sha256": mapping_sha256,
        "report": {"valid": True},
        "comfy": {"valid": True},
    }
    test_record = {
        "status": "succeeded",
        "workflow_sha256": profile.workflow_sha256,
        "mapping_sha256": mapping_sha256,
        "job_id": "job_store_fixture",
    }
    store.install_profile(
        profile,
        workflow,
        validation_record=evidence if with_evidence else None,
        test_record=test_record if with_evidence else None,
    )


def installed_custom_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> H3ProfileStore:
    store = isolated_store(tmp_path, monkeypatch)
    _install_custom_profile(store, with_evidence=True)
    store.select_profile("custom")
    return store


def test_fresh_store_resolves_builtin_official(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")

    resolved = H3ProfileStore().resolve_active()

    assert resolved.profile_id == "builtin-official-h3"
    assert resolved.source == "builtin"
    assert resolved.workflow_sha256
    assert resolved.mapping.h3_node_id == "136"
    assert resolved.mapping.saver_node_id == "92"


def test_active_pointer_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)

    with pytest.raises(ProfileStorageError):
        store.select_profile("../outside")


def test_changed_custom_workflow_falls_back_to_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = installed_custom_store(tmp_path, monkeypatch)
    store.workflow_path("custom").write_text("{}", encoding="utf-8")

    resolved = store.resolve_active()

    assert resolved.source == "builtin"
    assert resolved.warning is not None
    assert resolved.warning.code == "profile_changed"


def test_custom_profile_round_trips_utf8_bom_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = installed_custom_store(tmp_path, monkeypatch)
    profile = H3WorkflowProfile.model_validate_json(
        store.profile_path("custom").read_text("utf-8")
    )
    workflow = {
        "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"prompt": "å"}}
    }
    encoded = json.dumps(workflow, ensure_ascii=False, sort_keys=True).encode("utf-8")
    bom_encoded = b"\xef\xbb\xbf" + encoded
    profile = profile.model_copy(
        update={"workflow_sha256": hashlib.sha256(bom_encoded).hexdigest()}
    )
    store.workflow_path("custom").write_bytes(bom_encoded)
    store.profile_path("custom").write_text(profile.model_dump_json(), encoding="utf-8")
    evidence_path = store.profile_path("custom").parent / "validation.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["workflow_sha256"] = profile.workflow_sha256
    evidence["test"]["workflow_sha256"] = profile.workflow_sha256
    profile_bytes = json.dumps(
        profile.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    evidence["profile_sha256"] = hashlib.sha256(profile_bytes).hexdigest()
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    store.select_profile("custom")

    resolved = store.resolve_active()

    assert resolved.source == "custom"
    assert resolved.workflow["136"]["inputs"]["prompt"] == "å"


def test_profile_models_forbid_unknown_fields_and_numeric_node_ids() -> None:
    fields = _mapping().model_dump()
    fields["unexpected"] = "value"

    with pytest.raises(ValidationError):
        H3BoundaryMapping.model_validate(fields)

    with pytest.raises(ValidationError):
        H3BoundaryMapping.model_validate({**_mapping().model_dump(), "h3_node_id": 136})


def test_generated_import_ids_are_opaque_and_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)

    first = store.create_import({"1": {"class_type": "Test", "inputs": {}}})
    second = store.create_import({"1": {"class_type": "Test", "inputs": {}}})

    assert first != second
    assert first.startswith("imp-")
    assert store.import_workflow_path(first).is_file()


@pytest.mark.parametrize("status", ["draft", "mapped", "validated", "broken"])
def test_select_profile_rejects_custom_profiles_not_ready_for_activation(
    status: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)
    _install_custom_profile(store, status=status)

    with pytest.raises(ProfileStorageError, match="tested or active"):
        store.select_profile("custom")


def test_select_profile_rejects_custom_profile_without_durable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)
    _install_custom_profile(store, status="active")

    with pytest.raises(ProfileStorageError, match="validation and test evidence"):
        store.select_profile("custom")


def test_resolve_active_falls_back_when_pointer_targets_untested_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)
    _install_custom_profile(store, status="draft")
    profile = H3WorkflowProfile.model_validate_json(
        store.profile_path("custom").read_text("utf-8")
    )
    store.active_path.parent.mkdir(parents=True, exist_ok=True)
    store.active_path.write_text(
        json.dumps(
            {"profile_id": "custom", "workflow_sha256": profile.workflow_sha256}
        ),
        encoding="utf-8",
    )

    resolved = store.resolve_active()

    assert resolved.source == "builtin"
    assert resolved.warning is not None
    assert resolved.warning.code == "profile_unavailable"


@pytest.mark.parametrize(
    "workflow",
    [
        {"1": {"class_type": "LoadImage", "inputs": {}}},
        {
            "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}},
            "137": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}},
        },
        {
            "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}},
            "137": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {}},
        },
        {
            "136": {
                "class_type": "MiniMaxH3ReferenceToVideo",
                "inputs": {"ref_frame": "not allowed"},
            }
        },
        {
            "136": {
                "class_type": "MiniMaxH3ReferenceToVideo",
                "inputs": {"last_frame": "not allowed"},
            }
        },
    ],
    ids=["no-h3", "duplicate-h3", "i2v", "ref-frame", "last-frame"],
)
def test_install_profile_rejects_non_pure_ref2av_graphs(
    workflow: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)

    with pytest.raises(ProfileStorageError, match="pure Ref2AV"):
        _install_custom_profile(store, workflow)
