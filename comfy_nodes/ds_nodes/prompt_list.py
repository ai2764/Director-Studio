# SPDX-License-Identifier: Apache-2.0
# Copyright Director Studio contributors.
"""Split multiline text into a list of prompts (one ComfyUI list item per line)."""

from __future__ import annotations

HELP = (
    "DS Prompt List: one prompt per non-empty line of multiline_text, starting at "
    "start_index (0-based) and taking at most max_rows lines. Each prompt is "
    "prepend_text + line + append_text joined by single spaces (empty parts skipped). "
    "Outputs are lists, so downstream nodes run once per prompt."
)


def build_prompts(prepend_text: str, multiline_text: str, append_text: str,
                  start_index: int, max_rows: int) -> tuple[list[str], list[str]]:
    lines = [line.strip() for line in (multiline_text or "").splitlines()]
    lines = [line for line in lines if line]
    start = max(0, int(start_index))
    count = max(0, int(max_rows))
    selected = lines[start:start + count]
    prefix = (prepend_text or "").strip()
    suffix = (append_text or "").strip()
    prompts = [" ".join(part for part in (prefix, line, suffix) if part) for line in selected]
    return prompts, selected


class DSPromptList:
    CATEGORY = "Director Studio/text"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "body_text", "show_help")
    OUTPUT_IS_LIST = (True, True, False)

    @classmethod
    def INPUT_TYPES(cls):  # noqa: N802 - ComfyUI API
        return {
            "required": {
                "prepend_text": ("STRING", {"multiline": False, "default": ""}),
                "multiline_text": ("STRING", {"multiline": True, "default": ""}),
                "append_text": ("STRING", {"multiline": False, "default": ""}),
                "start_index": ("INT", {"default": 0, "min": 0, "max": 9999}),
                "max_rows": ("INT", {"default": 1000, "min": 1, "max": 9999}),
            }
        }

    def run(self, prepend_text="", multiline_text="", append_text="", start_index=0, max_rows=1000):
        prompts, bodies = build_prompts(prepend_text, multiline_text, append_text, start_index, max_rows)
        return (prompts, bodies, HELP)
