from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.agents.director.service import DirectorService
from app.config import settings
from app.core.jobs import store
from app.core.projects.layouts import (
    GptLayoutBrief,
    LayoutProvider,
    LayoutSourceRef,
    RefRole,
)
from app.core.projects.models import Shot, ShotStatus
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from app.core.schemas import LibraryAsset


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
) + (b"\x00" * 3000)


@pytest.fixture
def gpt_service_fixture(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    monkeypatch.setattr(settings, "gpt_bridge_max_file_mb", 20)
    monkeypatch.setattr(settings, "gpt_bridge_max_total_mb", 100)

    specs = [
        ("scenes", "scene_archive", "Archive", "angle_00"),
        ("actors", "actor_lu", "Lu", "master"),
        ("actors", "actor_chen", "Chen", "master"),
        ("props", "prop_recorder", "Recorder", "master"),
    ]
    refs = []
    roles = [RefRole.scene, RefRole.actor, RefRole.actor, RefRole.prop]
    for role, (kind, asset_id, name, file_key) in zip(roles, specs, strict=True):
        directory = library / kind / asset_id
        directory.mkdir(parents=True)
        filename = f"{file_key}.png"
        (directory / filename).write_bytes(PNG + asset_id.encode())
        asset = LibraryAsset(
            id=asset_id,
            kind=kind,
            name=name,
            notes=f"approved {name}",
            pipeline_id="external",
            job_id="seed",
            created_at="2026-01-01T00:00:00+00:00",
            files={file_key: filename},
            meta={},
        )
        (directory / "asset.json").write_text(
            asset.model_dump_json(indent=2),
            encoding="utf-8",
        )
        refs.append(
            LayoutSourceRef(
                role=role,
                asset_id=asset_id,
                file_key=file_key,
                notes=f"job for {name}",
            )
        )

    project = create_project("GPT Layout", "Chen enters while Lu hides the recorder")
    shot = Shot(
        id="shot_gpt_layout",
        project_id=project.id,
        scene_id="scene_01",
        title="Door reveal",
        script_beat="Chen enters behind Lu; the recorder is visible.",
        duration_s=8.0,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class ForbiddenOrchestrator:
        def __getattr__(self, name):
            raise AssertionError(f"GPT service must not use the VRAM orchestrator: {name}")

    service = DirectorService(plan_provider=object(), orchestrator=ForbiddenOrchestrator())
    return service, project, shot, refs


def _brief(refs):
    return GptLayoutBrief(
        purpose="establish newcomer and recorder continuity",
        state_description="Chen enters behind Lu while the recorder appears on the desk",
        time_hint="after the door opens",
        source_refs=refs,
        generation_prompt=(
            "Image1 controls the archive set. Image2 controls Lu. "
            "Image3 controls Chen. Image4 controls the recorder. Return one image."
        ),
    )


@pytest.mark.asyncio
async def test_queue_gpt_reference_frame_preserves_four_source_order(
    gpt_service_fixture, monkeypatch
):
    import app.agents.director.service as service_module

    service, project, shot, refs = gpt_service_fixture
    started = []

    async def fake_start(job, *, images=None):
        started.append((job, dict(images or {})))
        return job

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)
    monkeypatch.setattr(
        service_module,
        "analyze_ref_frame",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("GPT service must not invoke Qwen visual direction")
        ),
    )

    updated = await service.queue_gpt_reference_frame(shot.id, brief=_brief(refs))

    layout = updated.layout_refs[-1]
    job, images = started[0]
    assert layout.provider == LayoutProvider.gpt
    assert layout.selected_for_h3 is False
    assert [ref.asset_id for ref in layout.source_refs] == [ref.asset_id for ref in refs]
    assert job.pipeline_id == "gpt_ref_frame"
    assert job.params["image_keys"] == ["ref_0", "ref_1", "ref_2", "ref_3"]
    assert list(images) == ["ref_0", "ref_1", "ref_2", "ref_3"]
    assert [item["image_number"] for item in job.params["layout_source_refs"]] == [1, 2, 3, 4]
    assert "contentBase64" not in job.model_dump_json()
    persisted = load_shot(project.id, shot.id)
    assert persisted is not None
    assert persisted.layout_refs[-1].id == layout.id


@pytest.mark.asyncio
async def test_gpt_actor_sources_add_identity_and_wardrobe_authority(
    gpt_service_fixture, monkeypatch
):
    import app.agents.director.service as service_module

    service, _project, shot, refs = gpt_service_fixture
    started = []

    async def fake_start(job, *, images=None):
        started.append(job)
        return job

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)

    await service.queue_gpt_reference_frame(shot.id, brief=_brief(refs))

    prompt = started[0].params["generation_prompt"]
    assert prompt.startswith("REFERENCE AUTHORITY - follow this before the shot request:")
    assert "Image2 is the authoritative character reference for Lu" in prompt
    assert "Image3 is the authoritative character reference for Chen" in prompt
    assert "preserve the same exact person" in prompt
    assert "Preserve the approved wardrobe shown in Image2" in prompt
    assert "Preserve the approved wardrobe shown in Image3" in prompt
    assert prompt.endswith(_brief(refs).generation_prompt)


@pytest.mark.asyncio
async def test_queue_gpt_reference_frame_creates_a_text_only_job(
    gpt_service_fixture, monkeypatch
):
    import app.agents.director.service as service_module

    service, project, shot, _refs = gpt_service_fixture
    started = []

    async def fake_start(job, *, images=None):
        started.append((job, dict(images or {})))
        return job

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)
    brief = GptLayoutBrief(
        purpose="establish the empty room",
        state_description="the review room before anyone enters",
        source_refs=[],
        generation_prompt="Create one cinematic establishing plate of an empty review room.",
    )

    updated = await service.queue_gpt_reference_frame(shot.id, brief=brief)

    layout = updated.layout_refs[-1]
    job, images = started[0]
    assert layout.provider == LayoutProvider.gpt
    assert layout.source_refs == []
    assert job.params["image_keys"] == []
    assert job.params["layout_source_refs"] == []
    assert images == {}
    persisted = load_shot(project.id, shot.id)
    assert persisted is not None
    assert persisted.layout_refs[-1].id == layout.id


@pytest.mark.asyncio
async def test_gpt_source_file_key_must_exist_before_job_creation(
    gpt_service_fixture, monkeypatch
):
    import app.agents.director.service as service_module

    service, _project, shot, refs = gpt_service_fixture
    started = []
    monkeypatch.setattr(
        service_module,
        "start_pipeline_job",
        lambda *args, **kwargs: started.append(args),
    )
    bad_refs = [*refs]
    bad_refs[3] = bad_refs[3].model_copy(update={"file_key": "missing_closeup"})

    with pytest.raises(ValueError, match="missing_closeup"):
        await service.queue_gpt_reference_frame(shot.id, brief=_brief(bad_refs))

    assert started == []
    assert store.list_jobs(limit=None) == []


@pytest.mark.asyncio
async def test_gpt_source_budget_is_checked_before_job_creation(
    gpt_service_fixture, monkeypatch
):
    import app.agents.director.service as service_module

    service, _project, shot, refs = gpt_service_fixture
    monkeypatch.setattr(settings, "gpt_bridge_max_total_mb", 0)
    started = []
    monkeypatch.setattr(
        service_module,
        "start_pipeline_job",
        lambda *args, **kwargs: started.append(args),
    )

    with pytest.raises(ValueError, match="total upload limit"):
        await service.queue_gpt_reference_frame(shot.id, brief=_brief(refs))

    assert started == []
    assert store.list_jobs(limit=None) == []
