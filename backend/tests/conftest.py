from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolate_default_runtime_data(tmp_path, monkeypatch):
    """Tests must never fall back to the live service's project or Library data."""
    root = tmp_path / "isolated-runtime"
    for field, path in {
        "data_dir": root,
        "projects_dir": root / "projects",
        "jobs_dir": root / "jobs",
        "library_root": root / "library",
        "library_dir": root / "library" / "actors",
        "workflow_profiles_dir": root / "workflow_profiles",
    }.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, field, path)


@pytest.fixture
def enable_reference_review(monkeypatch):
    """Stub only external visual inference; keep capture, validation and storage real."""
    import base64
    import io
    import json
    from PIL import Image

    def enable(provider):
        complete = provider.complete

        async def vision(system, user, *, images, guides=()):
            assert len(images) == 1
            with Image.open(io.BytesIO(base64.b64decode(images[0]))) as image:
                image.verify()
            return json.dumps({"readable": True, "description": "Fixture reference inspected.", "concerns": []})

        async def with_decision(system, user, *, guides=()):
            if "reference review decision" in system.lower():
                return json.dumps({"brief": None, "rewrite_prompt": True,
                                   "reason": "Use the inspected references.", "blocking_question": None})
            return await complete(system, user, guides=guides)

        monkeypatch.setattr(provider, "complete_with_images", vision, raising=False)
        monkeypatch.setattr(provider, "complete", with_decision)
    return enable


@pytest.fixture
def tmp_projects_dir(tmp_path, monkeypatch):
    """Point settings.projects_dir at an isolated temp directory for store tests."""
    projects = tmp_path / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "projects_dir", projects)
    return projects
