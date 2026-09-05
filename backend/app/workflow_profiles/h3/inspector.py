"""Deterministic, bounded inspection of ComfyUI H3 API workflows."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Mapping
from typing import Any

from .models import (
    H3AnalysisIssue,
    H3BoundaryMapping,
    H3FixedDependency,
    H3NodeCandidate,
    H3WorkflowAnalysis,
)

MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
MAX_NODES = 2_000
MAX_EDGES = 10_000
MAX_NESTING = 32
MAX_STRING_BYTES = 64 * 1024

_H3_CLASS = "MiniMaxH3ReferenceToVideo"
_I2V_CLASS = "MiniMaxH3ImageToVideo"
_SECRET_PARTS = ("key", "token", "secret", "password", "authorization", "path")
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE = re.compile(r"\s+")


def _load_graph(graph: object) -> dict[str, Any]:
    if isinstance(graph, bytes):
        raw = graph
        if len(raw) > MAX_WORKFLOW_BYTES:
            raise ValueError("workflow exceeds the 8 MiB limit")
        try:
            graph = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("workflow must be valid UTF-8 JSON") from exc
    elif isinstance(graph, str):
        raw = graph.encode("utf-8")
        if len(raw) > MAX_WORKFLOW_BYTES:
            raise ValueError("workflow exceeds the 8 MiB limit")
        try:
            graph = json.loads(graph)
        except json.JSONDecodeError as exc:
            raise ValueError("workflow must be valid JSON") from exc
    if not isinstance(graph, Mapping):
        raise TypeError("workflow must be a JSON object")
    normalized = {str(node_id): node for node_id, node in graph.items()}
    if len(normalized) != len(graph):
        raise ValueError("workflow contains duplicate node IDs after string normalization")
    encoded_size = len(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if encoded_size > MAX_WORKFLOW_BYTES:
        raise ValueError("workflow exceeds the 8 MiB limit")
    if len(normalized) > MAX_NODES:
        raise ValueError("workflow exceeds the 2,000 node limit")
    _check_value_limits(normalized, depth=0)
    return normalized


def _check_value_limits(value: object, *, depth: int) -> None:
    if depth > MAX_NESTING:
        raise ValueError("workflow nesting exceeds depth 32")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise ValueError("workflow string exceeds the 64 KiB limit")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            _check_value_limits(key, depth=depth + 1)
            _check_value_limits(child, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _check_value_limits(child, depth=depth + 1)


def _is_link(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and not isinstance(value[0], bool)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
        and value[1] >= 0
    )


def _graph_edges(
    graph: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[tuple[str, str, str]]]:
    outgoing = {node_id: set() for node_id in graph}
    incoming = {node_id: set() for node_id in graph}
    links: list[tuple[str, str, str]] = []
    edge_count = 0
    for target_id, node in graph.items():
        if not isinstance(node, Mapping):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        for input_name, value in inputs.items():
            if not _is_link(value):
                continue
            edge_count += 1
            if edge_count > MAX_EDGES:
                raise ValueError("workflow exceeds the 10,000 graph edge limit")
            source_id = str(value[0])
            links.append((source_id, target_id, str(input_name)))
            if source_id in graph:
                outgoing[source_id].add(target_id)
                incoming[target_id].add(source_id)
    return outgoing, incoming, links


def _descendants(start: str, outgoing: Mapping[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    pending = deque([start])
    while pending:
        node_id = pending.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(sorted(outgoing.get(node_id, ())))
    return visited


def _title(node: object) -> str:
    if not isinstance(node, Mapping):
        return ""
    meta = node.get("_meta")
    value = meta.get("title") if isinstance(meta, Mapping) else ""
    if not isinstance(value, str):
        return ""
    value = _CONTROL_CHARACTERS.sub(" ", value)
    return _WHITESPACE.sub(" ", value).strip()[:256]


def _candidate(node_id: str, graph: Mapping[str, Any]) -> H3NodeCandidate:
    node = graph[node_id]
    return H3NodeCandidate(
        node_id=node_id,
        class_type=str(node.get("class_type")),
        title=_title(node),
    )


def _redacted_default(class_type: str, key: str, value: object) -> object:
    lowered = key.casefold()
    if any(part in lowered for part in _SECRET_PARTS):
        return "<redacted>"
    if class_type == "LoadImage" and lowered == "image":
        return "<redacted-file>"
    if class_type == "LoadAudio" and lowered == "audio":
        return "<redacted-file>"
    if isinstance(value, str) and _ABSOLUTE_PATH.match(value):
        return "<redacted-path>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return "<complex>"


def _fixed_dependencies(
    graph: Mapping[str, Any],
    output_reachability: Mapping[str, list[str]],
    h3_id: str | None,
) -> tuple[H3FixedDependency, ...]:
    dependencies: list[H3FixedDependency] = []
    input_by_class = {"LoadImage": "image", "LoadAudio": "audio"}
    mapped_file_nodes: set[str] = set()
    h3_node = graph.get(h3_id) if h3_id else None
    h3_inputs = h3_node.get("inputs") if isinstance(h3_node, Mapping) else None
    if isinstance(h3_inputs, Mapping):
        dynamic_input = re.compile(
            r"(?:ref_images\.ref_image_|ref_audios\.ref_audio_)\d+\Z"
        )
        mapped_file_nodes = {
            str(value[0])
            for name, value in h3_inputs.items()
            if dynamic_input.fullmatch(str(name)) and _is_link(value)
        }
    for node_id in sorted(graph):
        node = graph[node_id]
        if (
            not isinstance(node, Mapping)
            or not output_reachability.get(node_id)
            or node_id in mapped_file_nodes
        ):
            continue
        class_type = node.get("class_type")
        input_name = input_by_class.get(class_type)
        inputs = node.get("inputs")
        if input_name is None or not isinstance(inputs, Mapping):
            continue
        value = inputs.get(input_name)
        if isinstance(value, str) and value:
            dependencies.append(
                H3FixedDependency(
                    node_id=node_id,
                    class_type=class_type,
                    input_name=input_name,
                    value=value,
                )
            )
    return tuple(dependencies)


def inspect_h3_workflow(graph: object) -> H3WorkflowAnalysis:
    """Inspect one API-format graph without executing or exposing its raw values."""
    normalized = _load_graph(graph)
    outgoing, _incoming, _links = _graph_edges(normalized)
    issues: list[H3AnalysisIssue] = []

    h3_ids = sorted(
        node_id
        for node_id, node in normalized.items()
        if isinstance(node, Mapping) and node.get("class_type") == _H3_CLASS
    )
    if len(h3_ids) != 1:
        issues.append(
            H3AnalysisIssue(
                code="h3_node_count",
                message=f"pure Ref2AV requires exactly one {_H3_CLASS} node; found {len(h3_ids)}",
            )
        )

    for node_id, node in normalized.items():
        if not isinstance(node, Mapping):
            issues.append(
                H3AnalysisIssue(
                    code="invalid_node",
                    message="workflow nodes must be JSON objects",
                    node_id=node_id,
                )
            )
            continue
        if node.get("class_type") == _I2V_CLASS:
            issues.append(
                H3AnalysisIssue(
                    code="forbidden_semantics",
                    message="pure Ref2AV workflows cannot contain MiniMaxH3ImageToVideo",
                    node_id=node_id,
                )
            )
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            issues.append(
                H3AnalysisIssue(
                    code="invalid_inputs",
                    message="workflow node inputs must be a JSON object",
                    node_id=node_id,
                )
            )
            continue
        for forbidden in ("ref_frame", "last_frame"):
            if forbidden in inputs:
                issues.append(
                    H3AnalysisIssue(
                        code="forbidden_semantics",
                        message=f"pure Ref2AV workflows cannot contain the {forbidden} input",
                        node_id=node_id,
                        input_name=forbidden,
                    )
                )

    h3_id = h3_ids[0] if len(h3_ids) == 1 else None
    reachable_from_h3 = _descendants(h3_id, outgoing) if h3_id else set()
    saver_ids = sorted(
        node_id
        for node_id in reachable_from_h3
        if isinstance(normalized[node_id], Mapping)
        and normalized[node_id].get("class_type") == "SaveVideo"
        and isinstance(normalized[node_id].get("inputs"), Mapping)
        and "filename_prefix" in normalized[node_id]["inputs"]
    )
    seed_ids = sorted(
        node_id
        for node_id, node in normalized.items()
        if isinstance(node, Mapping)
        and node.get("class_type") == "RandomNoise"
        and isinstance(node.get("inputs"), Mapping)
        and "noise_seed" in node["inputs"]
        and set(saver_ids).intersection(_descendants(node_id, outgoing))
    )

    if h3_id:
        h3_inputs = normalized[h3_id].get("inputs")
        if isinstance(h3_inputs, Mapping):
            for input_name in ("prompt", "width", "height", "length"):
                if input_name not in h3_inputs:
                    issues.append(
                        H3AnalysisIssue(
                            code="missing_h3_input",
                            message=f"H3 node is missing required input {input_name}",
                            node_id=h3_id,
                            input_name=input_name,
                        )
                    )
    if h3_id and not seed_ids:
        issues.append(
            H3AnalysisIssue(
                code="missing_seed_candidate",
                message="no RandomNoise node reaches an H3 final output",
            )
        )
    if h3_id and not saver_ids:
        issues.append(
            H3AnalysisIssue(
                code="missing_saver_candidate",
                message="no SaveVideo node is reachable from the H3 node",
            )
        )

    output_reachability = {
        node_id: sorted(set(saver_ids).intersection(_descendants(node_id, outgoing)))
        for node_id in normalized
    }
    seed_candidates = tuple(_candidate(node_id, normalized) for node_id in seed_ids)
    saver_candidates = tuple(_candidate(node_id, normalized) for node_id in saver_ids)
    fixed_dependencies = _fixed_dependencies(normalized, output_reachability, h3_id)

    candidate_roles = {
        node_id: [
            role
            for role, candidates in (
                ("h3", [h3_id] if h3_id else []),
                ("seed", seed_ids),
                ("saver", saver_ids),
            )
            if node_id in candidates
        ]
        for node_id in normalized
    }
    manifest_nodes: list[dict[str, Any]] = []
    for node_id in sorted(normalized):
        node = normalized[node_id]
        if not isinstance(node, Mapping):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs")
        input_names = sorted(str(name) for name in inputs) if isinstance(inputs, Mapping) else []
        defaults = {
            str(name): _redacted_default(class_type, str(name), value)
            for name, value in sorted(
                inputs.items(), key=lambda item: str(item[0])
            )
            if not _is_link(value)
        } if isinstance(inputs, Mapping) else {}
        manifest_nodes.append(
            {
                "node_id": node_id,
                "class_type": class_type,
                "title": _title(node),
                "input_names": input_names,
                "reachable_outputs": output_reachability[node_id],
                "candidate_roles": candidate_roles[node_id],
                "defaults": defaults,
            }
        )

    mapping = None
    if not issues and len(seed_ids) == 1 and len(saver_ids) == 1 and h3_id:
        mapping = H3BoundaryMapping(
            h3_node_id=h3_id,
            prompt_input="prompt",
            width_input="width",
            height_input="height",
            frames_input="length",
            picture_input_pattern="ref_images.ref_image_{index}",
            audio_input_pattern="ref_audios.ref_audio_{index}",
            seed_node_id=seed_ids[0],
            seed_input="noise_seed",
            saver_node_id=saver_ids[0],
            output_prefix_input="filename_prefix",
        )
    if issues:
        compatibility = "unsupported"
    elif len(seed_ids) > 1 or len(saver_ids) > 1:
        compatibility = "needs_confirmation"
    else:
        compatibility = "auto_compatible"

    return H3WorkflowAnalysis(
        compatibility=compatibility,
        mapping=mapping,
        seed_candidates=seed_candidates,
        saver_candidates=saver_candidates,
        fixed_dependencies=fixed_dependencies,
        issues=tuple(issues),
        agent_manifest={"nodes": manifest_nodes},
    )


__all__ = [
    "MAX_EDGES",
    "MAX_NESTING",
    "MAX_NODES",
    "MAX_STRING_BYTES",
    "MAX_WORKFLOW_BYTES",
    "inspect_h3_workflow",
]
