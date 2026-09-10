from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.jobs.store import list_jobs
from app.core.projects.store import project_dir

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"json-h3-png"
WAV_BYTES = b"RIFF" + b"\x00" * 12 + b"WAVE"

VALID_DOCUMENT = {
    "version": 1,
    "revision": 99,
    "aspect_ratio": "16:9",
    "shots": [
        {
            "id": "shot_001",
            "title": "Corridor entry",
            "script_beat": "Lu enters the municipal archive corridor.",
            "duration_s": 6,
            "dialogue": [],
            "pictures": [
                {
                    "index": 1,
                    "role": "actor",
                    "label": "Lu identity and navy wardrobe",
                },
                {
                    "index": 2,
                    "role": "layout",
                    "label": "Post-entry blocking and corridor geography",
                },
            ],
            "audio": [],
            "prompt": {
                "subject_definitions": "<Picture 1> defines Lu's identity and wardrobe.",
                "summary": "<Picture 2> establishes the corridor composition.",
                "retention_analysis": "Hold attention through the doorway reveal.",
                "detailed_description": "0–6 seconds: Lu enters and stops at the desk.",
                "overall_soundscape": "Quiet rain and fluorescent hum.",
                "non_diegetic_music": "No non-diegetic music.",
            },
        }
    ],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _create_project(client: TestClient, *, mode: str) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"{mode} board", "script_text": "", "mode": mode},
    )
    assert response.status_code == 200
    return response.json()


def test_director_project_get_and_put_return_409(client):
    project = _create_project(client, mode="director")
    project_id = project["id"]

    got = client.get(f"/api/projects/{project_id}/production-storyboard")
    assert got.status_code == 409

    put = client.put(
        f"/api/projects/{project_id}/production-storyboard",
        json=VALID_DOCUMENT,
    )
    assert put.status_code == 409


def test_new_json_project_get_returns_empty_document(client):
    project = _create_project(client, mode="json_production")

    response = client.get(
        f"/api/projects/{project['id']}/production-storyboard"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["revision"] == 0
    assert body["shots"] == []


def test_valid_put_increments_revision_server_side(client):
    project = _create_project(client, mode="json_production")
    project_id = project["id"]

    response = client.put(
        f"/api/projects/{project_id}/production-storyboard",
        json=VALID_DOCUMENT,
    )
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    assert response.json()["shots"][0]["id"] == "shot_001"

    # Caller revision is ignored; next save is current + 1.
    again = client.put(
        f"/api/projects/{project_id}/production-storyboard",
        json=VALID_DOCUMENT,
    )
    assert again.status_code == 200
    assert again.json()["revision"] == 2


def test_invalid_put_returns_422_without_changing_document(client):
    project = _create_project(client, mode="json_production")
    project_id = project["id"]

    saved = client.put(
        f"/api/projects/{project_id}/production-storyboard",
        json=VALID_DOCUMENT,
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    invalid = copy.deepcopy(VALID_DOCUMENT)
    invalid["shots"][0]["pictures"] = []

    rejected = client.put(
        f"/api/projects/{project_id}/production-storyboard",
        json=invalid,
    )
    assert rejected.status_code == 422

    current = client.get(f"/api/projects/{project_id}/production-storyboard")
    assert current.status_code == 200
    assert current.json()["revision"] == 1
    assert current.json()["shots"][0]["id"] == "shot_001"


def _one_picture_document(**prompt_overrides) -> dict:
    document = copy.deepcopy(VALID_DOCUMENT)
    shot = document["shots"][0]
    shot["pictures"] = [shot["pictures"][0]]
    shot["prompt"]["summary"] = "Lu enters the municipal archive corridor."
    shot["prompt"].update(prompt_overrides)
    return document


def _put_storyboard(client: TestClient, project_id: str, document: dict) -> dict:
    response = client.put(
        f"/api/projects/{project_id}/production-storyboard",
        json=document,
    )
    assert response.status_code == 200
    return response.json()


def _submit_shot(
    client: TestClient,
    project_id: str,
    shot_id: str,
    *,
    revision: int,
    pictures: list[tuple[str, bytes, str]] | None = None,
    audios: list[tuple[str, bytes, str]] | None = None,
    h3_provider: str | None = None,
):
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for name, data, mime in pictures or []:
        files.append(("pictures", (name, data, mime)))
    for name, data, mime in audios or []:
        files.append(("audios", (name, data, mime)))
    form = {"revision": str(revision)}
    if h3_provider is not None:
        form["h3_provider"] = h3_provider
    return client.post(
        f"/api/projects/{project_id}/production-storyboard/shots/{shot_id}/submit",
        data=form,
        files=files or None,
    )


def _h3_jobs(*, project_id: str | None = None):
    return list_jobs(limit=50, pipeline_id="h3_ref2va", project_id=project_id)


@pytest.fixture
def capture_pipeline_start(monkeypatch):
    captured: dict = {"job": None, "images": None, "calls": 0}

    async def fake_start(job, *, images=None):
        captured["job"] = job
        captured["images"] = images or {}
        captured["calls"] += 1
        return job

    monkeypatch.setattr(
        "app.core.jobs.runner.start_pipeline_job",
        fake_start,
    )
    monkeypatch.setattr(
        "app.api.json_production.start_pipeline_job",
        fake_start,
        raising=False,
    )
    return captured


def test_submit_director_project_returns_409_without_job(client, capture_pipeline_start):
    project = _create_project(client, mode="director")
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=1,
        pictures=[("lu.png", PNG_BYTES, "image/png")],
    )
    assert response.status_code == 409
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_missing_pictures(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_wrong_picture_count(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], VALID_DOCUMENT)
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", PNG_BYTES, "image/png")],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_undeclared_audio_file(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", PNG_BYTES, "image/png")],
        audios=[("voice.wav", WAV_BYTES, "audio/wav")],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_stale_revision(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    _put_storyboard(client, project["id"], _one_picture_document())
    saved = _put_storyboard(client, project["id"], _one_picture_document())
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"] - 1,
        pictures=[("lu.png", PNG_BYTES, "image/png")],
    )
    assert response.status_code == 409
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_unsupported_image_extension(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.gif", b"GIF89a" + b"x" * 16, "image/gif")],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_oversized_input(client, capture_pipeline_start, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())
    oversized = b"x" * (settings.max_upload_mb * 1024 * 1024 + 1)
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", oversized, "image/png")],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_undeclared_prompt_tags(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    document = _one_picture_document(
        detailed_description=(
            "0–6 seconds: Lu enters. <Picture 2> must not appear."
        ),
    )
    saved = _put_storyboard(client, project["id"], document)
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", PNG_BYTES, "image/png")],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_submit_rejects_missing_declared_tags(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    document = copy.deepcopy(VALID_DOCUMENT)
    document["shots"][0]["prompt"]["summary"] = "Lu enters the corridor."
    saved = _put_storyboard(client, project["id"], document)
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[
            ("lu.png", PNG_BYTES, "image/png"),
            ("layout.png", PNG_BYTES, "image/png"),
        ],
    )
    assert response.status_code == 400
    assert capture_pipeline_start["calls"] == 0
    assert _h3_jobs(project_id=project["id"]) == []


def test_valid_submit_creates_h3_job_without_library_assets(
    client, capture_pipeline_start
):
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())
    uploaded_png_bytes = PNG_BYTES
    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", uploaded_png_bytes, "image/png")],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_id"] == "h3_ref2va"
    assert body["project_id"] == project["id"]
    assert body["json_shot_id"] == "shot_001"
    assert body["json_storyboard_revision"] == saved["revision"]

    captured_job = capture_pipeline_start["job"]
    captured_images = capture_pipeline_start["images"]
    assert captured_job.pipeline_id == "h3_ref2va"
    assert captured_job.params["json_shot_id"] == "shot_001"
    assert captured_job.params["h3_provider"] == settings.h3_provider
    assert captured_job.params["image_keys"] == ["ref_0"]
    assert captured_job.params["ref_roles"] == ["actor"]
    assert captured_images["ref_0"][1] == uploaded_png_bytes
    assert list((project_dir(project["id"]) / "library").rglob("asset.json")) == []


def test_submit_uses_requested_minimax_provider(
    client, capture_pipeline_start, monkeypatch
):
    monkeypatch.setattr(settings, "h3_provider", "local")
    monkeypatch.setattr(settings, "h3_minimax_api_key", "test-key")
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())

    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", PNG_BYTES, "image/png")],
        h3_provider="minimax",
    )

    assert response.status_code == 200
    assert response.json()["h3_provider"] == "minimax"
    assert capture_pipeline_start["job"].params["h3_provider"] == "minimax"


def test_submit_rejects_unconfigured_minimax_provider(
    client, capture_pipeline_start, monkeypatch
):
    monkeypatch.setattr(settings, "h3_minimax_api_key", None)
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())

    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", PNG_BYTES, "image/png")],
        h3_provider="minimax",
    )

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]
    assert capture_pipeline_start["calls"] == 0


def test_submit_rejects_unknown_h3_provider(client, capture_pipeline_start):
    project = _create_project(client, mode="json_production")
    saved = _put_storyboard(client, project["id"], _one_picture_document())

    response = _submit_shot(
        client,
        project["id"],
        "shot_001",
        revision=saved["revision"],
        pictures=[("lu.png", PNG_BYTES, "image/png")],
        h3_provider="other",
    )

    assert response.status_code == 400
    assert "Unsupported H3 provider" in response.json()["detail"]
    assert capture_pipeline_start["calls"] == 0


def test_list_h3_jobs_filters_project_and_json_shot_id(client):
    from app.core.jobs.store import create_job

    create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="keep-me",
        project_id="prj_json_a",
        params={"json_shot_id": "shot_001"},
    )
    create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="other-shot",
        project_id="prj_json_a",
        params={"json_shot_id": "shot_002"},
    )
    create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="other-project",
        project_id="prj_json_b",
        params={"json_shot_id": "shot_001"},
    )

    response = client.get(
        "/api/h3-ref2va/jobs",
        params={"project_id": "prj_json_a", "json_shot_id": "shot_001"},
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["keep-me"]
    assert body[0]["project_id"] == "prj_json_a"
    assert body[0]["json_shot_id"] == "shot_001"


def test_list_h3_jobs_filters_json_storyboard_revision(client):
    from app.core.jobs.store import create_job

    for revision in (1, 2):
        create_job(
            pipeline_id="h3_ref2va",
            asset_kind="productions",
            name=f"revision-{revision}",
            project_id="prj_json_revision",
            params={
                "json_shot_id": "shot_001",
                "json_storyboard_revision": revision,
            },
        )

    response = client.get(
        "/api/h3-ref2va/jobs",
        params={
            "project_id": "prj_json_revision",
            "json_shot_id": "shot_001",
            "json_storyboard_revision": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["revision-2"]
    assert body[0]["json_storyboard_revision"] == 2


def test_list_h3_jobs_json_shot_id_survives_limit(client):
    from app.core.jobs.store import create_job, list_jobs as list_stored_jobs

    project_id = "prj_json_limit"
    for index in range(4):
        create_job(
            pipeline_id="h3_ref2va",
            asset_kind="productions",
            name=f"decoy-{index}",
            project_id=project_id,
            params={"json_shot_id": f"shot_decoy_{index}"},
        )

    ordered = list_stored_jobs(
        limit=None, pipeline_id="h3_ref2va", project_id=project_id
    )
    assert len(ordered) == 4
    target = ordered[-1]
    target_shot_id = target.params["json_shot_id"]

    unfiltered = client.get(
        "/api/h3-ref2va/jobs",
        params={"project_id": project_id, "limit": 2},
    )
    assert unfiltered.status_code == 200
    unfiltered_ids = [item["json_shot_id"] for item in unfiltered.json()]
    assert target_shot_id not in unfiltered_ids

    filtered = client.get(
        "/api/h3-ref2va/jobs",
        params={
            "project_id": project_id,
            "json_shot_id": target_shot_id,
            "limit": 2,
        },
    )
    assert filtered.status_code == 200
    body = filtered.json()
    assert [item["json_shot_id"] for item in body] == [target_shot_id]
    assert body[0]["id"] == target.id
