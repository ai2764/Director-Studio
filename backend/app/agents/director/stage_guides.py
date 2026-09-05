"""Load on-demand Director guidance for a production stage."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


GUIDE_IDS = frozenset(
    {
        "script-planning",
        "storyboard-validation",
        "reference-strategy",
        "reference-frame-generation",
        "visual-qc",
        "h3-prompt-writing",
        "video-qc",
    }
)


def _guides_dir() -> Path:
    return Path(__file__).with_name("guides")


def load_stage_guides(guide_ids: Iterable[str]) -> str:
    """Read requested stage guides from disk without caching their contents."""
    blocks: list[str] = []
    for guide_id in dict.fromkeys(guide_ids):
        if guide_id not in GUIDE_IDS:
            raise ValueError(f"unknown Director stage guide: {guide_id}")
        path = _guides_dir() / f"{guide_id}.md"
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(
                f"Director stage guide could not be loaded: {guide_id}: {path}: {exc}"
            ) from exc
        if not body:
            raise RuntimeError(f"Director stage guide is empty: {guide_id}: {path}")
        blocks.append(
            f'<DIRECTOR_STAGE_GUIDE id="{guide_id}">\n{body}\n</DIRECTOR_STAGE_GUIDE>'
        )
    return "\n\n".join(blocks)
