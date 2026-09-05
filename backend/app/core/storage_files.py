from __future__ import annotations

from pathlib import Path

from .paths import resolve_under_data


def resolve_data_file(rel: str) -> Path | None:
    """Resolve a path under data/ safely (no traversal), project-rooted layout aware."""
    return resolve_under_data(rel)
