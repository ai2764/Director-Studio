"""H3 Ref2VA six-section prompt compose and validate."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.core.projects.models import PromptSections

SECTION_KEYS: list[str] = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]

# UTF-8 replacement character — optional corruption signal
_REPLACEMENT_CHAR = "\ufffd"

_TIMED_ACTION_INTERVAL_PATTERN = re.compile(
    r"(?<![\d.])(?P<start>\d+(?:\.\d+)?)\s*[–—-]\s*"
    r"(?P<end>\d+(?:\.\d+)?)\s*(?:s|seconds?)\b",
    re.IGNORECASE,
)
_TAIL_TRANSITION_VERB_PATTERN = re.compile(
    r"\b(?:continue(?:s|d|ing)?|carry(?:ing|ies|ied)?|unwind(?:s|ing)?|"
    r"dissolv(?:e|es|ed|ing)|transform(?:s|ed|ing)?|morph(?:s|ed|ing)?|"
    r"open(?:s|ed|ing)?|clear(?:s|ed|ing)?|reveal(?:s|ed|ing)?|"
    r"resolv(?:e|es|ed|ing))\b",
    re.IGNORECASE,
)
_TAIL_TRANSITION_NEGATION_PATTERNS = (
    re.compile(r"\bhard[\s-]+cut\b", re.IGNORECASE),
    re.compile(r"\b(?:palette|style)\s+only\b", re.IGNORECASE),
    re.compile(r"\bmust\s+not\s+(?:manifest|be\s+visible)\b", re.IGNORECASE),
    re.compile(r"\b(?:do\s+not|don't|never)\s+(?:show|render|manifest)\b", re.IGNORECASE),
    re.compile(r"\b(?:open|start|begin)(?:s|ing)?\s+(?:directly\s+)?(?:on|with)\b", re.IGNORECASE),
)


def validate_tail_frame_transition_prompt(
    sections: PromptSections,
    selected_layouts: Iterable[dict[str, object]],
) -> None:
    """Require an explicit visible handoff for a selected clip-tail Layout.

    The Picture still conditions the full clip. This validates action prose only;
    it does not claim that the reference is an exact or time-addressable frame.
    """
    if not any(
        bool(layout.get("visible_transition_required"))
        for layout in selected_layouts
    ):
        return

    description = sections.detailed_description
    intervals = list(_TIMED_ACTION_INTERVAL_PATTERN.finditer(description))
    if not intervals or float(intervals[0].group("start")) != 0.0:
        raise ValueError(
            "tail-frame transition must begin in the first action interval at 0 seconds"
        )

    first_start = intervals[0].start()
    first_end = intervals[1].start() if len(intervals) > 1 else len(description)
    first_interval = description[first_start:first_end]
    if any(pattern.search(first_interval) for pattern in _TAIL_TRANSITION_NEGATION_PATTERNS):
        raise ValueError(
            "tail-frame transition cannot be a hard cut, style-only cue, or hidden source state"
        )
    if not _TAIL_TRANSITION_VERB_PATTERN.search(first_interval):
        raise ValueError(
            "tail-frame transition needs a visible carryover and transition action in the first interval"
        )


def validate_required_picture_bindings(
    text: str,
    required_indices: Iterable[int],
    *,
    submitted_picture_indices: Iterable[int] | None = None,
    binding_label: str = "required Picture",
) -> None:
    found_indices = [
        int(value)
        for value in re.findall(r"<Picture\s+(\d+)>", text, re.IGNORECASE)
    ]
    if submitted_picture_indices is not None:
        submitted = {int(index) for index in submitted_picture_indices}
        unexpected = sorted(set(found_indices) - submitted)
        if unexpected:
            tags = ", ".join(f"<Picture {index}>" for index in unexpected)
            raise ValueError(
                f"prompt references unsubmitted Picture tags: {tags}"
            )
    for index in dict.fromkeys(int(value) for value in required_indices):
        tag = f"<Picture {index}>"
        if index not in found_indices:
            raise ValueError(f"missing {binding_label} binding: {tag}")


def compose_h3_prompt(sections: PromptSections) -> str:
    """Compose ordered H3 prompt text from PromptSections."""
    parts: list[str] = []
    for key in SECTION_KEYS:
        value = getattr(sections, key)
        parts.append(f"{key}:\n{value}")
    return "\n".join(parts)


def _dialogue_text(text: str) -> str:
    """Ignore formatting whitespace, not words or punctuation."""
    normalized = " ".join(text.split())
    # Joining continued Chinese <d> blocks must not introduce a word separator.
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", normalized)


def _spoken_dialogue_text(text: str) -> str:
    """Return only the authored words from a screenplay-style dialogue row."""
    raw = re.sub(r"^\[[^\[\]\n]+\]\s*", "", str(text).strip())
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) > 1 and re.fullmatch(
        r"[A-Z][A-Z0-9 .'-]*(?:\s*\([^()\n]{1,24}\))?",
        lines[0],
    ):
        value = _dialogue_text(" ".join(lines[1:]))
    else:
        value = _dialogue_text(raw)
    value = re.sub(r"^\[[^\[\]\n]+\]\s*", "", value)
    labelled = re.fullmatch(r"[^:：\n]{1,64}\s*[:：]\s*(.+)", value)
    if labelled:
        speaker = value[: labelled.start(1)].rstrip(" :：")
        candidate = labelled.group(1).strip()
        quote_pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
        quoted = len(candidate) >= 2 and quote_pairs.get(candidate[0]) == candidate[-1]
        screenplay_label = bool(re.fullmatch(r"[\w.-]+(?:\s+[\w.-]+){0,3}", speaker))
        if quoted or screenplay_label:
            value = candidate[1:-1].strip() if quoted else candidate
    return _dialogue_text(value)


def _misplaced_spoken_line(body: str, line: str) -> bool:
    # Exempt only the quoted visible-text occurrence, never the whole section.
    body = re.sub(
        r'\b(?:sign|label|banner|subtitles?|(?:visible|on-screen|neon) text)\s+'
        r'(?:reads?|reading|displays?|displaying|says|saying)\s*:?\s*'
        r'(?:"[^"\n]*"|“[^”\n]*”)',
        "", body, flags=re.IGNORECASE,
    )
    text = _dialogue_text(body)
    phrase = r"(?<![A-Za-z0-9_])" + re.escape(line) + r"(?![A-Za-z0-9_])"
    if not re.search(phrase, text):
        return False
    if text == line or re.search(r"[.!?。！？]$", line):
        return True
    # Bare short words are often ordinary prose (No -> No music.). Require
    # quotation or an explicit vocal cue before treating them as dialogue.
    return bool(
        re.search(r'["“\u0027]' + re.escape(line) + r'["”\u0027]', text)
        or re.search(r"\b(?:says?|whispers?|shouts?|speaks?|sings?)\s*[:,]?\s*" + phrase, text, re.IGNORECASE)
    )


def _validate_dialogue(bodies: dict[str, str], dialogue: list[str]) -> None:
    # Ref2VA guide sections 5.4/6: spoken words belong inside <d> in the
    # timeline, not in summaries of ambience/music. Count vocal content, not
    # matching text on signs or a short line embedded in a longer line.
    expected = [_spoken_dialogue_text(line) for line in dialogue]
    if any(not line for line in expected):
        raise ValueError("dialogue line is empty")
    misplaced = []
    for section, body in bodies.items():
        if section == "detailed_description":
            continue
        repeated = [line for line in dict.fromkeys(expected) if _misplaced_spoken_line(body, line)]
        if repeated or re.search(r"</?d\b", body):
            misplaced.append(f"{section}: {', '.join(repr(line) for line in repeated) or '<d> block'}")
    if misplaced:
        raise ValueError(
            "dialogue misplaced in " + "; ".join(misplaced)
            + ". Keep spoken words only inside detailed_description <d>[Language] ...</d>; "
            "describe ambience/music without repeating the line."
        )

    detail = bodies["detailed_description"]
    blocks = list(re.finditer(r"<d>(.*?)</d>", detail, re.DOTALL))
    remainder = re.sub(r"<d>.*?</d>", "", detail, flags=re.DOTALL)
    if re.search(r"</?d\b", remainder):
        raise ValueError("detailed_description has an unbalanced or malformed <d> block")
    spoken = []
    for index, block in enumerate(blocks, 1):
        content = block.group(1).strip()
        match = re.fullmatch(r"\[[^\[\]\n]+\]\s*(.+)", content, re.DOTALL)
        if match is None or re.search(r"</?d\b", content):
            raise ValueError(f"detailed_description <d> block {index} must contain [Language] and spoken words only")
        words = re.sub(r"<scenetrans>|<cutoff>", "", match.group(1))
        if not words.strip():
            raise ValueError(f"detailed_description <d> block {index} has no spoken words")
        spoken.append(_dialogue_text(words))

    # Compare the ordered spoken timeline, allowing scripted repetitions,
    # multiple lines in one block, and continuation across shot cuts. When no
    # dialogue is specified, source-audio/lyric cues retain their existing role.
    if expected and _dialogue_text(" ".join(spoken)) != _dialogue_text(" ".join(expected)):
        raise ValueError(
            "detailed_description <d> dialogue does not match shot.dialogue in order "
            "(missing, repeated, reordered, or changed words). "
            f"Expected: {' '.join(expected)!r}; found in <d>: {' '.join(spoken)!r}. "
            "Preserve the scripted words and repetitions; keep speaker/action prose outside <d>."
        )


def validate_h3_prompt(
    prompt: str,
    dialogue: list[str],
    *,
    audio_count: int = 0,
    required_picture_indices: Iterable[int] = (),
    submitted_picture_indices: Iterable[int] | None = None,
) -> None:
    """Validate section order, non-empty bodies, and structured spoken dialogue.

    Raises ValueError on any contract violation.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is empty")

    if _REPLACEMENT_CHAR in prompt:
        raise ValueError("prompt contains UTF-8 replacement character (corruption)")

    # Positions of each "{key}:" header (first occurrence)
    positions: list[tuple[str, int]] = []
    for key in SECTION_KEYS:
        header = f"{key}:"
        pos = prompt.find(header)
        if pos < 0:
            raise ValueError(f"section order: missing header {header!r}")
        positions.append((key, pos))

    # Strictly increasing positions
    for i in range(1, len(positions)):
        prev_key, prev_pos = positions[i - 1]
        key, pos = positions[i]
        if pos <= prev_pos:
            raise ValueError(
                f"section order: {key!r} at {pos} is not after {prev_key!r} at {prev_pos}"
            )

    # Non-empty section bodies (text between this header and the next, or EOF)
    bodies: dict[str, str] = {}
    for i, (key, pos) in enumerate(positions):
        header = f"{key}:"
        start = pos + len(header)
        if i + 1 < len(positions):
            end = positions[i + 1][1]
        else:
            end = len(prompt)
        body = prompt[start:end].strip()
        if not body:
            raise ValueError(f"section {key!r} is empty")
        bodies[key] = body

    _validate_dialogue(bodies, dialogue)

    found_audio_indexes = [int(value) for value in re.findall(r"<Audio\s+(\d+)>", prompt)]
    expected_audio_indexes = list(range(1, audio_count + 1))
    for index in expected_audio_indexes:
        count = found_audio_indexes.count(index)
        if count < 1:
            raise ValueError(f"prompt must reference submitted <Audio {index}>")
    unexpected = sorted(set(found_audio_indexes) - set(expected_audio_indexes))
    if unexpected:
        tags = ", ".join(f"<Audio {index}>" for index in unexpected)
        raise ValueError(f"prompt references unsubmitted Audio tags: {tags}")

    validate_required_picture_bindings(
        prompt,
        required_picture_indices,
        submitted_picture_indices=submitted_picture_indices,
    )
