"""Boundary-only validation for opaque custom H3 workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .inspector import _ancestors, _graph_edges, _is_link, _load_graph, _structural_issues
from .models import (
    H3BoundaryMapping,
    H3ValidationIssue,
    ResolvedH3Profile,
    ValidationReport,
)

_H3_CLASS = "MiniMaxH3ReferenceToVideo"
_PICTURE_PATTERN = "ref_images.ref_image_{index}"
_AUDIO_PATTERN = "ref_audios.ref_audio_{index}"
_CANONICAL_INPUTS = {
    "prompt_input": "prompt",
    "width_input": "width",
    "height_input": "height",
    "frames_input": "length",
    "picture_input_pattern": _PICTURE_PATTERN,
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


def validate_h3_contract(
    graph: object,
    mapping: H3BoundaryMapping,
    object_info: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Validate only Director Studio's selected input/output graph boundary."""
    del object_info
    synthetic_boundary = {
        "pictures": 1,
        "audios": 0,
        "width": 864,
        "height": 480,
        "frames": 56,
        "seed": 42,
    }
    try:
        normalized = _load_graph(graph)
    except (TypeError, ValueError, RecursionError) as exc:
        message = (
            "workflow nesting exceeds depth 32"
            if isinstance(exc, RecursionError)
            else str(exc)
        )
        return ValidationReport(
            valid=False,
            issues=(_issue("invalid_structure", message),),
            synthetic_boundary=synthetic_boundary,
        )

    structural = _structural_issues(normalized)
    if structural:
        return ValidationReport(
            valid=False,
            issues=tuple(
                _issue(
                    item.code,
                    item.message,
                    node_id=item.node_id,
                    input_name=item.input_name,
                )
                for item in structural
            ),
            synthetic_boundary=synthetic_boundary,
        )

    outgoing, incoming, _links = _graph_edges(normalized)
    issues: list[H3ValidationIssue] = []
    output_id = mapping.output.node_id
    selected_output = normalized.get(output_id)
    if not isinstance(selected_output, Mapping):
        issues.append(
            _issue(
                "invalid_output_selection",
                f"selected output node {output_id} does not exist",
                node_id=output_id,
            )
        )
        upstream: set[str] = set()
    else:
        upstream = _ancestors(output_id, incoming)
        if outgoing.get(output_id):
            issues.append(
                _issue(
                    "nonterminal_output",
                    f"selected output node {output_id} has downstream consumers",
                    node_id=output_id,
                )
            )

    inputs_mapping = mapping.inputs
    h3_id = inputs_mapping.h3_node_id
    h3_node = normalized.get(h3_id)
    if h3_id not in upstream:
        issues.append(
            _issue(
                "unreachable_mapping",
                f"mapped H3 node {h3_id} is not upstream of output {output_id}",
                node_id=h3_id,
            )
        )
    elif not isinstance(h3_node, Mapping) or h3_node.get("class_type") != _H3_CLASS:
        issues.append(
            _issue(
                "invalid_mapped_node",
                f"mapped H3 node {h3_id} is not {_H3_CLASS}",
                node_id=h3_id,
            )
        )

    h3_inputs = h3_node.get("inputs") if isinstance(h3_node, Mapping) else None
    for field, expected in _CANONICAL_INPUTS.items():
        actual = getattr(inputs_mapping, field)
        if actual != expected:
            issues.append(
                _issue(
                    "noncanonical_mapping",
                    f"H3 boundary requires {field}={expected}; got {actual}",
                    node_id=h3_id,
                    input_name=str(actual),
                )
            )
        elif field != "picture_input_pattern" and (
            not isinstance(h3_inputs, Mapping) or actual not in h3_inputs
        ):
            issues.append(
                _issue(
                    "missing_mapped_input",
                    f"mapped input {actual} does not exist on H3 node {h3_id}",
                    node_id=h3_id,
                    input_name=actual,
                )
            )
    if inputs_mapping.audio_input_pattern not in (None, _AUDIO_PATTERN):
        issues.append(
            _issue(
                "noncanonical_mapping",
                f"audio input pattern must be {_AUDIO_PATTERN} or absent",
                node_id=h3_id,
                input_name=inputs_mapping.audio_input_pattern,
            )
        )
    if isinstance(h3_inputs, Mapping):
        for forbidden in ("ref_frame", "last_frame"):
            if forbidden in h3_inputs:
                issues.append(
                    _issue(
                        "unsupported_boundary_input",
                        f"selected H3 boundary uses unsupported input {forbidden}",
                        node_id=h3_id,
                        input_name=forbidden,
                    )
                )

    if inputs_mapping.seed_node_id is not None:
        seed_id = inputs_mapping.seed_node_id
        seed_input = inputs_mapping.seed_input
        if seed_id not in upstream:
            issues.append(
                _issue(
                    "unreachable_mapping",
                    f"mapped seed node {seed_id} is not upstream of output {output_id}",
                    node_id=seed_id,
                )
            )
        else:
            seed_node = normalized.get(seed_id)
            seed_inputs = seed_node.get("inputs") if isinstance(seed_node, Mapping) else None
            if not isinstance(seed_inputs, Mapping) or seed_input not in seed_inputs:
                issues.append(
                    _issue(
                        "missing_mapped_input",
                        f"mapped seed input {seed_input} does not exist on node {seed_id}",
                        node_id=seed_id,
                        input_name=seed_input,
                    )
                )

    for target_id, node in normalized.items():
        node_inputs = node.get("inputs") if isinstance(node, Mapping) else None
        if not isinstance(node_inputs, Mapping):
            continue
        for input_name, value in node_inputs.items():
            if _is_link(value) and str(value[0]) not in normalized:
                issues.append(
                    _issue(
                        "dangling_link",
                        f"input {input_name} links to missing node {value[0]}",
                        node_id=target_id,
                        input_name=str(input_name),
                    )
                )

    if not issues:
        try:
            from app.pipelines.h3_ref2va.workflow import fill_profile_graph

            fill_profile_graph(
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
                },
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            issues.append(_issue("synthetic_fill_failed", str(exc)))

    return ValidationReport(
        valid=not issues,
        issues=tuple(issues),
        synthetic_boundary=synthetic_boundary,
    )


__all__ = ["validate_h3_contract"]
