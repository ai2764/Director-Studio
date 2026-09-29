"""Shared Director dialogue preparation and prompt provenance checks."""
from __future__ import annotations

import json
from contextlib import contextmanager

from pydantic import ValidationError

from ...core.h3.prompt import validate_required_picture_bindings
from ...core.prompt_errors import PromptFailureError
from ...core.projects.models import ProjectMode, PromptSections
from ...core.projects.dialogue import (DialogueLine, DialogueContractError, DialogueIssue,
    dialogue_contract_signature, dialogue_evidence_version, verify_dialogue_sources, digest)
from ...core.h3.dialogue_binding import (DialoguePromptDraft,
    validate_dialogue_uses, speech_blocks)
from .brief import directing_requests
from .dialogue_grounding import ground_dialogue, review_dialogue_attribution
from .planner import _extract_json_payload, parse_prompt_sections_json


WRITER_CONTRACT = """
Director internal response contract (the final H3 prompt still has exactly six sections):
Return {"prompt_sections": {the six nonempty section strings}}.
Place {{speech:line_id}} in detailed_description where each source line is spoken.
The backend emits <d>[Language] exact source words</d> and attribution metadata; do not
write those blocks or dialogue_uses yourself. This internal response contract overrides
the final-H3 dialogue formatting instructions above. For revisions, replace existing
speech blocks with the corresponding source references. Cover every supplied line ID
once in source order, including distinct IDs for repeated words. To split one line across
cuts, use {{speech:line_id:start:end}} with zero-based
Unicode character offsets and an exclusive end. Those spans must be contiguous and cover
the entire source text once in order. Different speakers get their own references.
Keep prose, pacing, camera and acting choices free;
make prose consistent with attribution. Do not rewrite source words or reassign speakers.
Respect the current directing requirements, including each speaker's language/accent, without
changing dialogue to simulate accent. A missing Voice asset does not reassign dialogue.
These bindings are internal metadata and must not appear as extra H3 sections.
Self-check for prose that contradicts these bindings. Correct it before returning; if a clear
conflict remains, report optional dialogue_conflicts entries with line_id, actual_speaker_id,
an exact quote including the speech block, and confidence: clear or uncertain. Do not invent
findings merely because a character lacks a reference or because the staging is unusual.
"""


@contextmanager
def collect_prompt_contract_errors(raw: str, *, required_picture_indices=(),
                                   required_layout_indices=(), submitted_picture_indices=None):
    """Give one repair independent protocol defects without modifying the candidate.

    Complete sections remain readable when the dialogue envelope fails validation.
    Preserve the primary error and its typed issues; inspect only actual H3 sections
    for reference tags, never metadata or arbitrary prose outside the sections.
    """
    original = None
    try:
        yield
    except ValueError as exc:
        original = exc

    picture_errors = []
    try:
        payload = _extract_json_payload(raw)
        section_payload = payload.get("prompt_sections", payload) if isinstance(payload, dict) else None
        sections = parse_prompt_sections_json(json.dumps(section_payload))
        text = "\n".join(sections.values())
    except (ValueError, TypeError):
        text = None
    if text is not None:
        submitted = tuple(submitted_picture_indices) if submitted_picture_indices is not None else None
        for indices, label, allowed in (
            (required_picture_indices, "required Picture", submitted),
            (required_layout_indices, "selected Layout", None),
        ):
            try:
                validate_required_picture_bindings(text, indices,
                    submitted_picture_indices=allowed, binding_label=label)
            except PromptFailureError as exc:
                if original is None or str(exc) not in str(original):
                    picture_errors.append(exc)
    if not picture_errors:
        if original is not None:
            raise original
        return
    issues = list(getattr(original, "issues", []))
    if isinstance(original, ValidationError):
        issues.append(DialogueIssue(code="prompt_schema_invalid",
            expected="valid prompt envelope and dialogue metadata", actual=str(original),
            action="Repair the reported schema fields. For dialogue, use speech references "
                   "and omit dialogue_uses in the writer response."))
    issues.extend(DialogueIssue(code="picture_binding_invalid", actual=str(exc),
        expected="literal <Picture N> tags for the required submitted references",
        action="Correct reference tags in the prompt sections; preserve the creative prose.")
        for exc in picture_errors)
    error = PromptFailureError("contract", "; ".join(
        ([str(original)] if original is not None else []) + [str(exc) for exc in picture_errors]))
    error.issues = issues
    raise error from original


async def prepare_dialogue(project, shot, provider, *, revision_request=""):
    from .dialogue_metadata import (complete_dialogue_metadata, verify_prepared_dialogue,
        DialogueMetadataError, DialogueClarificationRequired, PreparedDialogue)
    if project.mode != ProjectMode.director:
        return None
    if not revision_request and shot.dialogue and dialogue_contract_current(project, shot):
        record = shot.meta["prompt_dialogue_contract"]
        lines = [DialogueLine.model_validate(x) for x in record["lines"]]
        return PreparedDialogue(lines, record["metadata"]) if record.get("metadata") else lines
    try:
        lines = await _prepare_attribution(project, shot, provider)
    except DialogueContractError as exc:
        unresolved = {"dialogue_source_missing", "dialogue_source_unresolved", "dialogue_speaker_unresolved"}
        error_type = (DialogueClarificationRequired if exc.issues and
                      all(issue.code in unresolved for issue in exc.issues) else DialogueMetadataError)
        raise error_type(exc.issues) from exc
    lines = await complete_dialogue_metadata(project, shot, lines, provider, revision_request=revision_request)
    if lines:
        verify_prepared_dialogue(project, shot, lines)
    return lines


async def _prepare_attribution(project, shot, provider):
    if project.mode != ProjectMode.director:
        return None
    if shot.dialogue_lines is not None:
        return await ground_dialogue(project, shot, provider)
    record = shot.meta.get("dialogue_grounding") or shot.meta.get("prompt_dialogue_contract") or {}
    if record.get("lines"):
        try:
            source_lines = (record.get("metadata") or {}).get("source_lines", record["lines"])
            lines = [DialogueLine.model_validate(x) for x in source_lines]
            source_beat = record.get("script_beat", shot.script_beat)
            verify_dialogue_sources(project, shot.model_copy(update={"script_beat": source_beat}), lines)
        except ValueError:
            pass
        else:
            if source_beat != shot.script_beat:
                from ...core.projects.chat_history import load_chat_history
                source_ids = {line.source.source_id for line in lines if line.source.kind == "user_message"}
                sources = [m.content for m in load_chat_history(project.id)
                           if m.role == "user" and m.id in source_ids]
                if any(line.source.kind == "script" for line in lines):
                    sources.append(project.script_text)
                # Re-check meaning after a beat edit, but never re-extract or
                # silently change established speaker IDs as a prompt repair.
                await review_dialogue_attribution(provider, sources, shot, lines)
                lines = [line.model_copy(update={"source": line.source.model_copy(update={
                    "shot_hash": dialogue_evidence_version(shot)})})
                    if line.source.kind == "user_message" else line for line in lines]
            verify_dialogue_sources(project, shot, lines)
            return lines
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
    metadata = getattr(lines, "metadata", None)
    signature = digest([source, shot.prompt_sections.model_dump(mode="json"), uses] +
                       ([metadata] if metadata else []))
    return {"lines": [x.model_dump(mode="json") for x in lines], "uses": uses, "signature": signature,
            **({"metadata": metadata} if metadata else {}),
            "conflicts": [x.model_dump(mode="json") for x in draft.dialogue_conflicts] if draft else []}


def dialogue_contract_current(project, shot) -> bool:
    from .dialogue_metadata import verify_prepared_dialogue, PreparedDialogue, metadata_input_signature
    if project.mode != ProjectMode.director:
        return True
    if not shot.dialogue:
        return not speech_blocks(shot.prompt_sections.detailed_description)
    record = shot.meta.get("prompt_dialogue_contract") or {}
    try:
        lines = [DialogueLine.model_validate(x) for x in record.get("lines", [])]
        metadata = record.get("metadata")
        if metadata:
            if metadata["input_signature"] != metadata_input_signature(
                    project, shot, metadata["source_lines"], metadata["revision_request"], metadata.get("previous_requests", [])):
                return False
            lines = PreparedDialogue(lines, metadata)
        verify_prepared_dialogue(project, shot, lines)
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
