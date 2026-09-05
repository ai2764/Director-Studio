"""Exact application-boundary validation for inspected H3 workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .inspector import (
    _graph_edges,
    _is_link,
    _unmapped_reachable_file_nodes,
    inspect_h3_workflow,
)
from .models import (
    H3BoundaryMapping,
    H3ValidationIssue,
    ResolvedH3Profile,
    ValidationReport,
)

_H3_CLASS = "MiniMaxH3ReferenceToVideo"
_DYNAMIC_PICTURE_PATTERN = "ref_images.ref_image_{index}"
_DYNAMIC_AUDIO_PATTERN = "ref_audios.ref_audio_{index}"
_CANONICAL_FIELDS = {
    "prompt_input": "prompt",
    "width_input": "width",
    "height_input": "height",
    "frames_input": "length",
    "picture_input_pattern": _DYNAMIC_PICTURE_PATTERN,
    "seed_input": "noise_seed",
    "output_prefix_input": "filename_prefix",
}


def _issue(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    input_name: str | None = None,
) -> H3ValidationIssue:
    return H3ValidationIssue(
        code=code,
        message=message,
        node_id=node_id,
        input_name=input_name,
    )


def _node(graph: Mapping[str, Any], node_id: str) -> Mapping[str, Any] | None:
    value = graph.get(node_id)
    return value if isinstance(value, Mapping) else None


def _validate_mapped_input(
    issues: list[H3ValidationIssue],
    graph: Mapping[str, Any],
    node_id: str,
    input_name: str,
) -> None:
    node = _node(graph, node_id)
    inputs = node.get("inputs") if node else None
    if not isinstance(inputs, Mapping) or input_name not in inputs:
        issues.append(
            _issue(
                "missing_mapped_input",
                f"mapped input {input_name} does not exist on node {node_id}",
                node_id=node_id,
                input_name=input_name,
            )
        )


def validate_h3_contract(graph: object, mapping: H3BoundaryMapping) -> ValidationReport:
    """Validate exact mapping names and the graph produced by a synthetic fill."""
    synthetic_boundary = {
        "pictures": 1,
        "audios": 0,
        "width": 864,
        "height": 480,
        "frames": 56,
        "seed": 42,
    }
    try:
        analysis = inspect_h3_workflow(graph)
    except (TypeError, ValueError) as exc:
        return ValidationReport(
            valid=False,
            issues=(_issue("inspection_error", str(exc)),),
            synthetic_boundary=synthetic_boundary,
        )

    normalized = (
        {str(node_id): node for node_id, node in graph.items()}
        if isinstance(graph, Mapping)
        else {}
    )
    issues: list[H3ValidationIssue] = []
    for analysis_issue in analysis.issues:
        issues.append(
            _issue(
                analysis_issue.code,
                analysis_issue.message,
                node_id=analysis_issue.node_id,
                input_name=analysis_issue.input_name,
            )
        )
    if any(issue.code == "invalid_structure" for issue in issues):
        return ValidationReport(
            valid=False,
            issues=tuple(issues),
            fixed_dependencies=analysis.fixed_dependencies,
            synthetic_boundary=synthetic_boundary,
        )

    canonical_fields: dict[str, bool] = {}
    for field, expected in _CANONICAL_FIELDS.items():
        actual = getattr(mapping, field)
        canonical_fields[field] = actual == expected
        if actual != expected:
            issues.append(
                _issue(
                    "noncanonical_mapping",
                    f"contract v1 requires {field}={expected}; got {actual}",
                    input_name=str(actual),
                )
            )
    canonical_audio = mapping.audio_input_pattern in (None, _DYNAMIC_AUDIO_PATTERN)
    if not canonical_audio:
        issues.append(
            _issue(
                "noncanonical_mapping",
                f"contract v1 requires audio_input_pattern={_DYNAMIC_AUDIO_PATTERN} or absent; "
                f"got {mapping.audio_input_pattern}",
                input_name=mapping.audio_input_pattern,
            )
        )

    h3_node = _node(normalized, mapping.h3_node_id)
    if h3_node is None or h3_node.get("class_type") != _H3_CLASS:
        issues.append(
            _issue(
                "invalid_mapped_node",
                f"mapped H3 node {mapping.h3_node_id} is not {_H3_CLASS}",
                node_id=mapping.h3_node_id,
            )
        )
    else:
        for field in ("prompt_input", "width_input", "height_input", "frames_input"):
            if canonical_fields[field]:
                _validate_mapped_input(
                    issues, normalized, mapping.h3_node_id, getattr(mapping, field)
                )

    seed_ids = {candidate.node_id for candidate in analysis.seed_candidates}
    saver_ids = {candidate.node_id for candidate in analysis.saver_candidates}
    reachable_outputs = {
        str(node["node_id"]): node["reachable_outputs"]
        for node in analysis.agent_manifest["nodes"]
    }
    if mapping.seed_node_id not in seed_ids:
        issues.append(
            _issue(
                "unreachable_mapping",
                f"mapped seed node {mapping.seed_node_id} is not on an H3 final-output path",
                node_id=mapping.seed_node_id,
            )
        )
    elif canonical_fields["seed_input"]:
        _validate_mapped_input(
            issues, normalized, mapping.seed_node_id, mapping.seed_input
        )
    if mapping.saver_node_id not in saver_ids:
        issues.append(
            _issue(
                "unreachable_mapping",
                f"mapped saver node {mapping.saver_node_id} is not reachable from H3",
                node_id=mapping.saver_node_id,
            )
        )
    elif canonical_fields["output_prefix_input"]:
        _validate_mapped_input(
            issues,
            normalized,
            mapping.saver_node_id,
            mapping.output_prefix_input,
        )
    if (
        mapping.seed_node_id in seed_ids
        and mapping.saver_node_id in saver_ids
        and mapping.saver_node_id
        not in reachable_outputs.get(mapping.seed_node_id, [])
    ):
        issues.append(
            _issue(
                "unreachable_mapping",
                f"mapped seed node {mapping.seed_node_id} does not reach selected saver "
                f"{mapping.saver_node_id}",
                node_id=mapping.seed_node_id,
            )
        )

    fixed_ids = {dependency.node_id for dependency in analysis.fixed_dependencies}
    _outgoing, _incoming, links = _graph_edges(normalized)
    h3_ids = [
        str(node["node_id"])
        for node in analysis.agent_manifest["nodes"]
        if "h3" in node["candidate_roles"]
    ]
    dependency_nodes = _unmapped_reachable_file_nodes(
        normalized,
        reachable_outputs,
        h3_ids[0] if len(h3_ids) == 1 else None,
        links,
    )
    for node_id in sorted(dependency_nodes - fixed_ids):
        node = normalized[node_id]
        if isinstance(node, Mapping):
            input_name = "image" if node.get("class_type") == "LoadImage" else "audio"
            issues.append(
                _issue(
                    "unmapped_file_input",
                    f"reachable {node.get('class_type')} node lacks explicit fixed input {input_name}",
                    node_id=node_id,
                    input_name=input_name,
                )
            )

    if not issues:
        try:
            from app.pipelines.h3_ref2va.workflow import fill_profile_graph

            filled = fill_profile_graph(
                ResolvedH3Profile(
                    profile_id="contract-validation",
                    workflow=normalized,
                    mapping=mapping,
                    workflow_sha256="",
                    source="custom",
                ),
                {
                    "prompt": "Neutral H3 workflow contract test",
                    "images": ["contract-picture.png"],
                    "audios": [],
                    "frames": 56,
                    "width": 864,
                    "height": 480,
                    "seed": 42,
                    "output_prefix": "director-studio/h3/contract-test",
                },
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            issues.append(_issue("synthetic_fill_failed", str(exc)))
        else:
            for target_id, node in filled.items():
                if not isinstance(node, Mapping):
                    continue
                inputs = node.get("inputs")
                if not isinstance(inputs, Mapping):
                    continue
                for input_name, value in inputs.items():
                    if _is_link(value) and str(value[0]) not in filled:
                        issues.append(
                            _issue(
                                "dangling_link",
                                f"input {input_name} links to missing node {value[0]}",
                                node_id=str(target_id),
                                input_name=str(input_name),
                            )
                        )
            required_filled_inputs = (
                (mapping.h3_node_id, mapping.prompt_input),
                (mapping.h3_node_id, mapping.width_input),
                (mapping.h3_node_id, mapping.height_input),
                (mapping.h3_node_id, mapping.frames_input),
                (mapping.h3_node_id, mapping.picture_input_pattern.format(index=0)),
                (mapping.seed_node_id, mapping.seed_input),
                (mapping.saver_node_id, mapping.output_prefix_input),
            )
            for node_id, input_name in required_filled_inputs:
                _validate_mapped_input(issues, filled, node_id, input_name)

    return ValidationReport(
        valid=not issues,
        issues=tuple(issues),
        fixed_dependencies=analysis.fixed_dependencies,
        synthetic_boundary=synthetic_boundary,
    )
__all__ = ["validate_h3_contract"]
