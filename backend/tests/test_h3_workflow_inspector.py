"""Deterministic inspection coverage for imported H3 API workflows."""

from __future__ import annotations

import json

import pytest

from app.workflow_profiles.h3.inspector import inspect_h3_workflow


@pytest.mark.parametrize(
    "title",
    [
        r"C:\Users\private\workflow.json",
        "/home/private/workflow.json",
        r"\\server\private\workflow.json",
    ],
)
def test_agent_manifest_redacts_absolute_node_titles(title):
    graph = unique_graph()
    graph["136"]["_meta"] = {"title": title}
    analysis = inspect_h3_workflow(graph)
    node = next(
        node for node in analysis.agent_manifest["nodes"] if node["node_id"] == "136"
    )
    assert node["title"] == "<redacted-path>"


def unique_graph() -> dict[str, object]:
    return {
        "136": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "prompt": "",
                "width": 864,
                "height": 480,
                "length": 124,
                "clip": ["128", 0],
            },
            "_meta": {"title": "H3\u0000 Reference\nNode"},
        },
        "128": {"class_type": "CLIPLoader", "inputs": {}},
        "129": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": 1, "api_token": "do-not-leak"},
        },
        "140": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {"positive": ["136", 0], "noise": ["129", 0]},
        },
        "130": {
            "class_type": "VAEDecodeTiled",
            "inputs": {"samples": ["140", 0]},
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["130", 0],
                "filename_prefix": "video/H3",
                "authorization": "Bearer private",
            },
            "_meta": {"title": "Final video"},
        },
        "700": {"class_type": "RandomNoise", "inputs": {"noise_seed": 9}},
        "701": {
            "class_type": "SaveVideo",
            "inputs": {"filename_prefix": r"C:\\private\\decoy.mp4"},
        },
    }


def graph_with_preview_and_final_savers() -> dict[str, object]:
    graph = unique_graph()
    graph["148"] = {
        "class_type": "SaveVideo",
        "inputs": {"video": ["130", 0], "filename_prefix": "preview/H3"},
        "_meta": {"title": "Preview"},
    }
    return graph


def graph_with_forbidden(forbidden: str) -> dict[str, object]:
    graph = unique_graph()
    if forbidden == "MiniMaxH3ImageToVideo":
        graph["500"] = {"class_type": forbidden, "inputs": {}}
    else:
        graph["136"]["inputs"][forbidden] = ["128", 0]  # type: ignore[index]
    return graph


def test_inspector_auto_maps_unique_ref2av_graph() -> None:
    analysis = inspect_h3_workflow(unique_graph())

    assert analysis.compatibility == "auto_compatible"
    assert analysis.mapping is not None
    assert analysis.mapping.h3_node_id == "136"
    assert analysis.mapping.seed_node_id == "129"
    assert analysis.mapping.saver_node_id == "92"


def test_inspector_requires_confirmation_for_two_reachable_savers() -> None:
    analysis = inspect_h3_workflow(graph_with_preview_and_final_savers())

    assert analysis.compatibility == "needs_confirmation"
    assert {candidate.node_id for candidate in analysis.saver_candidates} == {
        "92",
        "148",
    }
    assert {candidate.node_id for candidate in analysis.seed_candidates} == {"129"}


@pytest.mark.parametrize(
    "forbidden", ["MiniMaxH3ImageToVideo", "ref_frame", "last_frame"]
)
def test_inspector_rejects_non_ref2av_semantics(forbidden: str) -> None:
    analysis = inspect_h3_workflow(graph_with_forbidden(forbidden))

    assert analysis.compatibility == "unsupported"
    assert any("pure Ref2AV" in issue.message for issue in analysis.issues)


def test_inspector_manifest_is_compact_deterministic_and_redacted() -> None:
    graph = unique_graph()
    graph["125"] = {
        "class_type": "ModelLoader",
        "inputs": {
            "model_name": "h3.safetensors",
            "model_path": "/Users/private/h3.safetensors",
        },
        "_meta": {"title": "Model\r\nLoader"},
    }
    graph["140"]["inputs"]["model"] = ["125", 0]  # type: ignore[index]

    first = inspect_h3_workflow(graph).agent_manifest
    second = inspect_h3_workflow(dict(reversed(list(graph.items())))).agent_manifest

    assert first == second
    assert list(first) == ["nodes"]
    nodes = {node["node_id"]: node for node in first["nodes"]}
    assert nodes["136"]["title"] == "H3 Reference Node"
    assert nodes["125"]["title"] == "Model Loader"
    assert nodes["125"]["defaults"] == {
        "model_name": "h3.safetensors",
        "model_path": "<redacted>",
    }
    assert nodes["129"]["defaults"]["api_token"] == "<redacted>"
    assert nodes["701"]["defaults"]["filename_prefix"] == "<redacted-path>"
    assert "do-not-leak" not in json.dumps(first)
    assert nodes["129"]["candidate_roles"] == ["seed"]
    assert nodes["92"]["candidate_roles"] == ["saver"]
    assert nodes["136"]["candidate_roles"] == ["h3"]
    assert nodes["700"]["candidate_roles"] == []
    assert nodes["136"]["reachable_outputs"] == ["92"]


def test_inspector_reports_reachable_fixed_file_dependencies() -> None:
    graph = unique_graph()
    graph["44"] = {
        "class_type": "LoadAudio",
        "inputs": {"audio": "workflow-owned.wav"},
    }
    graph["140"]["inputs"]["guide_audio"] = ["44", 0]  # type: ignore[index]

    analysis = inspect_h3_workflow(graph)

    assert [dependency.model_dump() for dependency in analysis.fixed_dependencies] == [
        {
            "node_id": "44",
            "class_type": "LoadAudio",
            "input_name": "audio",
            "value": "workflow-owned.wav",
        }
    ]


def test_inspector_does_not_report_mapped_dynamic_picture_as_fixed() -> None:
    graph = unique_graph()
    graph["45"] = {
        "class_type": "LoadImage",
        "inputs": {"image": "replace-me.png"},
    }
    graph["136"]["inputs"]["ref_images.ref_image_0"] = ["45", 0]  # type: ignore[index]

    analysis = inspect_h3_workflow(graph)

    assert analysis.compatibility == "auto_compatible"
    assert analysis.fixed_dependencies == ()


def test_inspector_reports_loader_with_mapped_and_fixed_reachable_consumers() -> None:
    graph = unique_graph()
    graph["45"] = {
        "class_type": "LoadImage",
        "inputs": {"image": "fixed-control.png"},
    }
    graph["136"]["inputs"]["ref_images.ref_image_0"] = ["45", 0]  # type: ignore[index]
    graph["140"]["inputs"]["control"] = ["45", 0]  # type: ignore[index]

    analysis = inspect_h3_workflow(graph)

    assert [dependency.node_id for dependency in analysis.fixed_dependencies] == ["45"]


@pytest.mark.parametrize(
    ("node_id", "input_name", "candidate_field"),
    [
        ("129", "noise_seed", "seed_candidates"),
        ("92", "filename_prefix", "saver_candidates"),
    ],
)
def test_inspector_rejects_candidate_missing_its_exact_boundary_input(
    node_id: str, input_name: str, candidate_field: str
) -> None:
    graph = unique_graph()
    del graph[node_id]["inputs"][input_name]  # type: ignore[index]

    analysis = inspect_h3_workflow(graph)

    assert analysis.compatibility == "unsupported"
    assert getattr(analysis, candidate_field) == ()


@pytest.mark.parametrize(
    ("graph", "message"),
    [
        (
            {str(index): {"class_type": "X", "inputs": {}} for index in range(2001)},
            "2,000",
        ),
        ({"1": {"class_type": "X", "inputs": {"value": "x" * 65_537}}}, "64 KiB"),
    ],
)
def test_inspector_rejects_structural_limits(
    graph: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        inspect_h3_workflow(graph)


def test_inspector_rejects_more_than_ten_thousand_edges() -> None:
    graph: dict[str, object] = {
        "source": {"class_type": "X", "inputs": {}},
        "sink": {
            "class_type": "X",
            "inputs": {f"edge_{index}": ["source", 0] for index in range(10_001)},
        },
    }

    with pytest.raises(ValueError, match="10,000"):
        inspect_h3_workflow(graph)


def test_inspector_rejects_nesting_deeper_than_thirty_two() -> None:
    nested: object = "value"
    for _ in range(33):
        nested = {"nested": nested}
    graph = {"1": {"class_type": "X", "inputs": {"value": nested}}}

    analysis = inspect_h3_workflow(graph)

    assert analysis.compatibility == "unsupported"
    assert {issue.code for issue in analysis.issues} == {"invalid_structure"}
    assert "nesting" in analysis.issues[0].message


def test_inspector_contains_python_recursion_errors_as_unsupported() -> None:
    nested: object = "value"
    for _ in range(2_000):
        nested = {"nested": nested}
    graph = {"1": {"class_type": "X", "inputs": {"value": nested}}}

    analysis = inspect_h3_workflow(graph)

    assert analysis.compatibility == "unsupported"
    assert {issue.code for issue in analysis.issues} == {"invalid_structure"}


def test_inspector_rejects_serialized_workflows_over_eight_mib() -> None:
    graph = {
        str(index): {
            "class_type": "X",
            "inputs": {"blob": f"{index}:" + ("x" * 60_000)},
        }
        for index in range(140)
    }

    with pytest.raises(ValueError, match="8 MiB"):
        inspect_h3_workflow(graph)
