from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture
def tmp_projects_dir(tmp_path, monkeypatch):
    """Point settings.projects_dir at an isolated temp directory for store tests."""
    projects = tmp_path / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "projects_dir", projects)
    return projects
