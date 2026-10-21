"""Deterministic graph inspection for custom image workflows.

Given a ComfyUI API graph and an asset kind, propose the boundary
mapping between the kind's semantic contract and the graph's nodes.
The analysis is structural only — node classes, input names and link
topology — never titles or prompt text, so a user's own graph maps the
same way as a bundled one. Where the graph is ambiguous the inspector
returns candidates and issues instead of guessing, mirroring the H3
inspector's contract (``workflow_profiles/h3/inspector.py``).

Phase 1 scope: text lives in TextEncode-style nodes (the Qwen/Flux
families used by the prop and layout pipelines). Graphs that keep text
in free-standing string nodes feeding an encode are reported as
candidates for the encode only; mapping the string node itself is a
later refinement.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from .contracts import contract_for
from .models import (
    ImageAnalysisIssue,
    ImageBoundaryMapping,
    ImageNodeCandidate,
    ImageOutputBinding,
    ImageRefBinding,
    ImageSeedBinding,
    ImageSizeBinding,
    ImageTextBinding,
)

_TEXT_INPUT_PRIORITY = ("prompt", "text", "value")
_LATENT_SIZE_CLASSES = {
    "EmptyLatentImage",
    "EmptySD3LatentImage",
    "EmptyQwenImageLatent",
    "EmptyLatent",
}


class ImageInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    candidates: list[ImageNodeCandidate]
    proposed_mapping: ImageBoundaryMapping | None = None
    issues: list[ImageAnalysisIssue]


def _is_link(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    )


def _node_id_sort_key(node_id: str) -> tuple[int, int | str]:
    return (0, int(node_id)) if node_id.isdigit() else (1, node_id)


def _incoming(graph: Mapping[str, Any]) -> dict[str, set[str]]:
    incoming: dict[str, set[str]] = {node_id: set() for node_id in graph}
    for node_id, node in graph.items():
        if not isinstance(node, Mapping):
            continue
        for value in (node.get("inputs") or {}).values():
            if _is_link(value) and value[0] in incoming:
                incoming[node_id].add(value[0])
    return incoming


def _ancestors(starts: set[str], incoming: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        stack.extend(incoming.get(node_id, ()))
    return seen


def _linked_source(node: Mapping[str, Any], input_name: str) -> str | None:
    value = (node.get("inputs") or {}).get(input_name)
    return value[0] if _is_link(value) else None


def _is_sampler(node: Mapping[str, Any]) -> bool:
    class_type = str(node.get("class_type") or "")
    return "KSampler" in class_type or class_type in {"SamplerCustom", "SamplerCustomAdvanced"}


def _is_text_encode(node: Mapping[str, Any]) -> bool:
    return "TextEncode" in str(node.get("class_type") or "")


def _text_input_name(node: Mapping[str, Any]) -> str | None:
    inputs = node.get("inputs") or {}
    for name in _TEXT_INPUT_PRIORITY:
        if isinstance(inputs.get(name), str):
            return name
    return None


def _title(node: Mapping[str, Any]) -> str:
    meta = node.get("_meta") or {}
    return str(meta.get("title") or "") if isinstance(meta, Mapping) else ""


def inspect_image_workflow(graph: Mapping[str, Any], kind: str) -> ImageInspection:
    contract = contract_for(kind)
    issues: list[ImageAnalysisIssue] = []
    candidates: list[ImageNodeCandidate] = []
    incoming = _incoming(graph)

    samplers = sorted(
        (nid for nid, n in graph.items() if isinstance(n, Mapping) and _is_sampler(n)),
        key=_node_id_sort_key,
    )
    if not samplers:
        issues.append(ImageAnalysisIssue(
            code="no_sampler", message="No sampler node found; seed cannot be controlled."))

    positive_roots: set[str] = set()
    negative_roots: set[str] = set()
    for nid in samplers:
        if (src := _linked_source(graph[nid], "positive")) is not None:
            positive_roots.add(src)
        if (src := _linked_source(graph[nid], "negative")) is not None:
            negative_roots.add(src)
    positive_ancestors = _ancestors(positive_roots, incoming)
    negative_ancestors = _ancestors(negative_roots, incoming)

    # --- text roles -----------------------------------------------------
    prompt_bindings: list[ImageTextBinding] = []
    negative_bindings: list[ImageTextBinding] = []
    for nid in sorted(graph, key=_node_id_sort_key):
        node = graph[nid]
        if not isinstance(node, Mapping) or not _is_text_encode(node):
            continue
        input_name = _text_input_name(node)
        if input_name is None:
            continue
        in_positive = nid in positive_ancestors
        in_negative = nid in negative_ancestors
        # Combined encodes (e.g. TextEncodeQwenImage21) emit positive and
        # negative conditioning from one node but keep separate string
        # inputs ("prompt" / "negative_prompt") — that is a legitimate
        # two-role binding. A single-string encode feeding both sides is
        # the genuinely ambiguous case: it cannot split roles.
        has_negative_input = isinstance((node.get("inputs") or {}).get("negative_prompt"), str)
        if in_positive and in_negative and not has_negative_input:
            issues.append(ImageAnalysisIssue(
                code="shared_conditioning_encode",
                message="One text encode feeds both positive and negative conditioning; "
                        "it cannot be bound to a single text role.",
                node_id=nid))
            continue
        class_type = str(node.get("class_type") or "")
        if in_positive:
            candidates.append(ImageNodeCandidate(
                role="prompt", node_id=nid, class_type=class_type,
                input_name=input_name, title=_title(node)))
            prompt_bindings.append(ImageTextBinding(role="prompt", node_id=nid, input_name=input_name))
        if has_negative_input and (in_positive or in_negative):
            candidates.append(ImageNodeCandidate(
                role="negative", node_id=nid, class_type=class_type,
                input_name="negative_prompt", title=_title(node)))
            negative_bindings.append(ImageTextBinding(
                role="negative", node_id=nid, input_name="negative_prompt"))
        elif in_negative and not in_positive:
            candidates.append(ImageNodeCandidate(
                role="negative", node_id=nid, class_type=class_type,
                input_name=input_name, title=_title(node)))
            negative_bindings.append(ImageTextBinding(role="negative", node_id=nid, input_name=input_name))

    # --- reference images ----------------------------------------------
    load_nodes = sorted(
        (nid for nid, n in graph.items()
         if isinstance(n, Mapping) and n.get("class_type") == "LoadImage"),
        key=_node_id_sort_key,
    )
    image_bindings: list[ImageRefBinding] = []
    for index, nid in enumerate(load_nodes):
        candidates.append(ImageNodeCandidate(
            role="reference_images", node_id=nid, class_type="LoadImage",
            input_name="image", title=_title(graph[nid])))
        if index < contract.images.max_count:
            image_bindings.append(ImageRefBinding(index=index, node_id=nid))
    if len(load_nodes) > contract.images.max_count:
        issues.append(ImageAnalysisIssue(
            code="too_many_image_inputs",
            message=f"Graph exposes {len(load_nodes)} LoadImage nodes but kind "
                    f"{kind!r} accepts at most {contract.images.max_count} reference images."))
    if len(load_nodes) < contract.images.min_count:
        issues.append(ImageAnalysisIssue(
            code="missing_image_input",
            message=f"Kind {kind!r} requires at least {contract.images.min_count} "
                    "reference image input(s); the graph has none mapped."))

    # --- seeds -----------------------------------------------------------
    seed_bindings: list[ImageSeedBinding] = []
    for nid in sorted(graph, key=_node_id_sort_key):
        node = graph[nid]
        if not isinstance(node, Mapping):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") or {}
        seed_input = next(
            (name for name in ("seed", "noise_seed")
             if isinstance(inputs.get(name), int) and not isinstance(inputs.get(name), bool)),
            None,
        )
        if seed_input and (_is_sampler(node) or class_type == "RandomNoise"):
            candidates.append(ImageNodeCandidate(
                role="seed", node_id=nid, class_type=class_type,
                input_name=seed_input, title=_title(node)))
            seed_bindings.append(ImageSeedBinding(node_id=nid, input_name=seed_input))

    # --- latent size (optional) -----------------------------------------
    size_binding: ImageSizeBinding | None = None
    size_candidates = [
        nid for nid in sorted(graph, key=_node_id_sort_key)
        if isinstance(graph[nid], Mapping)
        and (str(graph[nid].get("class_type") or "") in _LATENT_SIZE_CLASSES
             or ("Latent" in str(graph[nid].get("class_type") or "")
                 and isinstance((graph[nid].get("inputs") or {}).get("width"), int)
                 and isinstance((graph[nid].get("inputs") or {}).get("height"), int)))
    ]
    for nid in size_candidates:
        candidates.append(ImageNodeCandidate(
            role="size", node_id=nid, class_type=str(graph[nid].get("class_type") or ""),
            title=_title(graph[nid])))
    if len(size_candidates) == 1:
        size_binding = ImageSizeBinding(node_id=size_candidates[0])

    # --- outputs ----------------------------------------------------------
    save_nodes = sorted(
        (nid for nid, n in graph.items()
         if isinstance(n, Mapping) and str(n.get("class_type") or "").startswith("SaveImage")),
        key=_node_id_sort_key,
    )
    for nid in save_nodes:
        candidates.append(ImageNodeCandidate(
            role="output", node_id=nid, class_type=str(graph[nid].get("class_type") or ""),
            title=_title(graph[nid])))
    output_bindings: list[ImageOutputBinding] = []
    if len(save_nodes) == len(contract.outputs):
        output_bindings = [
            ImageOutputBinding(role=slot.role, node_id=nid)
            for slot, nid in zip(contract.outputs, save_nodes, strict=True)
        ]
    elif len(save_nodes) < len(contract.required_outputs()):
        issues.append(ImageAnalysisIssue(
            code="missing_output",
            message=f"Kind {kind!r} requires {len(contract.required_outputs())} output(s); "
                    f"the graph has {len(save_nodes)} SaveImage node(s)."))
    else:
        issues.append(ImageAnalysisIssue(
            code="ambiguous_output",
            message=f"Graph has {len(save_nodes)} SaveImage nodes for "
                    f"{len(contract.outputs)} output slot(s); a choice is required."))

    # --- proposed mapping -------------------------------------------------
    blocking = {"too_many_image_inputs", "missing_image_input", "missing_output",
                "ambiguous_output", "shared_conditioning_encode"}
    proposed: ImageBoundaryMapping | None = None
    if not any(issue.code in blocking for issue in issues):
        if any(spec.required and spec.role == "prompt" for spec in contract.texts) and not prompt_bindings:
            issues.append(ImageAnalysisIssue(
                code="missing_prompt",
                message=f"Kind {kind!r} requires a prompt text input; none was found "
                        "feeding positive conditioning."))
        else:
            proposed = ImageBoundaryMapping(
                texts=[*prompt_bindings, *negative_bindings],
                images=image_bindings,
                seeds=seed_bindings,
                size=size_binding,
                outputs=output_bindings,
            )
    return ImageInspection(
        kind=kind, candidates=candidates, proposed_mapping=proposed, issues=issues)
