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
    H3InputMapping,
    H3OutputSelection,
    H3ProfileStore,
    H3WorkflowProfile,
    ProfileStorageError,
)


def test_contract_v2_separates_inputs_from_output_and_allows_workflow_seed() -> None:
    mapping = H3BoundaryMapping(
        inputs=H3InputMapping(
            h3_node_id="136",
            prompt_input="prompt",
            width_input="width",
            height_input="height",
            frames_input="length",
            picture_input_pattern="ref_images.ref_image_{index}",
            audio_input_pattern="ref_audios.ref_audio_{index}",
            seed_node_id=None,
            seed_input=None,
        ),
        output=H3OutputSelection(node_id="92"),
    )
    profile = H3WorkflowProfile(
        id="custom-v2",
        workflow_sha256="a" * 64,
        mapping=mapping,
        status="mapped",
    )

    assert profile.contract_version == 2
    assert profile.mapping.inputs.seed_node_id is None
    assert profile.mapping.output.node_id == "92"
    assert profile.mapping.output.artifact_index is None


def test_contract_v2_requires_seed_node_and_input_as_a_pair() -> None:
    with pytest.raises(ValidationError, match="seed_node_id and seed_input"):
        H3InputMapping(
            h3_node_id="136",
            prompt_input="prompt",
            width_input="width",
            height_input="height",
            frames_input="length",
            picture_input_pattern="ref_images.ref_image_{index}",
            seed_node_id="129",
            seed_input=None,
        )

    with pytest.raises(ValidationError):
        H3OutputSelection(node_id="92", artifact_index=-1)


def test_boundary_hash_ignores_only_observed_artifact_choice() -> None:
    base = H3BoundaryMapping(
        inputs=H3InputMapping(
            h3_node_id="136",
            prompt_input="prompt",
            width_input="width",
            height_input="height",
            frames_input="length",
            picture_input_pattern="ref_images.ref_image_{index}",
        ),
        output=H3OutputSelection(node_id="92"),
    )
    selected = base.model_copy(
        update={"output": H3OutputSelection(node_id="92", artifact_index=1)}
    )
    changed_output = base.model_copy(
        update={"output": H3OutputSelection(node_id="214")}
    )

    assert H3ProfileStore.boundary_sha256(base) == H3ProfileStore.boundary_sha256(
        selected
    )
    assert H3ProfileStore.boundary_sha256(base) != H3ProfileStore.boundary_sha256(
        changed_output
    )


def _mapping() -> H3BoundaryMapping:
    return H3BoundaryMapping(
        inputs=H3InputMapping(
            h3_node_id="136",
            prompt_input="prompt",
            width_input="width",
            height_input="height",
            frames_input="length",
            picture_input_pattern="ref_images.ref_image_{index}",
            audio_input_pattern="ref_audios.ref_audio_{index}",
            seed_node_id="129",
            seed_input="noise_seed",
        ),
        output=H3OutputSelection(node_id="92"),
    )


def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> H3ProfileStore:
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    return H3ProfileStore()


def _valid_workflow() -> dict[str, object]:
    return {
        "136": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"prompt": "", "width": 864, "height": 480, "length": 56},
        },
        "129": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        "140": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {"positive": ["136", 0], "noise": ["129", 0]},
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["140", 0], "filename_prefix": "video/H3"},
        },
    }


def _install_custom_profile(
    store: H3ProfileStore,
    workflow: dict[str, object] | None = None,
    *,
    status: str = "active",
    with_evidence: bool = False,
) -> None:
    workflow = workflow or _valid_workflow()
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
        "contract_version": 2,
        "workflow_sha256": profile.workflow_sha256,
        "mapping_sha256": mapping_sha256,
        "report": {"valid": True},
        "comfy": {"valid": True},
    }
    test_record = {
        "status": "succeeded",
        "contract_version": 2,
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
    assert resolved.mapping.inputs.h3_node_id == "136"
    assert resolved.mapping.output.node_id == "92"


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
    workflow = _valid_workflow()
    workflow["136"]["inputs"]["prompt"] = "å"  # type: ignore[index]
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
        H3BoundaryMapping.model_validate(
            {
                **_mapping().model_dump(),
                "inputs": {**_mapping().inputs.model_dump(), "h3_node_id": 136},
            }
        )


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
        {
            **_valid_workflow(),
            "136": {"class_type": "LoadImage", "inputs": {}},
        },
        {
            **_valid_workflow(),
            "136": {
                **_valid_workflow()["136"],  # type: ignore[dict-item]
                "inputs": {
                    **_valid_workflow()["136"]["inputs"],  # type: ignore[index]
                    "ref_frame": "not allowed",
                },
            },
        },
        {
            **_valid_workflow(),
            "136": {
                **_valid_workflow()["136"],  # type: ignore[dict-item]
                "inputs": {
                    **_valid_workflow()["136"]["inputs"],  # type: ignore[index]
                    "last_frame": "not allowed",
                },
            },
        },
    ],
    ids=["selected-not-h3", "ref-frame", "last-frame"],
)
def test_install_profile_rejects_invalid_confirmed_boundary(
    workflow: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)

    with pytest.raises(ProfileStorageError, match="confirmed H3 boundary"):
        _install_custom_profile(store, workflow)


def test_install_profile_allows_unrelated_h3_and_i2v_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = isolated_store(tmp_path, monkeypatch)
    workflow = _valid_workflow()
    workflow["500"] = {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {"prompt": "", "width": 1, "height": 1, "length": 6},
    }
    workflow["501"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {}}

    _install_custom_profile(store, workflow, status="draft")

    assert store.profile_path("custom").is_file()
