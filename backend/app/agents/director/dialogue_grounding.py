"""Recover attribution from source evidence, without modifying authored state."""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from ...core.projects.dialogue import (DialogueContractError, DialogueIssue, DialogueLine,
    missing_dialogue_source_issue, verify_dialogue_sources)
from .planner import _extract_json_payload
from .brief import directing_requests


GROUNDING_INSTRUCTIONS = """Extract the current shot's dialogue attribution, not a new screenplay.
Return JSON {"dialogue_lines": [...], "issues": []}. Each line contains line_id,
speaker_id, speaker_name, text, language, source:{kind:"script",source_hash,scene_id,quote,occurrence}.
Preserve exact words, punctuation and order. IDs identify narrative people and line occurrences,
not Picture/Voice assets. Reuse existing narrative IDs when supplied; distinguish same-name people.
quote is an exact source excerpt including the speaker cue and this line. occurrence is its
zero-based occurrence in the script. Include enough scene context to disambiguate repetitions.
Use the supplied script hash and scene ID. Never infer a speaker from available voices.
Do not invent evidence or user approval. If attribution is genuinely ambiguous or absent,
return issues:[{code:"dialogue_source_unresolved",line_id,evidence,action}] and no guessed lines.
Language/accent requirements guide delivery; do not rewrite dialogue to simulate an accent.
"""


class GroundingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dialogue_lines: list[DialogueLine] = Field(default_factory=list)
    issues: list[DialogueIssue] = Field(default_factory=list)


async def ground_dialogue(project, shot, provider) -> list[DialogueLine]:
    if not shot.dialogue:
        return []
    if shot.dialogue_lines is not None:
        verify_dialogue_sources(project, shot, shot.dialogue_lines)
        return shot.dialogue_lines
    if not project.script_text.strip():
        raise DialogueContractError([missing_dialogue_source_issue(shot)])
    request = {"script": project.script_text,
        "script_hash": hashlib.sha256(project.script_text.encode()).hexdigest(),
        "scene_id": shot.scene_id, "shot_id": shot.id, "script_beat": shot.script_beat,
        "dialogue": shot.dialogue, "directing_requests": directing_requests(project)}
    correction = ""
    for attempt in range(2):
        raw = await provider.complete(GROUNDING_INSTRUCTIONS, json.dumps(request, ensure_ascii=False) + correction)
        try:
            response = GroundingResponse.model_validate(_extract_json_payload(raw))
        except ValueError as exc:
            error = DialogueContractError([DialogueIssue(code="dialogue_grounding_structure",
                expected="JSON with dialogue_lines and issues matching the grounding schema", actual=str(exc),
                action="Return the complete corrected grounding object using only supplied script evidence.")])
        else:
            if response.issues:
                raise DialogueContractError(response.issues)
            lines = response.dialogue_lines
            if any(line.source.kind != "script" for line in lines):
                raise DialogueContractError([DialogueIssue(code="dialogue_source_unresolved",
                    expected="script evidence", actual="model-authored revision",
                    action="Grounding cannot authorize a new authored revision.")])
            try:
                verify_dialogue_sources(project, shot, lines)
            except DialogueContractError as exc:
                error = exc
            else:
                return lines
        if attempt:
            raise error
        correction = (f"\nCorrect this grounding response once using the concrete validation feedback: {error}"
                      f"\nPrevious response: {raw}\nDo not invent evidence or user approval. "
                      "Return issues if the supplied source cannot resolve attribution.")
