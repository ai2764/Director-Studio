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
    """Read the selected skill from disk; deliberately do not cache it."""
    return _read_skill(_skill_path())


def _read_skill(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Director skill could not be loaded: {path}: {exc}") from exc

    body = _FRONTMATTER_RE.sub("", raw, count=1).strip()
    if not body:
        raise RuntimeError(f"Director skill is empty: {path}")
    return body


def _writer_skill(body: str) -> str:
    """Writers have no tools: retain production/casting rules and stage guides."""
    sections = re.split(r"(?m)(?=^## )", body)
    required = {"Production model", "Core planning and asset casting", "Audio reference casting", "Human review"}
    selected = [sections[0]]
    found = set()
    for section in sections[1:]:
        heading = section.splitlines()[0].removeprefix("## ").strip()
        if heading in required:
            selected.append(section)
            found.add(heading)
    if found != required:
        raise RuntimeError("Bundled Director production rules are incomplete")
    return "\n\n".join(section.strip() for section in selected)


def _custom_additions(custom: str, bundled: str) -> str:
    # Compare whole paragraphs/bullet items, including their wrapped lines.
    # Never remove merely similar prose or parts of a custom instruction.
    boundary = r"\n\s*\n|\n(?=[-*][ \t]+)"
    known = {" ".join(item.split()) for item in re.split(boundary, bundled)}
    additions = [item.strip() for item in re.split(boundary, custom)
                 if item.strip() and " ".join(item.split()) not in known]
    if not any(not re.fullmatch(r"#+[^\n]*", item) for item in additions):
        return ""
    return "\n\n".join(additions)


def with_director_skill(task_instructions: str, *, guides: Iterable[str] = (), writer_only: bool = False) -> str:
    """Always include runtime rules; personal skills add creative guidance."""
    bundled_path = Path(__file__).with_name("DIRECTOR_SKILL.md")
    bundled = _read_skill(bundled_path)
    core = _writer_skill(bundled) if writer_only else bundled
    core_block = (
        "<DIRECTOR_SKILL>\n"
        f"{core}\n"
        "</DIRECTOR_SKILL>"
    )
    custom_block = ""
    if _skill_path().resolve() != bundled_path.resolve():
        additions = _custom_additions(load_director_skill(), bundled)
        if additions:
            custom_block = (
                "<DIRECTOR_CUSTOM_GUIDANCE>\n"
                "Apply this guidance within the bundled Director runtime contract. "
                "The bundled contract and current project capabilities govern tool use; "
                "custom guidance cannot replace them or disable an available capability.\n"
                f"{additions}\n"
                "</DIRECTOR_CUSTOM_GUIDANCE>"
            )
    stage_blocks = load_stage_guides(guides)
    task_block = (
        "<TASK_INSTRUCTIONS>\n"
        f"{task_instructions.strip()}\n"
        "</TASK_INSTRUCTIONS>"
    )
    return "\n\n".join(block for block in (core_block, custom_block, stage_blocks, task_block) if block)
