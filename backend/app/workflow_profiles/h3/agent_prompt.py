"""Compact, capability-bounded prompt for H3 mapping proposals."""

from __future__ import annotations

import json
from typing import Any

from .models import H3WorkflowAnalysis

SYSTEM_PROMPT = """You propose a Director Studio H3 Ref2AV boundary mapping.
You may select only node IDs and input names listed in the supplied inspected
candidates and redacted manifest. You cannot change the workflow, add nodes,
edit graph topology, activate a profile, access files, or invent values.
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
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


__all__ = ["SYSTEM_PROMPT", "mapping_messages"]
