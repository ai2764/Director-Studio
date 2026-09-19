"""Save-to-Library must be visible in the project Library without a global copy."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.core.jobs.store import create_job, save_job, save_output_file
from app.core.projects.store import create_project
from app.core.schemas import JobStatus, OutputSlot
from app.main import create_app


def _succeeded_actor_job(project_id: str):
    job = create_job(
        pipeline_id="actor",
        asset_kind="actors",
        name="Mara",
        notes="Lead",
        project_id=project_id,
    )
    png = b"\x89PNG\r\n\x1a\n" + b"fake-actor-bytes"
    path = save_output_file(
        job.id, "fullbody_threeview", "fullbody_threeview.png", png, project_id=project_id
    )
    job.status = JobStatus.succeeded
    job.outputs = {
        "fullbody_threeview": OutputSlot(
            key="fullbody_threeview",
            label="Full-body",
            path=str(path),
            filename=path.name,
        )
    }
    save_job(job)
    return job, png


def test_save_actor_to_project_library_lists_and_serves_without_global_copy():
    project = create_project("Library mismatch", "Mara walks the corridor.")
    job, png = _succeeded_actor_job(project.id)

    client = TestClient(create_app())
    saved = client.post(
        f"/api/actors/jobs/{job.id}/save",
        json={"name": "Mara", "notes": "Lead", "project_id": project.id},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    asset_id = body["id"]
    assert body["project_id"] == project.id

    project_file = (
        settings.projects_dir
        / project.id
        / "library"
        / "actors"
        / asset_id
        / "fullbody_threeview.png"
    )
    global_file = (
        settings.library_root / "actors" / asset_id / "fullbody_threeview.png"
    )
    assert project_file.is_file()
    assert not global_file.exists()

    listed = client.get(
        "/api/library",
        params={"kind": "actors", "project_id": project.id},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()
    assert [item["id"] for item in items] == [asset_id]
    assert items[0]["urls"]["fullbody_threeview"] == (
        f"/api/files/library/actors/{asset_id}/fullbody_threeview.png"
    )

    served = client.get(f"/api/files/library/actors/{asset_id}/fullbody_threeview.png")
    assert served.status_code == 200, served.text
    assert served.content == png
