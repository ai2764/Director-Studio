"""Shared Director dialogue preparation and prompt provenance checks."""
from __future__ import annotations

import json

from ...core.projects.models import ProjectMode, PromptSections
from ...core.projects.dialogue import (DialogueLine, DialogueContractError, DialogueIssue,
    dialogue_contract_signature, verify_dialogue_sources, digest)
from ...core.h3.dialogue_binding import (DialoguePromptDraft,
    validate_dialogue_uses, speech_blocks)
from .brief import directing_requests
from .dialogue_grounding import ground_dialogue
from .planner import _extract_json_payload, parse_prompt_sections_json


WRITER_CONTRACT = """
Director internal response contract (the final H3 prompt still has exactly six sections):
Return {"prompt_sections": {the six nonempty section strings}, "dialogue_uses": [...]}.
Place {{speech:line_id}} in detailed_description where each source line is spoken.
The backend emits <d>[Language] exact source words</d> and attribution metadata; do not
write those blocks yourself. You may omit dialogue_uses or return an empty list for these
references. Cover every supplied line ID once in source order, including distinct IDs for
repeated words. To split one line across cuts, use {{speech:line_id:start:end}} with zero-based
Unicode character offsets and an exclusive end. Those spans must be contiguous and cover
the entire source text once in order. Different speakers get their own references.
Existing final H3 drafts may retain valid <d> blocks with explicit dialogue_uses entries:
line_ids:[source line IDs], speaker_id:source narrative ID, block_indexes:[zero-based <d>
occurrences]. Never mix final blocks and speech references. Keep prose, pacing, camera and acting choices free;
make prose consistent with attribution. Do not rewrite source words or reassign speakers.
Respect the current directing requirements, including each speaker's language/accent, without
changing dialogue to simulate accent. A missing Voice asset does not reassign dialogue.
These bindings are internal metadata and must not appear as extra H3 sections.
Self-check for prose that contradicts these bindings. Correct it before returning; if a clear
conflict remains, report optional dialogue_conflicts entries with line_id, actual_speaker_id,
an exact quote including the speech block, and confidence: clear or uncertain. Do not invent
findings merely because a character lacks a reference or because the staging is unusual.
"""


async def prepare_dialogue(project, shot, provider):
    if project.mode != ProjectMode.director:
        return None
    if shot.dialogue_lines is not None:
        return await ground_dialogue(project, shot, provider)
    record = shot.meta.get("prompt_dialogue_contract") or {}
    if record.get("lines"):
        try:
            lines = [DialogueLine.model_validate(x) for x in record["lines"]]
            verify_dialogue_sources(project, shot, lines)
            return lines
        except ValueError:
            pass
    return await ground_dialogue(project, shot, provider)


def parse_dialogue_draft(raw: str) -> DialoguePromptDraft:
    payload = _extract_json_payload(raw)
    if isinstance(payload, dict) and "prompt_sections" not in payload:
        # Report malformed section data before the missing binding envelope.
        parse_prompt_sections_json(raw)
    section_payload = payload.get("prompt_sections") if isinstance(payload, dict) else None
    detail = section_payload.get("detailed_description", "") if isinstance(section_payload, dict) else ""
    detail = detail if isinstance(detail, str) else ""
    if not isinstance(payload, dict) or ("dialogue_uses" not in payload and "{{speech" not in detail):
        raise DialogueContractError([DialogueIssue(code="dialogue_bindings_missing",
            expected="prompt_sections with {{speech:line_id}} references or explicit dialogue_uses",
            actual=str(payload), action="Place supplied source IDs in speech references, or bind every existing speech block.")])
    sections = PromptSections(**parse_prompt_sections_json(json.dumps(payload.get("prompt_sections"))))
    return DialoguePromptDraft(prompt_sections=sections, dialogue_uses=payload.get("dialogue_uses", []),
        dialogue_conflicts=payload.get("dialogue_conflicts", []))


def prompt_dialogue_record(project, shot, lines, draft):
    if lines is None:
        return None
    uses = [use.model_dump(mode="json", exclude_none=True) for use in draft.dialogue_uses] if draft else []
    source = dialogue_contract_signature(project, shot, lines, directing_requests(project))
    signature = digest([source, shot.prompt_sections.model_dump(mode="json"), uses])
    return {"lines": [x.model_dump(mode="json") for x in lines], "uses": uses, "signature": signature,
            "conflicts": [x.model_dump(mode="json") for x in draft.dialogue_conflicts] if draft else []}


def dialogue_contract_current(project, shot) -> bool:
    if project.mode != ProjectMode.director:
        return True
    if not shot.dialogue:
        return not speech_blocks(shot.prompt_sections.detailed_description)
    record = shot.meta.get("prompt_dialogue_contract") or {}
    try:
        lines = [DialogueLine.model_validate(x) for x in record.get("lines", [])]
        verify_dialogue_sources(project, shot, lines)
        draft = DialoguePromptDraft(prompt_sections=shot.prompt_sections,
                                    dialogue_uses=record.get("uses", []),
                                    dialogue_conflicts=record.get("conflicts", []))
        validate_dialogue_uses(draft, lines)
        return record.get("signature") == prompt_dialogue_record(project, shot, lines, draft)["signature"]
    except (ValueError, TypeError, KeyError):
        return False


def require_current_dialogue_contract(project, shot):
    if not dialogue_contract_current(project, shot):
        raise DialogueContractError([DialogueIssue(code="dialogue_contract_stale",
            action="Refresh attribution and the prompt against the current source before submitting.")])
