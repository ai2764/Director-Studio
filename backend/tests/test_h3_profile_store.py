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


def installed_custom_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> H3ProfileStore:
    store = isolated_store(tmp_path, monkeypatch)
    workflow = {"136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}}
    workflow_bytes = json.dumps(
        workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    profile = H3WorkflowProfile(
        id="custom",
        workflow_sha256=hashlib.sha256(workflow_bytes).hexdigest(),
        mapping=_mapping(),
        status="active",
    )
    store.install_profile(profile, workflow)
    store.select_profile("custom")
    return store


def test_fresh_store_resolves_builtin_official(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")

    resolved = H3ProfileStore().resolve_active()

    assert resolved.profile_id == "builtin-official-h3"
    assert resolved.source == "builtin"
    assert resolved.workflow_sha256
    assert resolved.mapping.h3_node_id == "136"
    assert resolved.mapping.saver_node_id == "92"


def test_active_pointer_rejects_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = isolated_store(tmp_path, monkeypatch)

    with pytest.raises(ProfileStorageError):
        store.select_profile("../outside")


def test_changed_custom_workflow_falls_back_to_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = installed_custom_store(tmp_path, monkeypatch)
    store.workflow_path("custom").write_text("{}", encoding="utf-8")

    resolved = store.resolve_active()

    assert resolved.source == "builtin"
    assert resolved.warning is not None
    assert resolved.warning.code == "profile_changed"


def test_custom_profile_round_trips_utf8_bom_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = installed_custom_store(tmp_path, monkeypatch)
    profile = H3WorkflowProfile.model_validate_json(store.profile_path("custom").read_text("utf-8"))
    workflow = {"136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"prompt": "å"}}}
    encoded = json.dumps(workflow, ensure_ascii=False, sort_keys=True).encode("utf-8")
    bom_encoded = b"\xef\xbb\xbf" + encoded
    profile = profile.model_copy(
        update={"workflow_sha256": hashlib.sha256(bom_encoded).hexdigest()}
    )
    store.workflow_path("custom").write_bytes(bom_encoded)
    store.profile_path("custom").write_text(profile.model_dump_json(), encoding="utf-8")
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


def test_generated_import_ids_are_opaque_and_unique(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = isolated_store(tmp_path, monkeypatch)

    first = store.create_import({"1": {"class_type": "Test", "inputs": {}}})
    second = store.create_import({"1": {"class_type": "Test", "inputs": {}}})

    assert first != second
    assert first.startswith("imp-")
    assert store.import_workflow_path(first).is_file()
