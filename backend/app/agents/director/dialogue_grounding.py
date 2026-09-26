"""Recover attribution from source evidence, without modifying authored state."""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from ...core.projects.dialogue import (DialogueContractError, DialogueIssue, DialogueLine,
    dialogue_beat_text, dialogue_evidence_version, missing_dialogue_source_issue, verify_dialogue_sources)
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


class AttributionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid: bool
    issues: list[DialogueIssue] = Field(default_factory=list)


ATTRIBUTION_REVIEW = """Review recovered dialogue attribution against the original user-authored scene.
Return JSON {"valid":true,"issues":[]} only if every candidate line has the correct narrative
speaker, exact words and order in the supplied source and shot beat. Resolve pronouns and omitted
subjects from context; a name appearing anywhere in a paragraph does not prove that person speaks
every line. Distinguish addressing/mentioning a person from that person speaking. Do not impose a
new screenplay style or demand an explicit name on every sentence. This is source verification,
not creative direction. If attribution contradicts the source or is genuinely ambiguous, return
valid:false and issues with code:dialogue_speaker_unresolved, line_id, evidence and action.
"""


def _contains_dialogue(source: str, dialogue: list[str]) -> bool:
    from ...core.h3.prompt import _dialogue_text, _spoken_dialogue_text
    normalized = _dialogue_text(source)
    return all(_dialogue_text(text) in normalized or _spoken_dialogue_text(text) in normalized
               for text in dialogue)


async def ground_dialogue(project, shot, provider, *, known_speakers=None,
                         authoring_request: str = "", contextual_source: bool = False) -> list[DialogueLine]:
    if not shot.dialogue:
        return []
    if shot.dialogue_lines is not None:
        verify_dialogue_sources(project, shot, shot.dialogue_lines)
        return shot.dialogue_lines
    if not authoring_request and not _contains_dialogue(project.script_text, shot.dialogue):
        from ...core.projects.chat_history import load_chat_history
        candidates = {m.content: m for m in load_chat_history(project.id)
                      if m.role == "user" and _contains_dialogue(m.content, shot.dialogue)
                      and dialogue_beat_text(m.content) == dialogue_beat_text(shot.script_beat)}
        if len(candidates) == 1:
            message = next(iter(candidates.values()))
            # Reuse source extraction, but never promote its result to an authored
            # revision. The server attaches a verifiable project-local message ID.
            from .dialogue_authoring import known_speakers as collect_speakers
            lines = await ground_dialogue(project.model_copy(update={"script_text": message.content}),
                shot, provider, known_speakers=known_speakers or collect_speakers(project),
                authoring_request=message.content, contextual_source=True)
            lines = [line.model_copy(update={"source": line.source.model_copy(update={
                "kind": "user_message", "source_id": message.id,
                "shot_hash": dialogue_evidence_version(shot)})}) for line in lines]
            verify_dialogue_sources(project, shot, lines)
            return lines
        if project.script_text.strip() or candidates:
            raise DialogueContractError([DialogueIssue(code="dialogue_source_unresolved",
                expected="one unambiguous user-authored source containing the current dialogue",
                actual=f"{len(candidates)} matching sources; current script lacks these words",
                action="Supply or clarify the speaker-attributed source for this shot. "
                       "Do not keep retrying against the old script or alter its hash.")])
    if not project.script_text.strip():
        raise DialogueContractError([missing_dialogue_source_issue(shot)])
    request = {"script": project.script_text,
        "script_hash": hashlib.sha256(project.script_text.encode()).hexdigest(),
        "scene_id": shot.scene_id, "shot_id": shot.id, "script_beat": shot.script_beat,
        "dialogue": shot.dialogue, "directing_requests": directing_requests(project)}
    if known_speakers:
        request["known_speakers"] = known_speakers
    if authoring_request:
        request["authoring_request"] = authoring_request
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
            # Unique quote locations are a deterministic source fact, not a
            # creative/model choice. Repeated quotes still require disambiguation.
            lines = [line.model_copy(update={"source": line.source.model_copy(update={"occurrence": 0})})
                     if project.script_text.count(line.source.quote) == 1 else line for line in lines]
            if contextual_source:
                expanded = []
                for line in lines:
                    quote = line.source.quote
                    if project.script_text.count(quote) == 1:
                        start = project.script_text.index(quote)
                        left = project.script_text.rfind("\n\n", 0, start) + 2
                        left = 0 if left == 1 else left
                        right = project.script_text.find("\n\n", start + len(quote))
                        quote = project.script_text[left:right if right >= 0 else None]
                    expanded.append(line.model_copy(update={"source": line.source.model_copy(
                        update={"quote": quote, "occurrence": 0})}) if quote != line.source.quote else line)
                lines = expanded
            try:
                verify_dialogue_sources(project, shot, lines)
                if contextual_source:
                    audit_input = json.dumps({"source": project.script_text, "shot_beat": shot.script_beat,
                        "lines": [line.model_dump(mode="json") for line in lines]}, ensure_ascii=False)
                    bounded = getattr(provider, "complete_bounded", None)
                    audit_raw = (await bounded(ATTRIBUTION_REVIEW, audit_input, max_tokens=2048,
                                              schema=AttributionReview.model_json_schema())
                                 if bounded else await provider.complete(ATTRIBUTION_REVIEW, audit_input))
                    try:
                        audit = AttributionReview.model_validate(_extract_json_payload(audit_raw))
                    except ValueError as exc:
                        raise DialogueContractError([DialogueIssue(code="dialogue_attribution_review_invalid",
                            actual=str(exc), action="Return the structured source-attribution review.")]) from exc
                    if not audit.valid or audit.issues:
                        raise DialogueContractError(audit.issues or [DialogueIssue(
                            code="dialogue_speaker_unresolved", action="Resolve the speaker from source context.")])
            except DialogueContractError as exc:
                error = exc
            else:
                return lines
        if attempt:
            raise error
        correction = (f"\nCorrect this grounding response once using the concrete validation feedback: {error}"
                      f"\nPrevious response: {raw}\nDo not invent evidence or user approval. "
                      "Return issues if the supplied source cannot resolve attribution.")
