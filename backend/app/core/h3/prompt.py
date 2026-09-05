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

_TIME_BOUND_PICTURE_PATTERNS = (
    re.compile(
        r"\b(?:use|show|switch(?:\s+to)?)\s+<Picture\s+\d+>"
        r"[^.\n]{0,80}\b(?:from|at|during)\s+\d+(?:\.\d+)?"
        r"(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*(?:s|seconds?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:from|at|during)\s+\d+(?:\.\d+)?"
        r"(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*(?:s|seconds?)\b"
        r"[^.\n]{0,80}\b(?:use|show|switch(?:\s+to)?)\s+<Picture\s+\d+>",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:from|at|during)\s+\d+(?:\.\d+)?"
        r"(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*(?:s|seconds?)\b"
        r"[^.\n]{0,80}<Picture\s+\d+>[^.\n]{0,40}"
        r"\b(?:activate(?:s)?|take(?:s)?\s+over|switch(?:es)?|"
        r"is\s+(?:used|shown))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"<Picture\s+\d+>[^.\n]{0,40}"
        r"\b(?:activate(?:s)?|take(?:s)?\s+over|switch(?:es)?|"
        r"is\s+(?:used|shown))\b[^.\n]{0,80}"
        r"\b(?:from|at|during)\s+\d+(?:\.\d+)?"
        r"(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*(?:s|seconds?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bswitch(?:es|ed|ing)?\s+from\s+<Picture\s+\d+>\s+to\s+"
        r"<Picture\s+\d+>[^.\n]{0,80}"
        r"\b(?:from|at|during)\s+\d+(?:\.\d+)?"
        r"(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*(?:s|seconds?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:from|at|during)\s+\d+(?:\.\d+)?"
        r"(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*(?:s|seconds?)\b"
        r"[^.\n]{0,80}\bswitch(?:es|ed|ing)?\s+from\s+"
        r"<Picture\s+\d+>\s+to\s+<Picture\s+\d+>",
        re.IGNORECASE,
    ),
)

_REFERENCE_CONTROLLER_PATTERN = re.compile(
    r"<Picture\s+\d+>|\b(?:this|the)\s+(?:layout|picture|reference)\b",
    re.IGNORECASE,
)
_TIME_WINDOW_PATTERN = re.compile(
    r"(?:\(\s*)?\d+(?:\.\d+)?\s*[–—-]\s*\d+(?:\.\d+)?\s*"
    r"(?:s|seconds?)(?:\s*\))?|"
    r"\b(?:first|opening)\s+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+seconds?\b|"
    r"\b(?:until|before|after)\b",
    re.IGNORECASE,
)
_SEMANTIC_CLAUSE_SPLIT_PATTERN = re.compile(r"\s*;\s*|\s+while\s+", re.IGNORECASE)

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


def _has_reference_controlled_timed_state(text: str) -> bool:
    """Find semantic time assignments controlled by a Picture/Layout reference."""
    for paragraph in re.split(r"\n+", text):
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            for clause in _SEMANTIC_CLAUSE_SPLIT_PATTERN.split(sentence):
                if (
                    _REFERENCE_CONTROLLER_PATTERN.search(clause)
                    and _TIME_WINDOW_PATTERN.search(clause)
                ):
                    return True
    return False


_ENDPOINT_PICTURE_PATTERNS = (
    re.compile(
        r"<Picture\s+\d+>[^.\n]{0,100}\b"
        r"(?:is|becomes|defines|serves\s+as|acts\s+as|must\s+be|should\s+be)\s+"
        r"(?:the\s+|an?\s+)?(?:exact\s+)?(?:first|last)[\s-]*frame\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:use|treat|set|lock)\s+<Picture\s+\d+>\s+as\s+"
        r"(?:the\s+)?(?:exact\s+)?(?:first|last)[\s-]*frame\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"<Picture\s+\d+>[^.\n]{0,160}\blast-frame\s+socket\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"<Picture\s+\d+>[^.\n]{0,160}\bactivates?\s+only\s+at\s+the\s+beginning\b",
        re.IGNORECASE,
    ),
)


def validate_no_time_addressable_pictures(text: str) -> None:
    if any(pattern.search(text) for pattern in _TIME_BOUND_PICTURE_PATTERNS) or (
        _has_reference_controlled_timed_state(text)
    ):
        raise ValueError(
            "Pictures condition the whole clip; put action timing in "
            "detailed_description"
        )
    if any(pattern.search(text) for pattern in _ENDPOINT_PICTURE_PATTERNS):
        raise ValueError(
            "Pictures condition the whole clip; do not describe a Picture as a "
            "first frame, last-frame socket, or start-only activation"
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
            raise ValueError(f"missing selected Layout binding: {tag}")


def compose_h3_prompt(sections: PromptSections) -> str:
    """Compose ordered H3 prompt text from PromptSections."""
    parts: list[str] = []
    for key in SECTION_KEYS:
        value = getattr(sections, key)
        parts.append(f"{key}:\n{value}")
    return "\n".join(parts)


def validate_h3_prompt(
    prompt: str,
    dialogue: list[str],
    *,
    audio_count: int = 0,
    required_picture_indices: Iterable[int] = (),
    submitted_picture_indices: Iterable[int] | None = None,
) -> None:
    """Validate section order, non-empty bodies, and exact dialogue occurrence.

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

    # Each dialogue line appears exactly once
    for line in dialogue:
        if not line:
            raise ValueError("dialogue line is empty")
        count = prompt.count(line)
        if count != 1:
            raise ValueError(
                f"dialogue line must appear exactly once (found {count}): {line!r}"
            )

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

    validate_no_time_addressable_pictures(prompt)
    validate_required_picture_bindings(
        prompt,
        required_picture_indices,
        submitted_picture_indices=submitted_picture_indices,
    )
