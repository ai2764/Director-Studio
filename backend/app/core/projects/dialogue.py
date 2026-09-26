"""Source-backed dialogue records, independent of creative direction and assets."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..prompt_errors import PromptFailureError


class DialogueSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["script", "shot_revision", "user_message"]
    source_hash: str = Field(min_length=1)
    scene_id: str
    quote: str = Field(min_length=1)
    occurrence: int = Field(default=0, ge=0)
    # Keep legacy script/revision serialization (and its signatures) unchanged.
    source_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    shot_hash: str | None = Field(default=None, exclude_if=lambda value: value is None)


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    speaker_name: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: str = ""
    source: DialogueSource


class DialogueLanguageUpdate(BaseModel):
    """Change a saved line's language without resubmitting its spoken words."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    line_id: str = Field(min_length=1)
    language: str = Field(min_length=1)


class DialogueIssue(BaseModel):
    code: str
    line_id: str | None = None
    expected: str = ""
    actual: str = ""
    evidence: str = ""
    action: str = ""


class DialogueContractError(PromptFailureError):
    def __init__(self, issues: list[DialogueIssue]):
        self.issues = issues
        descriptions = []
        for issue in issues:
            details = []
            if issue.expected:
                details.append(f"expected {issue.expected!r}")
            if issue.actual:
                details.append(f"got {issue.actual!r}")
            description = f"{issue.code} ({issue.line_id or 'dialogue'})"
            if details:
                description += ": " + ", ".join(details)
            if issue.action:
                description += ". " + issue.action
            descriptions.append(description)
        super().__init__("contract", "; ".join(descriptions))


_H3_PROTOCOL_TAG = re.compile(
    r"<\s*/?\s*(?:d|scenetrans|cutoff)\s*>|<\s*(?:Picture|Audio|Subject)\s+\d+\s*>",
    re.IGNORECASE,
)


def validate_authored_dialogue(dialogue: list[str] | None = None,
                               lines: list[DialogueLine] | None = None) -> None:
    """Keep generation protocol tags out of newly authored spoken words."""
    issues = []
    entries = [(f"dialogue[{index}]", text) for index, text in enumerate(dialogue or [])]
    entries.extend((line.line_id, line.text) for line in lines or [])
    for line_id, text in entries:
        match = _H3_PROTOCOL_TAG.search(text)
        if match:
            issues.append(DialogueIssue(
                code="dialogue_protocol_markup", line_id=line_id,
                expected="raw spoken words", actual=match.group(0),
                action="Remove H3 prompt markup from authored dialogue; keep only the spoken words.",
            ))
    if issues:
        raise DialogueContractError(issues)


def project_dialogue(lines: list[DialogueLine]) -> list[str]:
    return [line.text for line in lines]


def validate_dialogue_projection(dialogue: list[str], lines: list[DialogueLine]) -> None:
    # Import lazily: prompt.py uses the project's PromptSections type.
    from ..h3.prompt import _dialogue_text, _spoken_dialogue_text
    ids = [line.line_id for line in lines]
    if len(set(ids)) != len(ids):
        raise DialogueContractError([DialogueIssue(code="duplicate_line_id",
            action="Use a distinct ID for every line occurrence, including repetitions.")])
    expected = [_dialogue_text(line.text) for line in lines]
    if expected == [_dialogue_text(text) for text in dialogue]:
        return
    actual = [_spoken_dialogue_text(text) for text in dialogue]
    if expected != actual:
        raise DialogueContractError([DialogueIssue(code="dialogue_projection_mismatch",
            expected=str(expected), actual=str(actual),
            action="Submit matching dialogue and dialogue_lines, or update only one representation.")])


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def revision_digest(scene_id: str, lines: list[DialogueLine]) -> str:
    return digest([scene_id, [line.model_dump(exclude={"source"}) for line in lines]])


def dialogue_evidence_version(shot) -> str:
    """Bind recovered evidence to the unchanged legacy beat, not just its words."""
    return digest([shot.scene_id, shot.script_beat, shot.dialogue])


def dialogue_beat_text(text: str) -> str:
    """Compare authored beats across quote/formatting changes, never keywords."""
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def apply_dialogue_update(shot, updates: dict):
    """Apply an authored mutation; derived grounding never calls this function."""
    from .models import Shot
    updates = dict(updates)
    if "dialogue_language_updates" in updates and updates["dialogue_language_updates"] is None:
        raise ValueError("dialogue_language_updates must be a nonempty list")
    language_updates = updates.pop("dialogue_language_updates", None)
    if language_updates is not None:
        if "dialogue" in updates or "dialogue_lines" in updates:
            raise ValueError("dialogue_language_updates cannot be combined with dialogue or dialogue_lines")
        if updates.get("scene_id", shot.scene_id) != shot.scene_id:
            raise ValueError("dialogue_language_updates cannot change scene_id or source provenance")
        if not shot.dialogue_lines:
            raise ValueError("dialogue_language_updates requires saved attributed dialogue_lines")
        patches = [DialogueLanguageUpdate.model_validate(item) for item in language_updates]
        if not patches:
            raise ValueError("dialogue_language_updates must contain at least one line")
        by_id = {patch.line_id: patch.language for patch in patches}
        if len(by_id) != len(patches):
            raise ValueError("dialogue_language_updates contains duplicate line_id values")
        unknown_ids = sorted(set(by_id) - {line.line_id for line in shot.dialogue_lines})
        if unknown_ids:
            raise ValueError("dialogue_language_updates contains unknown line_id values: "
                             + ", ".join(unknown_ids))
        lines = [line.model_copy(update={"language": by_id.get(line.line_id, line.language)})
                 for line in shot.dialogue_lines]
        if lines != shot.dialogue_lines and any(line.source.kind == "shot_revision" for line in lines):
            version = revision_digest(shot.scene_id, lines)
            lines = [line.model_copy(update={"source": line.source.model_copy(
                update={"source_hash": version})}) if line.source.kind == "shot_revision"
                else line for line in lines]
        updates["dialogue_lines"] = lines
        updates["dialogue"] = list(shot.dialogue)
    if "dialogue" in updates:
        if language_updates is None:
            validate_authored_dialogue(updates["dialogue"])
    if updates.get("dialogue_lines") is not None:
        if language_updates is None:
            validate_authored_dialogue(lines=[DialogueLine.model_validate(x)
                                              for x in updates["dialogue_lines"]])
    if ("dialogue_lines" not in updates and "dialogue" not in updates
            and updates.get("scene_id", shot.scene_id) != shot.scene_id
            and shot.dialogue_lines is not None
            and all(line.source.kind == "shot_revision" for line in shot.dialogue_lines)):
        updates["dialogue_lines"] = shot.dialogue_lines
    if updates.get("dialogue_lines") is not None:
        lines = [DialogueLine.model_validate(x) for x in updates["dialogue_lines"]]
        if "dialogue" in updates:
            validate_dialogue_projection(updates["dialogue"], lines)
        else:
            updates["dialogue"] = project_dialogue(lines)
        # An unchanged dashboard round-trip is not a new authored revision.
        if language_updates is None and (lines != shot.dialogue_lines
                                         or updates.get("scene_id", shot.scene_id) != shot.scene_id):
            scene_id = updates.get("scene_id", shot.scene_id)
            version = revision_digest(scene_id, lines)
            lines = [line.model_copy(update={"source": DialogueSource(kind="shot_revision",
                source_hash=version, scene_id=scene_id,
                quote=f"{line.speaker_name}: {line.text}", occurrence=i)}) for i, line in enumerate(lines)]
        updates["dialogue_lines"] = lines
    elif "dialogue" in updates and updates["dialogue"] != shot.dialogue:
        updates["dialogue_lines"] = None
    payload = {**shot.model_dump(mode="python"), **updates}
    changed = Shot.model_validate(payload)
    if (changed.dialogue != shot.dialogue or changed.dialogue_lines != shot.dialogue_lines
            or changed.scene_id != shot.scene_id):
        meta = dict(changed.meta)
        meta.pop("dialogue_authoring", None)
        changed = changed.model_copy(update={"meta": meta})
    if (changed.dialogue != shot.dialogue or changed.dialogue_lines != shot.dialogue_lines
            or changed.scene_id != shot.scene_id or changed.prompt_sections != shot.prompt_sections):
        meta = dict(changed.meta)
        for key in ("prompt_dialogue_signature", "prompt_dialogue_contract", "dialogue_grounding"):
            meta.pop(key, None)
        changed = changed.model_copy(update={"meta": meta})
    return changed


def verify_dialogue_sources(project, shot, lines: list[DialogueLine]) -> None:
    from ..h3.prompt import _dialogue_text
    validate_dialogue_projection(shot.dialogue, lines)
    authored_hash = revision_digest(shot.scene_id, lines)
    issues = []
    previous_source_end = {}
    messages = None
    for line in lines:
        source = line.source
        def reject(code, expected, actual, action):
            issues.append(DialogueIssue(code=code, line_id=line.line_id,
                expected=str(expected), actual=str(actual), evidence=source.quote, action=action))

        if source.kind == "script" and not project.script_text.strip():
            issues.append(missing_dialogue_source_issue(shot, line.line_id))
            continue
        if source.scene_id != shot.scene_id:
            reject("dialogue_source_scene_mismatch", shot.scene_id, source.scene_id,
                   "Resolve this line against the current shot scene; do not relabel unsupported evidence.")
            continue
        if source.kind == "shot_revision":
            if source.source_hash != authored_hash or lines != shot.dialogue_lines:
                reject("dialogue_source_stale", f"persisted authored revision {authored_hash}",
                       f"submitted revision {source.source_hash}; matches saved lines: {lines == shot.dialogue_lines}",
                       "Reload the current explicitly authored dialogue revision; do not create approval by grounding.")
            continue
        source_text = project.script_text
        source_key = (source.kind, source.source_id)
        if source.kind == "user_message":
            if messages is None:
                from .chat_history import load_chat_history
                messages = {m.id: m for m in load_chat_history(project.id) if m.role == "user"}
            message = messages.get(source.source_id)
            if (message is None or source.shot_hash != dialogue_evidence_version(shot)
                    or dialogue_beat_text(message.content) != dialogue_beat_text(shot.script_beat)):
                reject("dialogue_source_stale", "existing user message bound to the current shot beat",
                       source.source_id, "Recover evidence again; do not rewrite authored dialogue.")
                continue
            source_text = message.content
        source_hash = hashlib.sha256(source_text.encode()).hexdigest()
        if source.source_hash != source_hash:
            reject("dialogue_source_stale", source_hash, source.source_hash,
                   "Resolve the quote again from the current script version; do not merely replace its hash.")
            continue
        quotes = list(re.finditer(re.escape(source.quote), source_text))
        if not quotes:
            reject("dialogue_source_quote_mismatch", "verbatim excerpt in the selected source", source.quote,
                   "Copy an exact source excerpt including this line and its speaker cue.")
            continue
        if source.occurrence >= len(quotes):
            reject("dialogue_source_occurrence_mismatch", f"occurrence 0 through {len(quotes) - 1}", source.occurrence,
                   "Select the zero-based occurrence of this exact quote in the current script.")
            continue
        words = list(re.finditer(r"\s+".join(re.escape(x) for x in line.text.split()), source.quote))
        if len(words) != 1 or _dialogue_text(line.text) not in _dialogue_text(source.quote):
            reject("dialogue_source_text_mismatch", f"one occurrence of {line.text!r}", source.quote,
                   "Choose an exact source excerpt containing this dialogue once; preserve the shot's spoken words.")
            continue
        # A name mentioned *in* speech identifies neither its speaker nor a cue.
        # Cues may precede or follow dialogue; don't impose a screenplay syntax.
        cue = source.quote[:words[0].start()] + " " + source.quote[words[0].end():]
        if line.speaker_name.casefold() not in cue.casefold():
            reject("dialogue_source_speaker_mismatch", f"speaker cue for {line.speaker_name!r}", source.quote,
                   "Resolve the speaker from the source cue; request explicit attribution if ambiguous.")
            continue
        quote_start = quotes[source.occurrence].start()
        start, end = quote_start + words[0].start(), quote_start + words[0].end()
        previous_end = previous_source_end.get(source_key, -1)
        if start < previous_end:
            reject("dialogue_source_occurrence_mismatch", f"source offset at or after {previous_end}", start,
                   "Resolve distinct source occurrences in shot dialogue order; do not reuse an earlier occurrence.")
        previous_source_end[source_key] = max(previous_end, end)
    if issues:
        raise DialogueContractError(issues)


def missing_dialogue_source_issue(shot, line_id: str | None = None) -> DialogueIssue:
    return DialogueIssue(code="dialogue_source_missing", line_id=line_id,
        expected="a nonempty script or an explicitly authored dialogue revision",
        actual=f"empty project script for shot {shot.id}, scene {shot.scene_id}",
        evidence=json.dumps(shot.dialogue, ensure_ascii=False),
        action="Provide the script with speaker cues or explicit speaker-attributed dialogue for this shot. "
               "Existing shot text alone does not authorize inferred speakers; retry after the source is supplied.")


def dialogue_contract_signature(project, shot, lines, directing_requests) -> str:
    return digest({"version": 1, "script": project.script_text, "scene_id": shot.scene_id,
        "dialogue": shot.dialogue, "lines": [x.model_dump(mode="json") for x in lines],
        "directing_requests": directing_requests})
