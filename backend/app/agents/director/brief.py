"""Authored directing requests survive storyboard retries and context rebuilds."""
from __future__ import annotations

import hashlib
import math
import re
import uuid

from ...core.projects.models import AgentContext
from ...core.projects.store import load_project
from .context_io import load_agent_context, save_agent_context


def _authored_request_text(message: str) -> str:
    """Discard the generated song-map appendix from older chat turns."""
    focus_marker = "\n\nSelected song segment IDs: "
    head, separator, appendix = message.partition(focus_marker)
    if separator and appendix.lstrip().startswith("["):
        return head.strip()
    marker = "\n\nComplete saved song segmentation:"
    head, separator, appendix = message.partition(marker)
    if separator and "\nAll saved song segments:\n[" in appendix:
        return head.strip()
    return message.strip()


def requested_minimum_duration_s(text: str) -> float:
    number = r"(\d+(?:\.\d+)?)"
    unit = r"(seconds?|secs?|s|minutes?|mins?|min|分钟|秒)"
    patterns = (
        rf"(?:at\s+least|minimum(?:\s+duration)?(?:\s+of)?|no\s+less\s+than)\s+{number}\s*{unit}\b",
        rf"{number}\s*{unit}\s*(?:minimum|at\s+minimum)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            value, unit_value = float(match.group(1)), match.group(2).lower()
            return value * 60 if unit_value.startswith("m") or unit_value == "分钟" else value
    range_pattern = rf"{number}\s*[-–—至]\s*\d+(?:\.\d+)?\s*{unit}(?:\b|(?=的))"
    for match in re.finditer(range_pattern, text or "", re.IGNORECASE):
        before, after = text[max(0, match.start() - 60):match.start()], text[match.end():match.end() + 70]
        # A story character waiting for minutes is not a requested film runtime.
        if (not before.strip() and not after.strip()) or re.search(
            r"(?:runtime|duration|length|total|时长|片长|全片|影片|视频)[^.!?。！？]*$", before, re.IGNORECASE
        ) or re.match(r"(?:\s+[\w-]+){0,6}\s+(?:film|video|short)\b|的(?:影片|视频|短片)", after, re.IGNORECASE):
            value, unit_value = float(match.group(1)), match.group(2).lower()
            return value * 60 if unit_value.startswith("m") or unit_value == "分钟" else value
    clock = re.search(r"(?:total\s+(?:runtime|duration)|runtime|总时长|时长)\s*[:：]?\s*(\d+):([0-5]\d)\s*[-–—至]\s*\d+:[0-5]\d", text or "", re.IGNORECASE)
    return int(clock.group(1)) * 60 + int(clock.group(2)) if clock else 0.0


def directing_request_sources(project) -> list[dict]:
    """Original authored requests, ordered by receipt; never current confirmations."""
    context = load_agent_context(project.id)
    record = (context.extra if context else {}).get("directing_brief", {})
    script_hash = hashlib.sha256(project.script_text.encode()).hexdigest()
    entries = record.get("sources")
    if entries is None:
        # Preserve legacy requests and their original script version during migration.
        entries = [dict(id=f"request-{index}-{hashlib.sha256(text.encode()).hexdigest()[:12]}",
                        source_message_id=None, text=text, script_hash=record.get("script_hash"))
                   for index, text in enumerate(record.get("messages", []))]
    return [{**entry, "text": _authored_request_text(entry.get("text") or ""),
             "kind": "user_directing_request",
             "script_current": entry.get("script_hash") == script_hash} for entry in entries]


def directing_requests(project) -> list[str]:
    return [source["text"] for source in directing_request_sources(project)]


SHOT_EXECUTION_INTENT = """
The saved shot is the execution target, not a proposal for storyboard approval.
The project script is background context; an appended or revised shot need not replay its
characters, location or action. Do not demand a rollback to the script just because they differ.
intent.authoring_request is the verified user request associated with this shot's authoring,
not an instruction to undo later saved edits. intent.directing_requests are ordered historical
requirements: apply only those relevant to this shot, respecting later replacements and scope.
intent.current_request is this prompt-writing request, not blanket permission to edit the story.
Apply the latest explicit shot-specific directing requirement when an older saved camera or
composition field contradicts it. Do not silently let a stale descriptive field cancel a newer
action in script_beat or the current request. Keep all six prompt sections consistent with the
resolved action and camera sequence; an initial Layout pose does not lock the camera for the clip.
Preserve the current saved brief, dialogue and duration. Use references according to their
assigned contribution: identity/design evidence need not share the desired rendering style or
depict the intended action. Ground actual visible facts honestly. Ask only if an essential
current requirement and a specific reference cannot be reconciled without a user choice.
"""


def shot_execution_intent(project, shot, current_request: str = "") -> dict:
    """Recover a shot-local authoring source without turning arbitrary chat into approval."""
    from ...core.projects.chat_history import load_chat_history
    # Existing authoring transactions already retain host-owned message identity here.
    # It is provenance, not a substitute for the current authored shot fields.
    evidence = (shot.meta or {}).get("dialogue_authoring") or {}
    authoring_request = None
    if isinstance(evidence, dict) and evidence.get("user_message_id"):
        message = next((m for m in load_chat_history(project.id)
                        if m.role == "user" and m.id == evidence["user_message_id"]
                        and m.content == evidence.get("user_message")), None)
        if message is not None:
            authoring_request = {"source_message_id": message.id, "text": message.content}
    return {"authoring_request": authoring_request,
            "directing_requests": directing_request_sources(project),
            "current_request": current_request}


def remember_directing_request(project_id: str, message: str) -> None:
    message = _authored_request_text(message)
    if not message.strip():
        return
    project = load_project(project_id)
    if project is None:
        return
    from ...core.projects.chat_history import load_chat_history
    sources = directing_request_sources(project)
    source_message = next((item for item in reversed(load_chat_history(project_id))
                           if item.role == "user" and item.content == message), None)
    source_message_id = source_message.id if source_message else None
    if source_message_id and any(source.get("source_message_id") == source_message_id for source in sources):
        return
    if not source_message_id and sources and sources[-1]["text"] == message:
        return
    context = load_agent_context(project_id) or AgentContext(project_id=project_id, script_hash="")
    sources.append(dict(id=source_message_id or f"request-{uuid.uuid4().hex}",
                        source_message_id=source_message_id, text=message,
                        script_hash=hashlib.sha256(project.script_text.encode()).hexdigest()))
    record = {"sources": [{key: value for key, value in source.items()
                            if key not in {"kind", "script_current"}} for source in sources]}
    save_agent_context(project_id, context.model_copy(update={"extra": {**context.extra, "directing_brief": record}}))


def minimum_duration(project, current_request: str = "") -> float:
    # An explicit newer runtime replaces an older one; unrelated follow-ups do not erase it.
    for text in reversed([project.script_text, *directing_requests(project), current_request]):
        minimum = requested_minimum_duration_s(text)
        if minimum:
            return minimum
    return 0.0


def duration_issues(project, shots) -> list[str]:
    if project.mode.value == "mv":
        return []
    required = minimum_duration(project)
    total = sum(shot.duration_s for shot in shots)
    issues = [f"Storyboard total duration {total:g}s is below the requested minimum {required:g}s"] if total + 1e-9 < required else []
    for shot in shots:
        try:
            validate_shot_duration(shot.duration_s)
        except ValueError as exc:
            issues.append(f"Shot {shot.id}: {exc}")
    return issues


def validate_shot_duration(seconds: float) -> None:
    from ...core.h3.frames import frames_for_seconds
    if not math.isfinite(seconds):
        raise ValueError("Shot duration must be finite")
    try:
        frames_for_seconds(seconds)
    except ValueError as exc:
        raise ValueError(f"Shot duration {seconds:g}s is unsupported by local H3; choose a positive duration up to 15 seconds. {exc}") from exc


def duration_budget(project, shots) -> dict[str, float]:
    total = sum(shot.duration_s for shot in shots)
    required = minimum_duration(project) if project.mode.value != "mv" else 0.0
    return {"total_s": total, "required_minimum_s": required,
            "deficit_s": max(0.0, required - total)}
