"""Constrained Ollama adviser for ambiguous H3 boundary mappings."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from app.core.vram import get_director_model, get_orchestrator

from .agent_prompt import (
    AUDIO_INPUT_PATTERN,
    PICTURE_INPUT_PATTERN,
    mapping_messages,
)
from .inspector import inspect_h3_workflow
from .models import H3BoundaryMapping, H3WorkflowAnalysis
from .store import H3ProfileStore


class ContractError(RuntimeError):
    """An agent response exceeded the deterministic inspection contract."""


class AgentConfigurationError(RuntimeError):
    """Local mapping advice cannot run with the current configuration."""


class MappingProposal(BaseModel):
    """Untrusted advisory mapping plus human-readable selection reasons."""

    model_config = ConfigDict(extra="forbid")

    mapping: H3BoundaryMapping
    explanations: tuple[Annotated[StrictStr, Field(min_length=1)], ...] = Field(
        min_length=1
    )

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Expose the guarded dynamic socket choices to structured generation."""
        schema = super().model_json_schema(*args, **kwargs)
        mapping_schema = schema["$defs"]["H3BoundaryMapping"]
        properties = mapping_schema["properties"]
        properties["picture_input_pattern"] = {
            "const": PICTURE_INPUT_PATTERN,
            "title": "Picture Input Pattern",
            "type": "string",
        }
        properties["audio_input_pattern"] = {
            "anyOf": [
                {"const": AUDIO_INPUT_PATTERN, "type": "string"},
                {"type": "null"},
            ],
            "title": "Audio Input Pattern",
        }
        return schema


_THINKING_PREFIX_RE = re.compile(
    r"^\s*(?:<think>[\s\S]*?</(?:think|redacted_reasoning)>|"
    r"<thinking>[\s\S]*?</thinking>|"
    r"<reasoning>[\s\S]*?</reasoning>)",
    re.IGNORECASE,
)


def _json_object(content: object) -> dict[str, Any]:
    raw = str(content or "")
    while match := _THINKING_PREFIX_RE.match(raw):
        raw = raw[match.end() :]
    raw = raw.strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError("mapping agent returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ContractError("mapping agent must return one JSON object")
    return value


def _candidate_ids(analysis: H3WorkflowAnalysis) -> dict[str, set[str]]:
    h3_ids = {
        str(node.get("node_id"))
        for node in analysis.agent_manifest.get("nodes", [])
        if isinstance(node, Mapping) and "h3" in node.get("candidate_roles", [])
    }
    return {
        "h3_node_id": h3_ids,
        "seed_node_id": {candidate.node_id for candidate in analysis.seed_candidates},
        "saver_node_id": {candidate.node_id for candidate in analysis.saver_candidates},
    }


def _assert_raw_ids_are_candidates(
    payload: Mapping[str, Any], analysis: H3WorkflowAnalysis
) -> None:
    allowed = _candidate_ids(analysis)
    nested = payload.get("mapping")
    selections = nested if isinstance(nested, Mapping) else payload
    for field, candidate_ids in allowed.items():
        value = selections.get(field)
        if isinstance(value, str) and value not in candidate_ids:
            raise ContractError(f"{field} {value!r} is not an inspected candidate")


def _manifest_nodes(analysis: H3WorkflowAnalysis) -> dict[str, Mapping[str, Any]]:
    return {
        str(node.get("node_id")): node
        for node in analysis.agent_manifest.get("nodes", [])
        if isinstance(node, Mapping) and isinstance(node.get("node_id"), str)
    }


def _assert_input(
    nodes: Mapping[str, Mapping[str, Any]],
    *,
    node_id: str,
    input_name: str,
    field: str,
) -> None:
    names = nodes.get(node_id, {}).get("input_names", [])
    if input_name not in names:
        raise ContractError(
            f"{field} {input_name!r} is not an inspected input for node {node_id}"
        )


def _assert_proposal_is_bounded(
    proposal: MappingProposal, analysis: H3WorkflowAnalysis
) -> None:
    mapping = proposal.mapping
    allowed = _candidate_ids(analysis)
    for field, candidate_ids in allowed.items():
        value = getattr(mapping, field)
        if value not in candidate_ids:
            raise ContractError(f"{field} {value!r} is not an inspected candidate")

    nodes = _manifest_nodes(analysis)
    for field in ("prompt_input", "width_input", "height_input", "frames_input"):
        _assert_input(
            nodes,
            node_id=mapping.h3_node_id,
            input_name=getattr(mapping, field),
            field=field,
        )
    if mapping.picture_input_pattern != PICTURE_INPUT_PATTERN:
        raise ContractError("picture_input_pattern is not the contract-v1 pattern")
    if mapping.audio_input_pattern not in (None, AUDIO_INPUT_PATTERN):
        raise ContractError("audio_input_pattern is not a contract-v1 pattern")
    if mapping.output_fields != ("videos",):
        raise ContractError("output_fields is not the contract-v1 output selection")
    _assert_input(
        nodes,
        node_id=mapping.seed_node_id,
        input_name=mapping.seed_input,
        field="seed_input",
    )
    _assert_input(
        nodes,
        node_id=mapping.saver_node_id,
        input_name=mapping.output_prefix_input,
        field="output_prefix_input",
    )

    reachable = nodes.get(mapping.seed_node_id, {}).get("reachable_outputs", [])
    if mapping.saver_node_id not in reachable:
        raise ContractError(
            f"seed candidate {mapping.seed_node_id} does not reach saver candidate "
            f"{mapping.saver_node_id}"
        )


class H3MappingProposer:
    """Propose, but never persist, a mapping from deterministic analysis."""

    async def propose(self, analysis: H3WorkflowAnalysis) -> MappingProposal:
        if analysis.compatibility == "auto_compatible":
            if analysis.mapping is None:
                raise ContractError("auto-compatible analysis has no mapping")
            proposal = MappingProposal(
                mapping=analysis.mapping,
                explanations=(
                    "Deterministic inspection found one compatible mapping.",
                ),
            )
            _assert_proposal_is_bounded(proposal, analysis)
            return proposal
        if analysis.compatibility != "needs_confirmation":
            raise ContractError("unsupported workflow has no mapping proposal")

        model = (get_director_model() or "").strip()
        if not model:
            raise AgentConfigurationError(
                "Select a Director Ollama model before requesting mapping advice"
            )
        messages = mapping_messages(analysis)
        orchestrator = get_orchestrator()
        async with orchestrator.llm_session(on_status=None):
            response = await orchestrator.ollama.chat_response(
                model,
                messages=messages,
                format=MappingProposal.model_json_schema(),
                options={"temperature": 0},
            )

        payload = _json_object(
            response.get("content") if isinstance(response, dict) else None
        )
        _assert_raw_ids_are_candidates(payload, analysis)
        try:
            proposal = MappingProposal.model_validate(payload)
        except ValidationError as exc:
            raise ContractError("mapping agent returned an invalid proposal") from exc
        _assert_proposal_is_bounded(proposal, analysis)
        return proposal


async def propose_h3_mapping(import_id: str) -> MappingProposal:
    """Inspect one opaque import and return an advisory, unpersisted proposal."""
    graph = H3ProfileStore().load_import_workflow(import_id)
    return await H3MappingProposer().propose(inspect_h3_workflow(graph))


__all__ = [
    "AgentConfigurationError",
    "ContractError",
    "H3MappingProposer",
    "MappingProposal",
    "propose_h3_mapping",
]
