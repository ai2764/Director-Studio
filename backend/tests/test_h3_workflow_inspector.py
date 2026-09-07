"""Output-first inspection coverage for imported H3 API workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.workflow_profiles.h3.inspector import inspect_h3_workflow


FIXTURE = Path(__file__).parent / "fixtures" / "h3_multistage_vhs.api.json"


def multistage_graph() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def object_info() -> dict[str, object]:
    return {
        "MiniMaxH3ReferenceToVideo": {
            "display_name": "MiniMax H3 Ref2AV",
            "output": ["CONDITIONING"],
            "output_node": False,
        },
        "VHS_VideoCombine": {
            "display_name": "Video Combine",
            "output": ["VHS_FILENAMES"],
            "output_node": True,
        },
        "SaveVideo": {
            "display_name": "Save Video",
            "output": ["VIDEO"],
            "output_node": True,
        },
    }


def test_discovers_named_terminal_video_outputs_without_saver_allowlist() -> None:
    analysis = inspect_h3_workflow(multistage_graph(), object_info=object_info())

    assert [candidate.node_id for candidate in analysis.output_candidates] == [
        "214",
        "300",
    ]
    assert analysis.output_candidates[0].display_name == "Final Video Combine"
    assert analysis.output_candidates[0].object_display_name == "Video Combine"
    assert analysis.output_candidates[0].class_type == "VHS_VideoCombine"
    assert analysis.output_candidates[0].terminal is True
    assert analysis.output_candidates[0].output_node is True
    assert analysis.output_candidates[0].output_types == ("VHS_FILENAMES",)
    assert analysis.mapping is None


def test_reverse_traversal_only_offers_h3_and_seed_upstream_of_output() -> None:
    analysis = inspect_h3_workflow(
        multistage_graph(), object_info=object_info(), output_node_id="214"
    )

    assert [candidate.node_id for candidate in analysis.h3_candidates] == ["265"]
    assert analysis.h3_candidates[0].display_name == "H3 Main Generator"
    assert [candidate.node_id for candidate in analysis.seed_candidates] == ["129"]
    assert analysis.mapping is not None
    assert analysis.mapping.inputs.h3_node_id == "265"
    assert analysis.mapping.output.node_id == "214"


def test_selecting_other_output_changes_reverse_discovered_boundary() -> None:
    analysis = inspect_h3_workflow(
        multistage_graph(), object_info=object_info(), output_node_id="300"
    )

    assert [candidate.node_id for candidate in analysis.h3_candidates] == ["400"]
    assert [candidate.node_id for candidate in analysis.seed_candidates] == ["700"]


def test_topology_candidates_remain_available_without_object_info() -> None:
    analysis = inspect_h3_workflow(multistage_graph())

    assert {candidate.node_id for candidate in analysis.output_candidates} == {
        "214",
        "300",
    }
    assert any(issue.code == "object_info_unavailable" for issue in analysis.issues)


def test_non_output_terminal_is_not_offered_when_live_metadata_is_available() -> None:
    graph = multistage_graph()
    graph["999"] = {
        "class_type": "PrimitiveNode",
        "inputs": {},
        "_meta": {"title": "Unused primitive"},
    }

    analysis = inspect_h3_workflow(graph, object_info=object_info())

    assert "999" not in {candidate.node_id for candidate in analysis.output_candidates}


def test_malformed_nodes_report_one_named_issue_per_node() -> None:
    graph = multistage_graph()
    graph["58"] = {"inputs": {}, "_meta": {"title": "Broken Loader"}}
    graph["140"] = []

    analysis = inspect_h3_workflow(graph, object_info=object_info())

    failures = [
        issue
        for issue in analysis.issues
        if issue.code in {"invalid_node", "invalid_class_type", "invalid_inputs"}
    ]
    assert sorted((issue.node_id, issue.node_name) for issue in failures) == [
        ("140", "Node 140"),
        ("58", "Broken Loader"),
    ]


@pytest.mark.parametrize(
    "title",
    [
        r"C:\Users\private\workflow.json",
        "/home/private/workflow.json",
        r"\\server\private\workflow.json",
    ],
)
def test_candidate_titles_redact_absolute_paths(title: str) -> None:
    graph = multistage_graph()
    graph["214"]["_meta"] = {"title": title}  # type: ignore[index]

    analysis = inspect_h3_workflow(graph, object_info=object_info())

    candidate = next(item for item in analysis.output_candidates if item.node_id == "214")
    assert candidate.display_name == "Video Combine"


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


def test_inspector_contains_excessive_nesting_as_unsupported() -> None:
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
