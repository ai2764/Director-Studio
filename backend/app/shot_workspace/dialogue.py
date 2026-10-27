"""Explicit speaker cues are authored data, not inferred casting decisions."""
import re
from ..core.projects.dialogue import DialogueLine, DialogueSource, digest

_CUE = re.compile(r"^([^\[\]:：\n]+)\s*\[([^\[\]\n]+)\]\s*[:：]\s*(.+)$")


def parse_cues(values, scene_id, *, shot=None):
    lines = []
    for index, value in enumerate(values):
        cue = value.strip()
        empty_language = re.fullmatch(r"([^\[\]:：\n]+)\s*\[\s*\]\s*[:：]\s*(.+)", cue)
        if empty_language and shot is not None:
            # Old saved lines may omit language while the certified prompt has it.
            # Reuse only a current source-backed record, never infer from pixels/text.
            from ..core.projects.store import load_project
            from ..agents.director.dialogue_preflight import dialogue_contract_current
            project = load_project(shot.project_id)
            if project and dialogue_contract_current(project, shot):
                for line in (shot.meta.get("prompt_dialogue_contract") or {}).get("lines", []):
                    if (line.get("speaker_name") == empty_language.group(1).strip()
                            and line.get("text") == empty_language.group(2).strip() and line.get("language")):
                        cue = f"{line['speaker_name']} [{line['language']}]: {line['text']}"
                        break
        match = _CUE.fullmatch(cue)
        if not match:
            raise ValueError(f"Dialogue line {index + 1} needs a speaker and nonempty language. Use Speaker [Language]: exact spoken words. Your text was kept.")
        speaker, language, text = (part.strip() for part in match.groups())
        speaker_id = "speaker_" + digest(speaker.casefold())[:12]
        lines.append(DialogueLine(line_id=f"line_{index + 1}", speaker_id=speaker_id, speaker_name=speaker,
            language=language, text=text, source=DialogueSource(kind="shot_revision", source_hash="pending",
                scene_id=scene_id, quote=f"{speaker}: {text}", occurrence=index)))
    return lines


def format_cues(shot):
    return [f"{line.speaker_name} [{line.language}]: {line.text}" for line in shot.dialogue_lines or []]
