"""Serialize source-backed speech while leaving surrounding creative prose free."""
from __future__ import annotations

import html
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..projects.dialogue import DialogueContractError, DialogueIssue, DialogueLine
from ..projects.models import PromptSections
from .prompt import _dialogue_text, dialogue_timeline_matches


class DialogueUse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_ids: list[str] = Field(min_length=1)
    speaker_id: str
    block_indexes: list[int] = Field(min_length=1)
    # When a source line crosses cuts, one exact Unicode character span per block.
    source_spans: list[tuple[int, int]] | None = None


class DialogueConflict(BaseModel):
    """A model finding, not a deterministic interpretation of natural prose."""
    model_config = ConfigDict(extra="forbid")
    line_id: str
    actual_speaker_id: str
    quote: str
    confidence: Literal["clear", "uncertain"]


class DialoguePromptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_sections: PromptSections
    dialogue_uses: list[DialogueUse] = Field(default_factory=list)
    dialogue_conflicts: list[DialogueConflict] = Field(default_factory=list)


def speech_blocks(text: str):
    return list(re.finditer(r"<d>(.*?)</d>", text, re.DOTALL))


def block_words(block, field="detailed_description") -> str:
    content = block.group(1).strip()
    match = re.fullmatch(r"\[[^\[\]\n]+\]\s*(.+)", content, re.DOTALL)
    if match is None:
        raise DialogueContractError([DialogueIssue(code="dialogue_block_invalid",
            expected=f"{field}: <d>[Language] exact source words</d>", actual=block.group(0),
            action=f"Repair {field} using the H3 <d>[Language] words</d> protocol, "
                   "or replace this occurrence with {{speech:line_id}} using the supplied source ID.")])
    return _dialogue_text(re.sub(r"<scenetrans>|<cutoff>", "", match.group(1)))


def validate_dialogue_uses(draft: DialoguePromptDraft, lines: list[DialogueLine]) -> None:
    blocks = speech_blocks(draft.prompt_sections.detailed_description)
    by_id = {line.line_id: line for line in lines}
    used_ids, used_blocks, issues = [], [], []
    for use in draft.dialogue_uses:
        selected = [by_id[id] for id in use.line_ids if id in by_id]
        if len(selected) != len(use.line_ids):
            issues.append(DialogueIssue(code="dialogue_unknown_line", actual=str(use.line_ids)))
            continue
        for line in selected:
            if line.speaker_id != use.speaker_id:
                issues.append(DialogueIssue(code="dialogue_speaker_mismatch", line_id=line.line_id,
                    expected=line.speaker_id, actual=use.speaker_id, evidence=line.source.quote,
                    action="Repair the speaker binding and conflicting prose, not the authored line."))
        if any(index < 0 or index >= len(blocks) for index in use.block_indexes):
            issues.append(DialogueIssue(code="dialogue_unknown_block", actual=str(use.block_indexes)))
            continue
        expected = _dialogue_text(" ".join(line.text for line in selected))
        actual_words = [block_words(blocks[i], f"detailed_description speech block {i}")
                        for i in use.block_indexes]
        actual = _dialogue_text(" ".join(actual_words))
        matches_words = dialogue_timeline_matches(actual_words, expected)
        if use.source_spans is not None:
            # Compare each slice independently: no language-specific joining heuristic.
            spans = use.source_spans
            text = selected[0].text
            cursor = 0
            valid_spans = len(selected) == 1 and len(spans) == len(use.block_indexes)
            for start, end in spans:
                valid_spans = valid_spans and start == cursor and start < end <= len(text)
                cursor = end
            valid_spans = valid_spans and cursor == len(text)
            if not valid_spans:
                issues.append(DialogueIssue(code="dialogue_span_invalid", line_id=use.line_ids[0],
                    expected=f"ordered contiguous spans covering [0, {len(text)}) once, one per speech block",
                    actual=str(spans), action="Correct the explicit source spans; preserve every authored character."))
            else:
                expected_words = [_dialogue_text(text[start:end]) for start, end in spans]
                matches_words = actual_words == expected_words
                expected, actual = str(expected_words), str(actual_words)
        if not matches_words:
            issues.append(DialogueIssue(code="dialogue_block_mismatch", line_id=use.line_ids[0],
                expected=expected, actual=actual, action="Rebind the exact current speech occurrences."))
        used_ids.extend(use.line_ids)
        used_blocks.extend(use.block_indexes)
    if used_ids != [line.line_id for line in lines] or used_blocks != list(range(len(blocks))):
        issues.append(DialogueIssue(code="dialogue_coverage_mismatch",
            expected=f"lines {[line.line_id for line in lines]}; blocks {list(range(len(blocks)))}",
            actual=f"lines {used_ids}; blocks {used_blocks}",
            action="Cover every line and speech block once in order; use explicit source spans for split lines."))
    for conflict in draft.dialogue_conflicts:
        line = by_id.get(conflict.line_id)
        indexes = [i for use in draft.dialogue_uses if conflict.line_id in use.line_ids
                   for i in use.block_indexes if 0 <= i < len(blocks)]
        if (line and conflict.confidence == "clear" and conflict.actual_speaker_id != line.speaker_id
                and conflict.quote and conflict.quote in draft.prompt_sections.detailed_description
                and any(blocks[i].group(0) in conflict.quote for i in indexes)):
            issues.append(DialogueIssue(code="dialogue_prose_conflict", line_id=line.line_id,
                expected=line.speaker_id, actual=conflict.actual_speaker_id, evidence=conflict.quote,
                action="Repair the cited attribution conflict while preserving creative direction."))
    if issues:
        raise DialogueContractError(issues)


def annotate_speakers(draft: DialoguePromptDraft, lines: list[DialogueLine]) -> PromptSections:
    validate_dialogue_uses(draft, lines)
    by_id = {line.line_id: line for line in lines}
    # Names immediately before speech can be rendered as extra spoken words.
    # Keep source IDs in DialogueUse; H3 gets neutral speaker handles instead.
    speakers = {line.speaker_id: line for line in reversed(lines)}
    order = list(dict.fromkeys(line.speaker_id for line in lines))
    aliases = {speaker_id: f"S{index + 1}" for index, speaker_id in enumerate(order)}
    labels = {}
    legacy_labels = {}
    for use in draft.dialogue_uses:
        line = by_id[use.line_ids[0]]
        label = f"({aliases[line.speaker_id]}) "
        labels.update({index: label for index in use.block_indexes})
        legacy = f"({html.escape(line.speaker_id)}: {html.escape(line.speaker_name)}) "
        legacy_labels.update({index: legacy for index in use.block_indexes})
    detail = draft.prompt_sections.detailed_description
    for index, block in reversed(list(enumerate(speech_blocks(detail)))):
        label = labels[index]
        prefix = detail[:block.start()]
        legacy = legacy_labels[index]
        if prefix.endswith(legacy):
            prefix = prefix[:-len(legacy)]
        # A revised dialogue can change speaker order and hence handle numbers.
        # Replace the protocol cue rather than stacking the new handle after it.
        prefix = re.sub(r"\(S[1-9][0-9]*\) $", "", prefix)
        if not prefix.endswith(label):
            prefix += label
        detail = prefix + detail[block.start():]
    subject = draft.prompt_sections.subject_definitions
    if order:
        binding_header = "\nSpeaker identities: "
        # Replace our binding line without discarding subsequently added direction.
        subject = re.sub(r"\nSpeaker identities: [^\r\n]*", "", subject)
        subject += binding_header + "; ".join(
            f"{aliases[speaker_id]} is {html.escape(speakers[speaker_id].speaker_name)}"
            for speaker_id in order) + "."
    return draft.prompt_sections.model_copy(update={
        "detailed_description": detail, "subject_definitions": subject})


_SPEECH_REFERENCE = re.compile(r"\{\{speech:([^{}:\r\n]+)(?::([0-9]+):([0-9]+))?\}\}")


def compile_dialogue_draft(draft: DialoguePromptDraft, lines: list[DialogueLine]) -> DialoguePromptDraft:
    """Pure writer-to-H3 boundary; return canonical sections plus persistence metadata.

    Whole-line references are ``{{speech:line_id}}``. Optional ``:start:end``
    offsets address Unicode characters, end-exclusive, and must cover the whole
    line contiguously. Legacy finalized blocks retain their existing validation.
    """
    detail = draft.prompt_sections.detailed_description
    def reject(code, expected, actual, action, line_id=None):
        raise DialogueContractError([DialogueIssue(code=code, line_id=line_id,
            expected=expected, actual=str(actual), action=action)])

    for field in PromptSections.model_fields:
        value = getattr(draft.prompt_sections, field)
        if field != "detailed_description" and "{{speech" in value:
            reject("dialogue_placeholder_misplaced", "speech references only in detailed_description",
                   f"{field}: {value}", "Move the speech reference to its timeline position in detailed_description.")
    if "{{speech" not in detail:
        return draft.model_copy(update={"prompt_sections": annotate_speakers(draft, lines)})

    matches = list(_SPEECH_REFERENCE.finditer(detail))
    if "{{speech" in _SPEECH_REFERENCE.sub("", detail):
        reject("dialogue_placeholder_invalid", "detailed_description: {{speech:line_id}} or {{speech:line_id:start:end}}",
               detail, "Close each speech reference and use a supplied source ID with optional integer character offsets.")
    if re.search(r"</?d(?:>|\s)", detail):
        reject("dialogue_mixed_format", "only speech references in this draft's detailed_description", detail,
               "Replace every manually written <d> block with its source speech reference, or submit a fully bound legacy draft.")
    ids = [line.line_id for line in lines]
    if len(set(ids)) != len(ids):
        reject("duplicate_line_id", "distinct source line IDs, including repeated words", ids,
               "Resolve a distinct source occurrence for every line.")
    by_id = {line.line_id: line for line in lines}
    uses: list[DialogueUse] = []
    pieces, cursor, replacements = [], 0, {}
    for index, match in enumerate(matches):
        line_id, start_raw, end_raw = match.groups()
        line = by_id.get(line_id)
        if line is None:
            reject("dialogue_unknown_line", f"one of the supplied IDs {ids}", line_id,
                   "Use the supplied source line ID; do not invent new dialogue.", line_id)
        language = line.language.strip()
        if not language:
            reject("dialogue_language_missing", "a source-backed nonempty DialogueLine.language", repr(line.language),
                   "Resolve the language from the source or explicit directing requirements before compiling speech.", line_id)
        if re.search(r"[\[\]\r\n<>]", language):
            reject("dialogue_language_invalid", "a plain language label without H3 delimiters", language,
                   "Provide the source language as a label, without speech markup.", line_id)
        start, end = (int(start_raw), int(end_raw)) if start_raw is not None else (0, len(line.text))
        if not 0 <= start < end <= len(line.text):
            reject("dialogue_span_invalid", f"a nonempty source span within [0, {len(line.text)})", [start, end],
                   "Use zero-based Unicode character offsets with an exclusive end.", line_id)
        if start_raw is not None and uses and uses[-1].line_ids == [line_id] and uses[-1].source_spans is not None:
            use = uses[-1]
            use.block_indexes.append(index)
            use.source_spans.append((start, end))
        else:
            uses.append(DialogueUse(line_ids=[line_id], speaker_id=line.speaker_id,
                block_indexes=[index], source_spans=[(start, end)] if start_raw is not None else None))
        block = f"<d>[{language}] {line.text[start:end]}</d>"
        replacements[match.group(0)] = block
        pieces.extend((detail[cursor:match.start()], block))
        cursor = match.end()
    pieces.append(detail[cursor:])
    sections = draft.prompt_sections.model_copy(update={"detailed_description": "".join(pieces)})
    # Preserve model findings as evidence after converting their quoted references.
    conflicts = [conflict.model_copy(update={"quote": _SPEECH_REFERENCE.sub(
        lambda match: replacements.get(match.group(0), match.group(0)), conflict.quote)})
        for conflict in draft.dialogue_conflicts]
    compiled = draft.model_copy(update={"prompt_sections": sections, "dialogue_uses": uses,
                                        "dialogue_conflicts": conflicts})
    if draft.dialogue_uses:
        # Supplied metadata is a claim to validate, never silently replace a bad speaker.
        validate_dialogue_uses(compiled.model_copy(update={"dialogue_uses": draft.dialogue_uses}), lines)
    return compiled.model_copy(update={"prompt_sections": annotate_speakers(compiled, lines)})
