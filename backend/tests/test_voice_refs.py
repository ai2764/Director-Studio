from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.library.store import write_asset
from app.core.projects.models import (
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
    ShotStatus,
    ShotVoiceRef,
)
from app.core.projects.store import create_project, save_project, save_shot
from app.core.schemas import LibraryAsset


def _shot(project_id: str, *, shot_id: str = "sht_voice_refs") -> Shot:
    return Shot(
        id=shot_id,
        project_id=project_id,
        scene_id="sc01",
        title="Dialogue",
        script_beat="Mia speaks.",
        duration_s=6,
        status=ShotStatus.needs_review,
        prompt_sections=PromptSections(),
    )


def _seed_voice(
    project_id: str,
    *,
    asset_id: str,
    duration_s: float = 4.0,
    kind: str = "voices",
    ready: bool = True,
) -> LibraryAsset:
    from app.core.library.store import asset_dir

    directory = asset_dir(kind, asset_id, project_id=project_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reference.wav").write_bytes(b"RIFF-reference")
    asset = LibraryAsset(
        id=asset_id,
        kind=kind,
        name="Mia",
        notes="warm neutral English",
        pipeline_id="external",
        job_id="",
        created_at="2026-08-25T00:00:00+00:00",
        files={"reference": "reference.wav"},
        meta={"duration_s": duration_s, "h3_ready": ready},
        project_id=project_id,
    )
    return write_asset(asset)


def _seed_actor(project_id: str, asset_id: str = "act_mia") -> LibraryAsset:
    from app.core.library.store import asset_dir

    directory = asset_dir("actors", asset_id, project_id=project_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "master.png").write_bytes(b"fake-image" * 400)
    return write_asset(
        LibraryAsset(
            id=asset_id,
            kind="actors",
            name="Mia",
            notes="",
            pipeline_id="external",
            job_id="",
            created_at="2026-08-25T00:00:00+00:00",
            files={"master": "master.png"},
            meta={},
            project_id=project_id,
        )
    )


def _full_voice_prompt() -> PromptSections:
    return PromptSections(
        subject_definitions="<Audio 1> defines Mia's voice identity.",
        summary="Mia speaks.",
        retention_analysis="The reply holds attention.",
        detailed_description="Mia says: Go now.",
        overall_soundscape="Quiet room tone.",
        non_diegetic_music="None.",
    )


@pytest.fixture
def voice_ref_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    return {"projects": projects, "jobs": jobs, "library": library}


@pytest.fixture
def client(voice_ref_env):
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_shot_voice_refs_require_unique_contiguous_order():
    from app.core.projects.models import ShotVoiceRef

    valid = _shot("prj_1").model_copy(
        update={
            "voice_refs": [
                ShotVoiceRef(asset_id="voi_mia", audio_index=1, speaker="Mia"),
                ShotVoiceRef(asset_id="voi_daniel", audio_index=2, speaker="Daniel"),
            ]
        }
    )
    assert [ref.audio_index for ref in valid.voice_refs] == [1, 2]

    base = _shot("prj_1").model_dump(mode="json")
    invalid_sets = [
        [
            {"asset_id": "voi_1", "audio_index": 1},
            {"asset_id": "voi_2", "audio_index": 3},
        ],
        [
            {"asset_id": "voi_1", "audio_index": 1},
            {"asset_id": "voi_1", "audio_index": 2},
        ],
        [
            {"asset_id": f"voi_{index}", "audio_index": index}
            for index in range(1, 5)
        ],
    ]
    for voice_refs in invalid_sets:
        with pytest.raises(ValueError):
            Shot.model_validate({**base, "voice_refs": voice_refs})


def test_existing_shot_without_voice_refs_loads_empty():
    payload = _shot("prj_1").model_dump(mode="json")
    payload.pop("voice_refs", None)
    assert Shot.model_validate(payload).voice_refs == []


def test_patch_shot_accepts_project_voice_refs_and_marks_prompt_stale(
    client,
    voice_ref_env,
):
    project = create_project("P", "Mia speaks")
    _seed_voice(project.id, asset_id="voi_mia")
    shot = _shot(project.id)
    shot.meta = {"prompt_voice_signature": "old", "keep": "value"}
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    response = client.patch(
        f"/api/shots/{shot.id}",
        json={
            "voice_refs": [
                {
                    "asset_id": "voi_mia",
                    "audio_index": 1,
                    "file_key": "reference",
                    "speaker": "Mia",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["voice_refs"][0]["asset_id"] == "voi_mia"
    assert body["voice_refs"][0]["audio_index"] == 1
    assert body["meta"]["prompt_voice_signature"] == ""
    assert body["meta"]["keep"] == "value"


@pytest.mark.parametrize(
    ("setup", "detail"),
    [
        ("other-project", "project"),
        ("wrong-kind", "voice"),
        ("not-ready", "h3-ready"),
        ("too-long", "15 seconds"),
    ],
)
def test_patch_shot_rejects_invalid_voice_assets(
    client,
    voice_ref_env,
    setup: str,
    detail: str,
):
    project = create_project("P", "Mia speaks")
    other = create_project("Other", "script")
    shot = _shot(project.id, shot_id=f"sht_{setup}")
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    if setup == "other-project":
        _seed_voice(other.id, asset_id="voi_bad")
    elif setup == "wrong-kind":
        _seed_voice(project.id, asset_id="voi_bad", kind="actors")
    elif setup == "not-ready":
        _seed_voice(project.id, asset_id="voi_bad", ready=False)
    else:
        _seed_voice(project.id, asset_id="voi_bad", duration_s=15.1)

    response = client.patch(
        f"/api/shots/{shot.id}",
        json={"voice_refs": [{"asset_id": "voi_bad", "audio_index": 1}]},
    )

    assert response.status_code == 400
    assert detail in response.json()["detail"].lower()


def test_submit_stages_voice_reference_in_audio_order(
    client,
    voice_ref_env,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import projects as projects_api

    project = create_project("P", "Mia: Go now.")
    _seed_actor(project.id)
    _seed_voice(project.id, asset_id="voi_mia")
    voice_ref = ShotVoiceRef(asset_id="voi_mia", audio_index=1, speaker="Mia")
    shot = _shot(project.id, shot_id="sht_submit_voice").model_copy(
        update={
            "status": ShotStatus.approved,
            "refs": [
                ShotRef(
                    role=RefRole.actor,
                    asset_id="act_mia",
                    picture_index=1,
                    file_key="master",
                )
            ],
            "voice_refs": [voice_ref],
            "prompt_sections": _full_voice_prompt(),
            "dialogue": ["Go now."],
            "meta": {"prompt_voice_signature": projects_api._voice_signature([voice_ref])},
        }
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)
    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 200, response.text
    assert started[0]["job"].params["audio_keys"] == ["voice_audio_1"]
    assert started[0]["images"]["voice_audio_1"][0] == "reference.wav"
    assert started[0]["images"]["voice_audio_1"][1] == b"RIFF-reference"


def test_submit_rejects_native_audio_instead_of_using_private_lock(
    client,
    voice_ref_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.api import projects as projects_api

    project = create_project("P", "Mia: Go now.")
    _seed_actor(project.id)
    _seed_voice(project.id, asset_id="voi_mia")
    native = tmp_path / "exact.wav"
    native.write_bytes(b"RIFF-native")
    voice_ref = ShotVoiceRef(asset_id="voi_mia", audio_index=1, speaker="Mia")
    shot = _shot(project.id, shot_id="sht_native_over_voice").model_copy(
        update={
            "status": ShotStatus.approved,
            "refs": [ShotRef(role=RefRole.actor, asset_id="act_mia", picture_index=1)],
            "voice_refs": [voice_ref],
            "prompt_sections": PromptSections(
                subject_definitions="Mia",
                summary="Mia speaks.",
                retention_analysis="Hold attention.",
                detailed_description="Mia says: Go now.",
                overall_soundscape="Exact source audio.",
                non_diegetic_music="None.",
            ),
            "dialogue": ["Go now."],
            "source_audio_path": str(native),
            "meta": {"prompt_voice_signature": projects_api._voice_signature([voice_ref])},
        }
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)
    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 400, response.text
    assert "official H3" in response.text
    assert "locked source audio" in response.text
    assert started == []
