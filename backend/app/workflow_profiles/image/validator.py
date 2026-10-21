"""Deterministic validation of an image boundary mapping against a graph.

Validation reports incompatibilities; it never rewrites the user's
graph or the mapping (the H3 validator's rule). An empty issue list
means the mapping may advance a profile to ``validated``.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import contract_for
from .inspector import _is_link
from .models import ImageAnalysisIssue, ImageBoundaryMapping


def _node(graph: Mapping[str, Any], node_id: str) -> Mapping[str, Any] | None:
    node = graph.get(node_id)
    return node if isinstance(node, Mapping) else None


def _check_target(
    graph: Mapping[str, Any], node_id: str, input_name: str, issues: list[ImageAnalysisIssue],
) -> Mapping[str, Any] | None:
    node = _node(graph, node_id)
    if node is None:
        issues.append(ImageAnalysisIssue(
            code="node_missing", message=f"Mapped node {node_id} is not in the graph.",
            node_id=node_id, input_name=input_name))
        return None
    inputs = node.get("inputs") or {}
    if input_name not in inputs:
        issues.append(ImageAnalysisIssue(
            code="input_missing",
            message=f"Node {node_id} has no input named {input_name!r}.",
            node_id=node_id, input_name=input_name))
        return None
    if _is_link(inputs[input_name]):
        issues.append(ImageAnalysisIssue(
            code="input_is_link",
            message=f"Input {input_name!r} on node {node_id} is fed by a link; "
                    "the boundary can only fill literal inputs.",
            node_id=node_id, input_name=input_name))
        return None
    return node


def validate_mapping(
    graph: Mapping[str, Any], kind: str, mapping: ImageBoundaryMapping,
) -> list[ImageAnalysisIssue]:
    contract = contract_for(kind)
    issues: list[ImageAnalysisIssue] = []

    # Text roles: every contract role that is required must be bound;
    # every binding must target a real literal input, and one node input
    # cannot serve two roles.
    bound_text_roles = {b.role for b in mapping.texts}
    for spec in contract.texts:
        if spec.required and spec.role not in bound_text_roles:
            issues.append(ImageAnalysisIssue(
                code="missing_required_text",
                message=f"Kind {kind!r} requires a {spec.role!r} text binding."))
    seen_targets: dict[tuple[str, str], str] = {}
    for binding in mapping.texts:
        if binding.role not in {spec.role for spec in contract.texts}:
            issues.append(ImageAnalysisIssue(
                code="unknown_text_role",
                message=f"Text role {binding.role!r} is not part of the {kind!r} contract.",
                node_id=binding.node_id, input_name=binding.input_name))
        target = (binding.node_id, binding.input_name)
        if target in seen_targets and seen_targets[target] != binding.role:
            issues.append(ImageAnalysisIssue(
                code="conflicting_binding",
                message=f"Node {binding.node_id} input {binding.input_name!r} is bound "
                        f"to both {seen_targets[target]!r} and {binding.role!r}.",
                node_id=binding.node_id, input_name=binding.input_name))
        seen_targets[target] = binding.role
        _check_target(graph, binding.node_id, binding.input_name, issues)

    # Reference images: count must fit the contract and every binding
    # must land on a LoadImage node.
    count = len(mapping.images)
    if not contract.images.min_count <= count <= contract.images.max_count:
        issues.append(ImageAnalysisIssue(
            code="image_count_out_of_range",
            message=f"Kind {kind!r} accepts {contract.images.min_count}–"
                    f"{contract.images.max_count} reference image(s); mapping binds {count}."))
    for binding in mapping.images:
        _check_target(graph, binding.node_id, binding.input_name, issues)
        node = _node(graph, binding.node_id)
        if node is not None and node.get("class_type") != "LoadImage":
            issues.append(ImageAnalysisIssue(
                code="wrong_node_class",
                message=f"Reference image binding targets node {binding.node_id} "
                        f"({node.get('class_type')}), not a LoadImage.",
                node_id=binding.node_id, input_name=binding.input_name))

    # Outputs: every required slot bound, every bound role contractual,
    # every target a SaveImage node.
    bound_output_roles = {b.role for b in mapping.outputs}
    for slot in contract.required_outputs():
        if slot.role not in bound_output_roles:
            issues.append(ImageAnalysisIssue(
                code="missing_required_output",
                message=f"Kind {kind!r} requires an output binding for slot {slot.role!r}."))
    for binding in mapping.outputs:
        if contract.output_slot(binding.role) is None:
            issues.append(ImageAnalysisIssue(
                code="unknown_output_role",
                message=f"Output role {binding.role!r} is not part of the {kind!r} contract.",
                node_id=binding.node_id))
        node = _node(graph, binding.node_id)
        if node is None:
            issues.append(ImageAnalysisIssue(
                code="node_missing", message=f"Mapped node {binding.node_id} is not in the graph.",
                node_id=binding.node_id))
        elif not str(node.get("class_type") or "").startswith("SaveImage"):
            issues.append(ImageAnalysisIssue(
                code="wrong_node_class",
                message=f"Output binding targets node {binding.node_id} "
                        f"({node.get('class_type')}), not a SaveImage.",
                node_id=binding.node_id))

    # Seeds and size: targets must exist as literal inputs.
    for binding in mapping.seeds:
        _check_target(graph, binding.node_id, binding.input_name, issues)
    if mapping.size is not None:
        _check_target(graph, mapping.size.node_id, mapping.size.width_input, issues)
        _check_target(graph, mapping.size.node_id, mapping.size.height_input, issues)

    return issues
