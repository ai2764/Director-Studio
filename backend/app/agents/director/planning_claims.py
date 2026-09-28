"""Shared evidence boundary for planning claims; meaning is judged by the LLM."""
from __future__ import annotations

import json
import logging


CLAIM_REVIEW_RULES = """
Before reporting a contradiction, determine the requirement's scope: whole film,
scene, or specific shot. Explain why it applies to this shot in the issue reason.
Evaluate project-wide participation, role assignments and topic coverage across
the complete storyboard. Do not expand them into an obligation for every character
to appear or speak in every shot. A missing mention in one field is not a statement
that the character is absent from the scene or that their role is abandoned.
Distinguish participation in the scene, visibility in the frame, camera operation,
and audible performance. A camera operator or off-screen narrator can participate
and speak without being visible; a visible listener can be silent. A face reference
requirement conditional on appearing does not itself require the character to appear.
Check explicit POV, on-camera address and required dialogue against the combined
beat, camera fields and attributed dialogue. Preserve requirements that really do
apply to the shot. Do not resolve uncertainty by inventing a mandatory composition
or speech. A quote's presence proves its source, not the model's interpretation:
state the actual incompatible instructions and rule out a coherent reading first.
"""


def grounded_conflicts(project, shots, claims, requests):
    """Validate quotes at every boundary against that boundary's shot snapshot."""
    from .asset_catalog import _script_hash

    by_id = {shot.id: shot for shot in shots}
    shared_sources = [project.script_text, *requests]
    confirmed = project.asset_coverage_review
    if confirmed and confirmed.script_hash == _script_hash(project.script_text):
        shared_sources.append(confirmed.notes)
        shared_sources.extend(json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
            for item in confirmed.recommendations if item.resolution != "pending")
    accepted, rejected = [], []
    for claim in claims:
        shot = by_id.get(claim.shot_id)
        sources = list(shared_sources)
        if shot is not None:
            sources.extend([shot.script_beat, shot.feedback,
                shot.meta.get("prompt_revision_request", ""),
                *shot.meta.get("prompt_revision_requests", [])])
        field = getattr(shot, claim.field, "") if shot else ""
        field_text = "\n".join(field) if isinstance(field, list) else str(field)
        if (shot is not None and claim.requirement_quote.strip() and claim.shot_quote.strip()
                and any(claim.requirement_quote in source for source in sources if isinstance(source, str))
                and claim.shot_quote in field_text):
            accepted.append(claim)
        else:
            rejected.append(claim)
            logging.getLogger(__name__).warning("Ignored ungrounded planning claim: %s", claim.model_dump())
    return accepted, rejected
