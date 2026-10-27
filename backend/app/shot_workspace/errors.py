"""Concise validation explanations without dumping model responses or traces."""
import re
from pydantic import ValidationError

LABELS = {
    "dialogue_bindings_missing": "Spoken dialogue was returned without valid source-line bindings.",
    "dialogue_block_invalid": "A speech block is missing its language label or has invalid tags.",
    "dialogue_block_mismatch": "Generated speech differs from the saved dialogue.",
    "dialogue_coverage_mismatch": "Dialogue was omitted, repeated or placed in a different order.",
    "dialogue_speaker_mismatch": "A dialogue line was assigned to the wrong speaker.",
    "dialogue_prose_conflict": "The action text assigns speech to a different speaker than the saved dialogue.",
    "dialogue_unknown_line": "The prompt refers to a dialogue line that is not in this shot.",
    "dialogue_unknown_block": "A dialogue binding points to a speech block that does not exist.",
    "dialogue_span_invalid": "A split dialogue line has missing, overlapping or invalid text spans.",
    "dialogue_placeholder_invalid": "A dialogue placeholder is malformed or mixes literal speech with source references.",
    "picture_binding_invalid": "Picture tags do not match the selected references.",
}


def validation_reason(error, raw, shot):
    if isinstance(error, ValidationError):
        return "\n".join(f"Field {'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors(include_input=False)[:5])
    issues = getattr(error, "issues", [])
    if issues:
        reasons = []
        for issue in issues[:5]:
            code = getattr(issue, "code", "prompt_format")
            line = getattr(issue, "line_id", None)
            label = LABELS.get(code, "Prompt validation failed: " + code.replace("_", " ") + ".")
            reasons.append((f"Dialogue {line}: " if line else "") + label)
        # Explain why final-format speech could not be bound to authored lines.
        if any(getattr(issue, "code", "") == "dialogue_bindings_missing" for issue in issues):
            from ..agents.director.planner import _extract_json_payload
            try:
                value = _extract_json_payload(raw)
                detail = value.get("prompt_sections", value).get("detailed_description", "")
                blocks = re.findall(r"<d>\[([^\]\n]+)\]\s*(.*?)</d>", detail, re.DOTALL)
                lines = shot.dialogue_lines or []
                if len(blocks) != len(lines):
                    reasons.append(f"Expected {len(lines)} saved dialogue line(s); found {len(blocks)} complete speech block(s).")
                for index, ((language, words), line) in enumerate(zip(blocks, lines), 1):
                    if language != line.language:
                        reasons.append(f"Dialogue line {index}: expected language [{line.language}], received [{language[:50]}].")
                    if words != line.text:
                        reasons.append(f"Dialogue line {index}: spoken words differ from the saved text. Keep the exact words, punctuation and order.")
            except (ValueError, AttributeError, TypeError):
                pass
        return "\n".join(dict.fromkeys(reasons))
    # Plain core validators already explain section/tag defects. Bound the text;
    # never render a JSON response, traceback, or provider credential payload.
    text = str(error)
    if text.lstrip().startswith(("{", "[")) or len(text) > 1200:
        return "The response is not valid prompt JSON. Return the six required prompt sections."
    return text[:1200] or "The response could not be parsed as the six required prompt sections."
