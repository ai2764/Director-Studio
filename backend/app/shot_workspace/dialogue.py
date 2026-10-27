"""Explicit speaker cues are authored data, not inferred casting decisions."""
import re
from ..core.projects.dialogue import DialogueLine, DialogueSource, digest

_CUE = re.compile(r"^([^\[\]:：\n]+)\s*\[([^\[\]\n]+)\]\s*[:：]\s*(.+)$")


def parse_cues(values, scene_id):
    lines = []
    for index, value in enumerate(values):
        match = _CUE.fullmatch(value.strip())
        if not match:
            raise ValueError("Use one dialogue cue per line: Speaker [Language]: exact spoken words.")
        speaker, language, text = (part.strip() for part in match.groups())
        speaker_id = "speaker_" + digest(speaker.casefold())[:12]
        lines.append(DialogueLine(line_id=f"line_{index + 1}", speaker_id=speaker_id, speaker_name=speaker,
            language=language, text=text, source=DialogueSource(kind="shot_revision", source_hash="pending",
                scene_id=scene_id, quote=f"{speaker}: {text}", occurrence=index)))
    return lines


def format_cues(shot):
    return [f"{line.speaker_name} [{line.language}]: {line.text}" for line in shot.dialogue_lines or []]
