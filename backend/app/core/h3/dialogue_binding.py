"""Bind existing speech blocks to source lines without templating creative prose."""
from __future__ import annotations

import html
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..projects.dialogue import DialogueContractError, DialogueIssue, DialogueLine
from ..projects.models import PromptSections
from .prompt import _dialogue_text


class DialogueUse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_ids: list[str] = Field(min_length=1)
    speaker_id: str
    block_indexes: list[int] = Field(min_length=1)


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
    dialogue_uses: list[DialogueUse]
    dialogue_conflicts: list[DialogueConflict] = Field(default_factory=list)


def speech_blocks(text: str):
    return list(re.finditer(r"<d>(.*?)</d>", text, re.DOTALL))


def block_words(block) -> str:
    content = block.group(1).strip()
    match = re.fullmatch(r"\[[^\[\]\n]+\]\s*(.+)", content, re.DOTALL)
    if match is None:
        raise DialogueContractError([DialogueIssue(code="dialogue_block_invalid",
            action="Use the H3 <d>[Language] words</d> protocol.")])
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
        actual = _dialogue_text(" ".join(block_words(blocks[i]) for i in use.block_indexes))
        if expected != actual:
            issues.append(DialogueIssue(code="dialogue_block_mismatch", line_id=use.line_ids[0],
                expected=expected, actual=actual, action="Rebind the exact current speech occurrences."))
        used_ids.extend(use.line_ids)
        used_blocks.extend(use.block_indexes)
    if used_ids != [line.line_id for line in lines] or used_blocks != list(range(len(blocks))):
        issues.append(DialogueIssue(code="dialogue_coverage_mismatch",
            action="Cover every line and speech block once in order. Split blocks only when speakers differ."))
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
    labels = {}
    for use in draft.dialogue_uses:
        line = by_id[use.line_ids[0]]
        label = f"({html.escape(line.speaker_id)}: {html.escape(line.speaker_name)}) "
        labels.update({index: label for index in use.block_indexes})
    detail = draft.prompt_sections.detailed_description
    for index, block in reversed(list(enumerate(speech_blocks(detail)))):
        label = labels[index]
        if not detail[:block.start()].endswith(label):
            detail = detail[:block.start()] + label + detail[block.start():]
    return draft.prompt_sections.model_copy(update={"detailed_description": detail})
