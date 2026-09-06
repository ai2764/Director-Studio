"""Boundary-only validation for opaque custom H3 workflows."""

from __future__ import annotations

import json
from pathlib import Path

from app.workflow_profiles.h3 import (
    H3BoundaryMapping,
    H3InputMapping,
    H3OutputSelection,
)
from app.workflow_profiles.h3.validator import validate_h3_contract


FIXTURE = Path(__file__).parent / "fixtures" / "h3_multistage_vhs.api.json"


def graph() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def mapping(**input_updates: object) -> H3BoundaryMapping:
    values: dict[str, object] = {
        "h3_node_id": "265",
        "prompt_input": "prompt",
        "width_input": "width",
        "height_input": "height",
        "frames_input": "length",
        "picture_input_pattern": "ref_images.ref_image_{index}",
        "audio_input_pattern": None,
        "seed_node_id": None,
        "seed_input": None,
    }
    values.update(input_updates)
    return H3BoundaryMapping(
        inputs=H3InputMapping.model_validate(values),
        output=H3OutputSelection(node_id="214"),
    )


def test_accepts_multistage_graph_without_seed_mapping() -> None:
    report = validate_h3_contract(graph(), mapping())

    assert report.valid is True
    assert report.issues == ()


def test_rejects_h3_node_not_upstream_of_selected_output() -> None:
    report = validate_h3_contract(graph(), mapping(h3_node_id="400"))

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"unreachable_mapping"}


def test_rejects_nonterminal_selected_output() -> None:
    selected = mapping().model_copy(
        update={"output": H3OutputSelection(node_id="210")}
    )

    report = validate_h3_contract(graph(), selected)

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"nonterminal_output"}


def test_rejects_noncanonical_or_missing_h3_socket() -> None:
    report = validate_h3_contract(graph(), mapping(prompt_input="text"))

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"noncanonical_mapping"}


def test_optional_seed_must_be_upstream_and_have_selected_input() -> None:
    report = validate_h3_contract(
        graph(), mapping(seed_node_id="700", seed_input="noise_seed")
    )

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"unreachable_mapping"}


def test_internal_fixed_file_dependency_is_left_for_comfy_validation() -> None:
    custom = graph()
    custom["44"] = {
        "class_type": "LoadAudio",
        "inputs": {"audio": "workflow-owned.wav"},
    }
    custom["210"]["inputs"]["guide_audio"] = ["44", 0]  # type: ignore[index]

    report = validate_h3_contract(custom, mapping())

    assert report.valid is True


def test_rejects_dangling_internal_link_before_comfy_submission() -> None:
    custom = graph()
    custom["210"]["inputs"]["missing"] = ["does-not-exist", 0]  # type: ignore[index]

    report = validate_h3_contract(custom, mapping())

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"dangling_link"}


def test_rejects_first_or_last_frame_semantics_on_selected_h3_boundary() -> None:
    custom = graph()
    custom["265"]["inputs"]["last_frame"] = ["10", 0]  # type: ignore[index]

    report = validate_h3_contract(custom, mapping())

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"unsupported_boundary_input"}


def test_contains_deep_graph_failure_as_structured_issue() -> None:
    custom = graph()
    nested: object = "value"
    for _ in range(2_000):
        nested = {"nested": nested}
    custom["2"]["inputs"]["deep"] = nested  # type: ignore[index]

    report = validate_h3_contract(custom, mapping())

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"invalid_structure"}
