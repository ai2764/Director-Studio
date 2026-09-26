"""Source-backed dialogue records, independent of creative direction and assets."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..prompt_errors import PromptFailureError


class DialogueSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["script", "shot_revision"]
    source_hash: str = Field(min_length=1)
    scene_id: str
    quote: str = Field(min_length=1)
    occurrence: int = Field(default=0, ge=0)


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    speaker_name: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: str = ""
    source: DialogueSource


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
        super().__init__("contract", "; ".join(
            f"{i.code} ({i.line_id or 'dialogue'}): expected {i.expected!r}, "
            f"got {i.actual!r}. {i.action}" for i in issues))


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


def apply_dialogue_update(shot, updates: dict):
    """Apply an authored mutation; derived grounding never calls this function."""
    from .models import Shot
    updates = dict(updates)
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
        if lines != shot.dialogue_lines or updates.get("scene_id", shot.scene_id) != shot.scene_id:
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
            or changed.scene_id != shot.scene_id or changed.prompt_sections != shot.prompt_sections):
        meta = dict(changed.meta)
        for key in ("prompt_dialogue_signature", "prompt_dialogue_contract", "dialogue_grounding"):
            meta.pop(key, None)
        changed = changed.model_copy(update={"meta": meta})
    return changed


def verify_dialogue_sources(project, shot, lines: list[DialogueLine]) -> None:
    from ..h3.prompt import _dialogue_text
    validate_dialogue_projection(shot.dialogue, lines)
    script_hash = hashlib.sha256(project.script_text.encode()).hexdigest()
    authored_hash = revision_digest(shot.scene_id, lines)
    issues = []
    previous_script_end = -1
    for line in lines:
        source = line.source
        valid = source.scene_id == shot.scene_id
        if source.kind == "shot_revision":
            valid = valid and source.source_hash == authored_hash and lines == shot.dialogue_lines
        else:
            quotes = list(re.finditer(re.escape(source.quote), project.script_text))
            words = list(re.finditer(r"\s+".join(re.escape(x) for x in line.text.split()), source.quote))
            valid = (valid and source.source_hash == script_hash
                and len(quotes) > source.occurrence and len(words) == 1
                and _dialogue_text(line.text) in _dialogue_text(source.quote)
                and line.speaker_name in source.quote)
            if valid:
                quote_start = quotes[source.occurrence].start()
                start, end = quote_start + words[0].start(), quote_start + words[0].end()
                valid = start >= previous_script_end
                previous_script_end = max(previous_script_end, end)
        if not valid:
            issues.append(DialogueIssue(code="dialogue_source_stale", line_id=line.line_id,
                evidence=source.quote, action="Resolve this occurrence from the current scene/source; do not guess."))
    if issues:
        raise DialogueContractError(issues)


def dialogue_contract_signature(project, shot, lines, directing_requests) -> str:
    return digest({"version": 1, "script": project.script_text, "scene_id": shot.scene_id,
        "dialogue": shot.dialogue, "lines": [x.model_dump(mode="json") for x in lines],
        "directing_requests": directing_requests})
