"""Phase 1 image workflow profiles: contracts, inspector, validator.

The bundled prop and layout graphs must map cleanly through the same
machinery a user's custom graph will use — nothing here may rely on
the bundled graphs' node ids, which the renumbering test proves.
"""
import copy
import json
from pathlib import Path

import pytest

from app.workflow_profiles.image import (
    ImageBoundaryMapping,
    ImageWorkflowProfile,
    contract_for,
    inspect_image_workflow,
    validate_mapping,
)

WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"


def load_graph(filename: str) -> dict:
    return json.loads((WORKFLOWS / filename).read_text(encoding="utf-8"))


def renumber(graph: dict, shift: int = 100) -> dict:
    """Shift every numeric node id and rewrite links accordingly."""

    def nid(value: str) -> str:
        return str(int(value) + shift) if value.isdigit() else value

    out = {}
    for key, node in graph.items():
        moved = copy.deepcopy(node)
        inputs = {}
        for name, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                inputs[name] = [nid(value[0]), value[1]]
            else:
                inputs[name] = value
        moved["inputs"] = inputs
        out[nid(key)] = moved
    return out


def bindings(mapping, field):
    return {(b.node_id, getattr(b, "input_name", "")) for b in getattr(mapping, field)}


# --- bundled graphs -----------------------------------------------------


def test_bundled_prop_graph_maps_and_validates():
    graph = load_graph("qwen_prop_master.api.json")
    result = inspect_image_workflow(graph, "prop")
    assert result.issues == []
    mapping = result.proposed_mapping
    assert mapping is not None
    # Three positive encodes (master / front / side) all take the prompt.
    assert bindings(mapping, "texts") >= {("10", "prompt"), ("20", "prompt"), ("21", "prompt")}
    assert ("14", "prompt") in bindings(mapping, "texts")  # negative role, "prompt" input
    negative = [b for b in mapping.texts if b.role == "negative"]
    assert [(b.node_id, b.input_name) for b in negative] == [("14", "prompt")]
    assert [(b.index, b.node_id) for b in mapping.images] == [(0, "7")]
    assert {b.node_id for b in mapping.seeds} == {"11", "23", "24"}
    assert mapping.size is not None and mapping.size.node_id == "6"
    assert [(b.role, b.node_id) for b in mapping.outputs] == [("master", "13")]
    assert validate_mapping(graph, "prop", mapping) == []


def test_bundled_layout_graph_maps_and_validates():
    graph = load_graph("qwen_image_21_layout.api.json")
    result = inspect_image_workflow(graph, "layout")
    assert result.issues == []
    mapping = result.proposed_mapping
    assert mapping is not None
    # TextEncodeQwenImage21 is a combined encode: one node, two roles,
    # two distinct string inputs.
    assert bindings(mapping, "texts") == {("10", "prompt"), ("10", "negative_prompt")}
    assert mapping.images == []  # layout references are optional
    assert {b.node_id for b in mapping.seeds} == {"12"}
    assert [(b.role, b.node_id) for b in mapping.outputs] == [("layout", "14")]
    assert validate_mapping(graph, "layout", mapping) == []


def test_inspector_follows_structure_not_node_ids():
    graph = renumber(load_graph("qwen_prop_master.api.json"), shift=100)
    result = inspect_image_workflow(graph, "prop")
    assert result.issues == []
    mapping = result.proposed_mapping
    assert mapping is not None
    assert [(b.role, b.node_id) for b in mapping.outputs] == [("master", "113")]
    assert [(b.index, b.node_id) for b in mapping.images] == [(0, "107")]
    assert {b.node_id for b in mapping.seeds} == {"111", "123", "124"}
    assert validate_mapping(graph, "prop", mapping) == []


# --- ambiguity and validation -------------------------------------------


def test_two_save_nodes_require_a_choice():
    graph = load_graph("qwen_image_21_layout.api.json")
    graph["30"] = {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "x"}}
    result = inspect_image_workflow(graph, "layout")
    assert result.proposed_mapping is None
    assert any(issue.code == "ambiguous_output" for issue in result.issues)


def test_validator_rejects_missing_required_output():
    graph = load_graph("qwen_image_21_layout.api.json")
    mapping = inspect_image_workflow(graph, "layout").proposed_mapping
    broken = mapping.model_copy(update={"outputs": []})
    issues = validate_mapping(graph, "layout", broken)
    assert any(issue.code == "missing_required_output" for issue in issues)


def test_validator_rejects_image_binding_to_non_loadimage():
    graph = load_graph("qwen_prop_master.api.json")
    mapping = inspect_image_workflow(graph, "prop").proposed_mapping
    wrong = mapping.model_copy(update={
        "images": [mapping.images[0].model_copy(update={"node_id": "13"})]})
    issues = validate_mapping(graph, "prop", wrong)
    assert any(issue.code == "wrong_node_class" for issue in issues)


def test_validator_rejects_prop_without_reference_image():
    graph = load_graph("qwen_prop_master.api.json")
    mapping = inspect_image_workflow(graph, "prop").proposed_mapping
    stripped = mapping.model_copy(update={"images": []})
    issues = validate_mapping(graph, "prop", stripped)
    assert any(issue.code == "image_count_out_of_range" for issue in issues)


def test_unknown_kind_has_no_contract():
    with pytest.raises(ValueError, match="no image workflow contract"):
        contract_for("actor")


def test_profile_record_shape():
    graph = load_graph("qwen_image_21_layout.api.json")
    mapping = inspect_image_workflow(graph, "layout").proposed_mapping
    profile = ImageWorkflowProfile(
        id="my-layout", kind="layout", workflow_sha256="a" * 64,
        mapping=mapping, status="mapped")
    assert profile.contract_version == 1
    assert profile.variant == "default"
    assert ImageBoundaryMapping.model_validate(profile.mapping.model_dump()) == mapping
