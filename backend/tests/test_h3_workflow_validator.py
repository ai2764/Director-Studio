"""Exact contract validation for custom H3 API workflows."""

from __future__ import annotations

from test_h3_workflow_inspector import unique_graph

from app.workflow_profiles.h3.inspector import inspect_h3_workflow
from app.workflow_profiles.h3.models import H3BoundaryMapping
from app.workflow_profiles.h3.validator import validate_h3_contract


def _mapping(**updates: str | None) -> H3BoundaryMapping:
    values: dict[str, object] = {
        "h3_node_id": "136",
        "prompt_input": "prompt",
        "width_input": "width",
        "height_input": "height",
        "frames_input": "length",
        "picture_input_pattern": "ref_images.ref_image_{index}",
        "audio_input_pattern": "ref_audios.ref_audio_{index}",
        "seed_node_id": "129",
        "seed_input": "noise_seed",
        "saver_node_id": "92",
        "output_prefix_input": "filename_prefix",
    }
    values.update(updates)
    return H3BoundaryMapping.model_validate(values)


def test_validator_fills_a_synthetic_boundary_job_and_accepts_exact_mapping() -> None:
    graph = unique_graph()
    mapping = _mapping()

    report = validate_h3_contract(graph, mapping)

    assert report.valid is True
    assert report.issues == ()
    assert report.synthetic_boundary == {
        "pictures": 1,
        "audios": 0,
        "width": 864,
        "height": 480,
        "frames": 56,
        "seed": 42,
    }


def test_validator_rejects_mapped_h3_input_that_does_not_exist() -> None:
    report = validate_h3_contract(unique_graph(), _mapping(prompt_input="text"))

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"missing_mapped_input"}
    assert "text" in report.issues[0].message


def test_validator_rejects_mapping_to_disconnected_candidate() -> None:
    report = validate_h3_contract(unique_graph(), _mapping(seed_node_id="700"))

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"unreachable_mapping"}


def test_validator_rejects_dangling_links_after_synthetic_fill() -> None:
    graph = unique_graph()
    graph["140"]["inputs"]["missing"] = ["does-not-exist", 0]  # type: ignore[index]

    report = validate_h3_contract(graph, _mapping())

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"dangling_link"}
    assert report.issues[0].node_id == "140"
    assert report.issues[0].input_name == "missing"


def test_validator_reports_but_accepts_explicit_fixed_dependency() -> None:
    graph = unique_graph()
    graph["44"] = {
        "class_type": "LoadImage",
        "inputs": {"image": "fixed-control.png"},
    }
    graph["140"]["inputs"]["control"] = ["44", 0]  # type: ignore[index]

    analysis = inspect_h3_workflow(graph)
    report = validate_h3_contract(graph, _mapping())

    assert [item.node_id for item in analysis.fixed_dependencies] == ["44"]
    assert report.valid is True
    assert report.fixed_dependencies == analysis.fixed_dependencies


def test_validator_rejects_file_loader_without_explicit_file_input() -> None:
    graph = unique_graph()
    graph["44"] = {"class_type": "LoadAudio", "inputs": {}}
    graph["140"]["inputs"]["control"] = ["44", 0]  # type: ignore[index]

    report = validate_h3_contract(graph, _mapping())

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"unmapped_file_input"}


def test_validator_rejects_forbidden_graph_even_with_plausible_mapping() -> None:
    graph = unique_graph()
    graph["136"]["inputs"]["last_frame"] = ["128", 0]  # type: ignore[index]

    report = validate_h3_contract(graph, _mapping())

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"forbidden_semantics"}
