"""Complete dialogue attribution inside the explicit authoring transaction.

This is not prompt repair: only append/revise tools call it. Nothing is saved
until evidence resolution succeeds and the service validates the write.
"""
from __future__ import annotations

from ...core.projects.dialogue import (DialogueLine, DialogueContractError, DialogueIssue,
    digest, verify_dialogue_sources)
from ...core.projects.models import ProjectMode, Shot
from ...core.projects.store import list_shots
from .dialogue_grounding import ground_dialogue


def known_speakers(project) -> list[dict]:
    result = {}
    for shot in list_shots(project.id):
        try:
            lines = shot.dialogue_lines
            if lines is None:
                lines = [DialogueLine.model_validate(v) for v in
                         (shot.meta.get("dialogue_grounding") or
                          shot.meta.get("prompt_dialogue_contract") or {}).get("lines", [])]
            verify_dialogue_sources(project, shot, lines)
        except (ValueError, TypeError):
            continue
        for line in lines:
            result[(line.speaker_id, line.speaker_name)] = {
                "speaker_id": line.speaker_id, "speaker_name": line.speaker_name}
    return list(result.values())


async def prepare_authored_dialogue(project, updates: dict, *, current: Shot | None,
                                   provider, user_message: str, user_message_id: str):
    """Return attributed updates plus server-owned provenance, without writes."""
    updates = dict(updates)
    if project.mode != ProjectMode.director or not {"dialogue", "dialogue_lines"} & updates.keys():
        return updates, None
    if (current is not None and updates.get("dialogue_lines") is None
            and updates.get("dialogue", current.dialogue) == current.dialogue
            and current.dialogue_lines is not None):
        # Camera edits / identical round trips must not re-author saved lines.
        verify_dialogue_sources(project, current, current.dialogue_lines)
        updates["dialogue_lines"] = current.dialogue_lines
        if updates.get("scene_id", current.scene_id) == current.scene_id:
            return updates, None
    scene_id = updates.get("scene_id", current.scene_id if current else "")
    beat = updates.get("script_beat", current.script_beat if current else "")
    lines = updates.get("dialogue_lines")
    texts = updates.get("dialogue")
    if texts is None:
        texts = [DialogueLine.model_validate(v).text for v in lines or []]
    source_kind, source_text = "explicit_lines", ""
    if lines is None and texts:
        # Source choice is based on exact words, not character/intent keywords.
        # User words take precedence; newly authored beats may contain dialogue
        # invented under the user's creative request, without rewriting script.
        sources = [("user_message", user_message), ("authored_beat", beat),
                   ("script", project.script_text)]
        source = next(((kind, text) for kind, text in sources
                       if text and all(words in text for words in texts)), None)
        if source is None or provider is None:
            raise DialogueContractError([DialogueIssue(code="dialogue_source_unresolved",
                action="Resubmit this append/revise with attributed dialogue_lines, or an exact "
                       "speaker-cued source for every line. No shot was saved; do not retry prompt writing.")])
        source_kind, source_text = source
        candidate = Shot(id=current.id if current else "pending", project_id=project.id,
                         scene_id=scene_id, title="Dialogue authoring", script_beat=beat,
                         dialogue=texts, duration_s=8)
        lines = await ground_dialogue(project.model_copy(update={"script_text": source_text}),
            candidate, provider, known_speakers=known_speakers(project), authoring_request=user_message)
    lines = [DialogueLine.model_validate(v) for v in lines or []]
    updates["dialogue_lines"] = lines
    evidence = {"user_message_id": user_message_id, "user_message": user_message,
                "source_kind": source_kind, "source_text": source_text,
                "source_hash": digest([source_kind, source_text]),
                "lines": [v.model_dump(mode="json") for v in lines]}
    return updates, evidence
