"""Cheap structural checks only: no model calls and no semantic quality claims."""
import re

from .dialogue import digest
from ..h3.prompt import SECTION_KEYS


BRIEF_FIELDS = ("scene_id", "script_beat", "duration_s", "shot_type", "camera_angle",
                "camera_motion", "composition", "dialogue", "dialogue_lines", "refs",
                "voice_refs", "video_context", "source_audio_path", "music_segment")
SECTION_LABELS = dict(zip(SECTION_KEYS, ("Subject definitions", "Summary", "Reference retention", "Detailed description", "Soundscape", "Music")))


def brief_signature(shot):
    data = shot.model_dump(mode="json")
    return digest({key: data.get(key) for key in BRIEF_FIELDS})


def stamp_prompt(shot):
    shot.meta["prompt_brief_signature"] = brief_signature(shot)
    shot.meta["prompt_brief_stale"] = False
    return shot


def check_prompt(shot):
    baseline = shot.meta.get("prompt_brief_signature")
    stale = (bool(shot.meta.get("prompt_brief_stale"))
             or bool(shot.meta.get("material_review_pending"))
             or bool(baseline and baseline != brief_signature(shot)))
    errors, warnings = [], []
    if stale:
        errors.append({"code": "prompt_stale", "message": "Prompt out of date: the brief, dialogue, references or continuity changed. Write the prompt again."})
    sections = shot.prompt_sections.model_dump()
    text = "\n".join(str(sections[key]) for key in SECTION_KEYS)
    missing = [key for key in SECTION_KEYS if not sections[key].strip()]
    if missing:
        errors.append({"code": "missing_sections", "message": "Incomplete prompt. Missing: " + "、".join(SECTION_LABELS[key] for key in missing)})
    if not text.strip():
        return {"stale": stale, "valid": False, "errors": errors, "warnings": warnings}
    if not baseline and not stale:
        warnings.append({"code": "legacy_unverified", "message": "This legacy prompt has no saved brief version. Consider rewriting it."})
    indices = [ref.picture_index for ref in shot.refs]
    submitted = set(indices)
    if len(submitted) != len(indices) or sorted(indices) != list(range(1, len(indices) + 1)):
        errors.append({"code": "picture_order", "message": "Reference numbers are duplicated or out of sequence. Relink the references."})
    found = {int(value) for value in re.findall(r"<Picture\s+([0-9]+)>", text)}
    for index in sorted(submitted - found):
        errors.append({"code": "unused_picture", "message": f"Selected reference P{index} is not cited in the prompt."})
    for index in sorted(found - submitted):
        errors.append({"code": "unknown_picture", "message": f"The prompt cites an unlinked <Picture {index}>."})
    if not submitted:
        errors.append({"code": "missing_references", "message": "Link at least one reference image."})
    malformed = re.findall(r"<\s*/?\s*(?:Picture|Audio)(?=\s|[0-9>])[^>\n]*(?:>|$)", text, re.IGNORECASE | re.MULTILINE)
    if any(not re.fullmatch(r"<(?:Picture|Audio) [1-9][0-9]*>", tag) for tag in malformed):
        errors.append({"code": "reference_tag_format", "message": "Invalid reference tag. Use <Picture 1> or <Audio 1>."})
    if re.search(r"(?<![<\w])\b(?:Picture|Audio)\s+[0-9]+\b(?!>)", text):
        errors.append({"code": "reference_tag_delimiters", "message": "Reference tags need angle brackets: <Picture 1>, <Audio 1>."})
    audio_count = 0 if shot.source_audio_path else 1 if shot.music_segment and shot.music_segment.use_as_audio_reference else len(shot.voice_refs)
    expected_audio = set(range(1, audio_count + 1))
    found_audio = {int(value) for value in re.findall(r"<Audio\s+([0-9]+)>", text)}
    if found_audio != expected_audio:
        errors.append({"code": "audio_bindings", "message": "Audio tags do not match the linked audio references."})
    depth, invalid_dialogue = 0, False
    for tag in re.findall(r"</?d>", text):
        depth += 1 if tag == "<d>" else -1
        if depth not in (0, 1):
            invalid_dialogue = True
    if depth != 0 or invalid_dialogue:
        errors.append({"code": "dialogue_tag_format", "message": "Dialogue tags <d> and </d> are not correctly paired."})
    if "\ufffd" in text:
        errors.append({"code": "encoding", "message": "The prompt contains corrupted text."})
    return {"stale": stale, "valid": not errors, "errors": errors, "warnings": warnings}
