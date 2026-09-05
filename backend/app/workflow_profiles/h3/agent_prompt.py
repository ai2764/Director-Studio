"""Compact, capability-bounded prompt for H3 mapping proposals."""

from __future__ import annotations

import json
from typing import Any

from .models import H3WorkflowAnalysis

PICTURE_INPUT_PATTERN = "ref_images.ref_image_{index}"
AUDIO_INPUT_PATTERN = "ref_audios.ref_audio_{index}"

SYSTEM_PROMPT = """You propose a Director Studio H3 Ref2AV boundary mapping.
You may select only node IDs and input names listed in the supplied inspected
candidates and redacted manifest. You cannot change the workflow, add nodes,
edit graph topology, activate a profile, access files, or invent values.
Contract v1 requires picture_input_pattern exactly ref_images.ref_image_{index}.
audio_input_pattern may be exactly ref_audios.ref_audio_{index} or null.
Return exactly one JSON object matching the supplied MappingProposal schema.
Explain the reason for the selected candidate mapping concisely.
"""


def mapping_messages(analysis: H3WorkflowAnalysis) -> list[dict[str, Any]]:
    """Build messages from inspector-owned safe data, never workflow bytes."""
    payload = {
        "manifest": analysis.agent_manifest,
        "h3_candidate_ids": [
            str(node["node_id"])
            for node in analysis.agent_manifest.get("nodes", [])
            if "h3" in node.get("candidate_roles", [])
        ],
        "seed_candidate_ids": [
            candidate.node_id for candidate in analysis.seed_candidates
        ],
        "saver_candidate_ids": [
            candidate.node_id for candidate in analysis.saver_candidates
        ],
        "contract_v1_dynamic_socket_patterns": {
            "picture_input_pattern": PICTURE_INPUT_PATTERN,
            "audio_input_pattern": [AUDIO_INPUT_PATTERN, None],
        },
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


__all__ = [
    "AUDIO_INPUT_PATTERN",
    "PICTURE_INPUT_PATTERN",
    "SYSTEM_PROMPT",
    "mapping_messages",
]
