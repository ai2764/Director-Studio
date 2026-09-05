"""Load the Director operating contract before every local-agent inference."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from .stage_guides import load_stage_guides


_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _skill_path() -> Path:
    configured = (os.environ.get("DS_DIRECTOR_SKILL_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()

    user_skill = Path.home() / ".codex" / "skills" / "director" / "SKILL.md"
    if user_skill.exists():
        return user_skill

    return Path(__file__).with_name("DIRECTOR_SKILL.md")


def load_director_skill() -> str:
    """Read the current skill from disk; deliberately do not cache it."""
    path = _skill_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Director skill could not be loaded: {path}: {exc}") from exc

    body = _FRONTMATTER_RE.sub("", raw, count=1).strip()
    if not body:
        raise RuntimeError(f"Director skill is empty: {path}")
    return body


def with_director_skill(task_instructions: str, *, guides: Iterable[str] = ()) -> str:
    """Put the live Director contract before task-specific instructions."""
    core_block = (
        "<DIRECTOR_SKILL>\n"
        f"{load_director_skill()}\n"
        "</DIRECTOR_SKILL>"
    )
    stage_blocks = load_stage_guides(guides)
    task_block = (
        "<TASK_INSTRUCTIONS>\n"
        f"{task_instructions.strip()}\n"
        "</TASK_INSTRUCTIONS>"
    )
    return "\n\n".join(block for block in (core_block, stage_blocks, task_block) if block)
