"""Deterministic output-first inspection of ComfyUI H3 API workflows."""

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
    H3InputMapping,
    H3NodeCandidate,
    H3OutputSelection,
    H3WorkflowAnalysis,
)

MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
MAX_NODES = 2_000
MAX_EDGES = 10_000
MAX_NESTING = 32
MAX_STRING_BYTES = 64 * 1024

_H3_CLASS = "MiniMaxH3ReferenceToVideo"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE = re.compile(r"\s+")
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")
_VIDEO_TYPE_PARTS = ("video", "vhs", "movie", "mp4", "webm", "filename")


def _load_graph(graph: object) -> dict[str, Any]:
    if isinstance(graph, bytes):
        if len(graph) > MAX_WORKFLOW_BYTES:
            raise ValueError("workflow exceeds the 8 MiB limit")
        try:
            graph = json.loads(graph.decode("utf-8-sig"))
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
        raise ValueError(
            "workflow contains duplicate node IDs after string normalization"
        )
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_WORKFLOW_BYTES:
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
    elif isinstance(value, (list, tuple)):
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
    for target_id, node in graph.items():
        if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping):
            continue
        for input_name, value in node["inputs"].items():
            if not _is_link(value):
                continue
            if len(links) >= MAX_EDGES:
                raise ValueError("workflow exceeds the 10,000 graph edge limit")
            source_id = str(value[0])
            links.append((source_id, target_id, str(input_name)))
            if source_id in graph:
                outgoing[source_id].add(target_id)
                incoming[target_id].add(source_id)
    return outgoing, incoming, links


def _ancestors(start: str, incoming: Mapping[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    pending = deque([start])
    while pending:
        node_id = pending.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(sorted(incoming.get(node_id, ())))
    return visited


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
    if not isinstance(node, Mapping) or not isinstance(node.get("_meta"), Mapping):
        return ""
    value = node["_meta"].get("title")
    if not isinstance(value, str):
        return ""
    value = _WHITESPACE.sub(" ", _CONTROL_CHARACTERS.sub(" ", value)).strip()[:256]
    return "" if _ABSOLUTE_PATH.match(value) else value


def _metadata(
    class_type: str, object_info: Mapping[str, Any] | None
) -> Mapping[str, Any]:
    value = object_info.get(class_type) if object_info else None
    return value if isinstance(value, Mapping) else {}


def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = _WHITESPACE.sub(" ", _CONTROL_CHARACTERS.sub(" ", value)).strip()[:256]
    return "" if _ABSOLUTE_PATH.match(value) else value


def _candidate(
    node_id: str,
    graph: Mapping[str, Any],
    *,
    object_info: Mapping[str, Any] | None,
    terminal: bool = False,
) -> H3NodeCandidate:
    node = graph[node_id]
    class_type = str(node.get("class_type") or "")
    metadata = _metadata(class_type, object_info)
    title = _title(node)
    object_name = _clean_name(metadata.get("display_name"))
    output_types = metadata.get("output")
    return H3NodeCandidate(
        node_id=node_id,
        class_type=class_type,
        title=title,
        object_display_name=object_name,
        display_name=title or object_name or class_type or f"Node {node_id}",
        terminal=terminal,
        output_node=bool(metadata.get("output_node")),
        output_types=tuple(
            str(item) for item in output_types if isinstance(item, (str, int, float))
        )
        if isinstance(output_types, (list, tuple))
        else (),
    )


def _numeric_node_key(candidate: H3NodeCandidate) -> tuple[int, int | str]:
    try:
        return (0, int(candidate.node_id))
    except ValueError:
        return (1, candidate.node_id)


def _looks_video_capable(candidate: H3NodeCandidate) -> bool:
    return any(
        part in output_type.casefold()
        for output_type in candidate.output_types
        for part in _VIDEO_TYPE_PARTS
    )


def _structural_issues(graph: Mapping[str, Any]) -> list[H3AnalysisIssue]:
    issues: list[H3AnalysisIssue] = []
    for node_id, node in graph.items():
        if not isinstance(node, Mapping):
            issues.append(
                H3AnalysisIssue(
                    code="invalid_node",
                    message=f"Node {node_id} must be a JSON object",
                    node_id=node_id,
                    node_name=f"Node {node_id}",
                )
            )
            continue
        name = _title(node) or f"Node {node_id}"
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not class_type.strip():
            issues.append(
                H3AnalysisIssue(
                    code="invalid_class_type",
                    message=f"{name} (Node {node_id}) requires a class_type string",
                    node_id=node_id,
                    node_name=name,
                )
            )
        elif not isinstance(inputs, Mapping):
            issues.append(
                H3AnalysisIssue(
                    code="invalid_inputs",
                    message=f"{name} (Node {node_id}) inputs must be a JSON object",
                    node_id=node_id,
                    node_name=name,
                )
            )
    return issues


def _fixed_dependencies(
    graph: Mapping[str, Any], upstream: set[str]
) -> tuple[H3FixedDependency, ...]:
    result: list[H3FixedDependency] = []
    fields = {"LoadImage": "image", "LoadAudio": "audio"}
    for node_id in sorted(upstream):
        node = graph.get(node_id)
        if not isinstance(node, Mapping) or node.get("class_type") not in fields:
            continue
        input_name = fields[str(node["class_type"])]
        inputs = node.get("inputs")
        value = inputs.get(input_name) if isinstance(inputs, Mapping) else None
        if isinstance(value, str) and value:
            result.append(
                H3FixedDependency(
                    node_id=node_id,
                    class_type=str(node["class_type"]),
                    input_name=input_name,
                    value="<workflow file>",
                )
            )
    return tuple(result)


def _unmapped_reachable_file_nodes(
    graph: Mapping[str, Any],
    output_reachability: Mapping[str, list[str]],
    h3_id: str | None,
    links: list[tuple[str, str, str]],
) -> set[str]:
    """Compatibility helper retained until boundary validation is rewritten."""
    del h3_id
    file_nodes = {
        node_id
        for node_id, node in graph.items()
        if isinstance(node, Mapping)
        and node.get("class_type") in {"LoadImage", "LoadAudio"}
    }
    return {
        source_id
        for source_id, target_id, _input_name in links
        if source_id in file_nodes and output_reachability.get(target_id)
    }


def _inspect_h3_workflow(
    graph: object,
    *,
    object_info: Mapping[str, Any] | None,
    output_node_id: str | None,
) -> H3WorkflowAnalysis:
    normalized = _load_graph(graph)
    issues = _structural_issues(normalized)
    if issues:
        return H3WorkflowAnalysis(
            compatibility="unsupported",
            issues=tuple(issues),
        )

    outgoing, incoming, _links = _graph_edges(normalized)
    terminal_ids = {node_id for node_id, targets in outgoing.items() if not targets}
    terminal_candidates = [
        _candidate(
            node_id,
            normalized,
            object_info=object_info,
            terminal=True,
        )
        for node_id in terminal_ids
    ]
    if object_info is None:
        output_candidates = terminal_candidates
        issues.append(
            H3AnalysisIssue(
                code="object_info_unavailable",
                message="Live ComfyUI node metadata is unavailable; confirm a terminal output",
            )
        )
    else:
        output_candidates = [
            candidate
            for candidate in terminal_candidates
            if candidate.output_node and _looks_video_capable(candidate)
        ]
    output_candidates.sort(key=_numeric_node_key)

    if output_node_id is None:
        compatibility = "needs_confirmation" if output_candidates else "unsupported"
        if not output_candidates:
            issues.append(
                H3AnalysisIssue(
                    code="missing_output_candidate",
                    message="No terminal video output candidates were found",
                )
            )
        return H3WorkflowAnalysis(
            compatibility=compatibility,
            output_candidates=tuple(output_candidates),
            issues=tuple(issues),
        )

    selected = next(
        (
            candidate
            for candidate in output_candidates
            if candidate.node_id == output_node_id
        ),
        None,
    )
    if selected is None:
        issues.append(
            H3AnalysisIssue(
                code="invalid_output_selection",
                message=f"Node {output_node_id} is not an eligible terminal video output",
                node_id=output_node_id,
                node_name=f"Node {output_node_id}",
            )
        )
        return H3WorkflowAnalysis(
            compatibility="unsupported",
            output_candidates=tuple(output_candidates),
            issues=tuple(issues),
        )

    upstream = _ancestors(output_node_id, incoming)
    h3_candidates = sorted(
        (
            _candidate(node_id, normalized, object_info=object_info)
            for node_id in upstream
            if normalized[node_id].get("class_type") == _H3_CLASS
        ),
        key=_numeric_node_key,
    )
    seed_candidates = sorted(
        (
            _candidate(node_id, normalized, object_info=object_info)
            for node_id in upstream
            if normalized[node_id].get("class_type") == "RandomNoise"
            and "noise_seed" in normalized[node_id]["inputs"]
        ),
        key=_numeric_node_key,
    )
    mapping: H3BoundaryMapping | None = None
    if not h3_candidates:
        issues.append(
            H3AnalysisIssue(
                code="missing_upstream_h3",
                message="The selected output has no upstream MiniMax H3 Ref2AV node",
                node_id=output_node_id,
                node_name=selected.display_name,
            )
        )
        compatibility = "unsupported"
    elif len(h3_candidates) > 1:
        compatibility = "needs_confirmation"
    else:
        h3_id = h3_candidates[0].node_id
        h3_inputs = normalized[h3_id]["inputs"]
        missing = [
            name
            for name in ("prompt", "width", "height", "length")
            if name not in h3_inputs
        ]
        for input_name in missing:
            issues.append(
                H3AnalysisIssue(
                    code="missing_h3_input",
                    message=f"{h3_candidates[0].display_name} (Node {h3_id}) is missing {input_name}",
                    node_id=h3_id,
                    node_name=h3_candidates[0].display_name,
                    input_name=input_name,
                )
            )
        if missing:
            compatibility = "unsupported"
        else:
            seed = seed_candidates[0] if len(seed_candidates) == 1 else None
            mapping = H3BoundaryMapping(
                inputs=H3InputMapping(
                    h3_node_id=h3_id,
                    prompt_input="prompt",
                    width_input="width",
                    height_input="height",
                    frames_input="length",
                    picture_input_pattern="ref_images.ref_image_{index}",
                    # Comfy's dynamic Ref2AV sockets are not serialized until a
                    # reference is connected, so absence from the API graph does
                    # not mean the H3 node lacks standalone-audio support.
                    audio_input_pattern="ref_audios.ref_audio_{index}",
                    seed_node_id=seed.node_id if seed else None,
                    seed_input="noise_seed" if seed else None,
                ),
                output=H3OutputSelection(node_id=output_node_id),
            )
            compatibility = (
                "needs_confirmation" if len(seed_candidates) > 1 else "auto_compatible"
            )

    return H3WorkflowAnalysis(
        compatibility=compatibility,
        mapping=mapping,
        output_candidates=tuple(output_candidates),
        h3_candidates=tuple(h3_candidates),
        seed_candidates=tuple(seed_candidates),
        fixed_dependencies=_fixed_dependencies(normalized, upstream),
        issues=tuple(issues),
    )


def inspect_h3_workflow(
    graph: object,
    *,
    object_info: Mapping[str, Any] | None = None,
    output_node_id: str | None = None,
) -> H3WorkflowAnalysis:
    """Inspect a graph without executing it or exposing raw workflow values."""
    try:
        return _inspect_h3_workflow(
            graph,
            object_info=object_info,
            output_node_id=output_node_id,
        )
    except RecursionError:
        message = "workflow nesting exceeds depth 32"
    except ValueError as exc:
        if "nesting" not in str(exc):
            raise
        message = str(exc)
    return H3WorkflowAnalysis(
        compatibility="unsupported",
        issues=(H3AnalysisIssue(code="invalid_structure", message=message),),
    )


__all__ = [
    "MAX_EDGES",
    "MAX_NESTING",
    "MAX_NODES",
    "MAX_STRING_BYTES",
    "MAX_WORKFLOW_BYTES",
    "inspect_h3_workflow",
]
