"""Projects/Shots HTTP API: dual human gates + H3 submit."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.core.projects.models import (
    Project,
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
    ShotStatus,
)
from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from app.core.schemas import LibraryAsset
from app.core.projects.chat_history import load_chat_history
from app.core.vram import GenerationActiveError, GenerationReservation


class _LockedChatOrchestrator:
    def __init__(self) -> None:
        self.reservations = [
            GenerationReservation(
                job_id="job_locked",
                pipeline_id="ref_frame",
                kind="image",
                status="queued",
                phase="queued",
                queued_at="2026-08-31T10:00:00+00:00",
            )
        ]
        self.ollama_calls: list[str] = []

    async def generation_reservations(self):
        return list(self.reservations)

    class _Session:
        def __init__(self, parent):
            self.parent = parent

        async def __aenter__(self):
            raise GenerationActiveError(self.parent.reservations)

        async def __aexit__(self, *args):
            return None

    def llm_session(self, **kwargs):
        return self._Session(self)

    async def ensure_llm_ready(self, **kwargs):
        self.ollama_calls.append("warm")


def _full_prompt(*, layout_picture_index: int | None = None) -> PromptSections:
    subject = "subject"
    if layout_picture_index is not None:
        subject += (
            f"; <Picture {layout_picture_index}> controls the shot composition"
        )
    return PromptSections(
        subject_definitions=subject,
        summary="summary",
        retention_analysis="retention",
        detailed_description="detailed",
        overall_soundscape="sound",
        non_diegetic_music="music",
    )


def _fresh_layout_prompt_meta(shot: Shot) -> dict:
    from app.core.projects.layouts import (
        layout_prompt_signature,
        selected_layout_prompt_context,
    )

    context = selected_layout_prompt_context(shot)
    asset_ids = [str(item["asset_id"]) for item in context]
    return {
        "prompt_layout_asset_ids": asset_ids,
        "prompt_layout_asset_id": asset_ids[0] if asset_ids else "",
        "prompt_layout_signature": layout_prompt_signature(shot),
    }


def _seed_layout(library_root: Path, asset_id: str = "lay_testlayout01") -> LibraryAsset:
    adir = library_root / "layouts" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "layout.png").write_bytes(b"fake-layout-png" * 200)  # >2KB for resolve
    asset = LibraryAsset(
        id=asset_id,
        kind="layouts",
        name="Layout",
        notes="",
        pipeline_id="ref_frame",
        job_id="job_seed",
        created_at="2026-01-01T00:00:00+00:00",
        files={"layout": "layout.png"},
        meta={"review_status": "pending_review"},
    )
    (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    return asset


def _seed_actor(library_root: Path, asset_id: str = "act_testactor01") -> LibraryAsset:
    adir = library_root / "actors" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "master.png").write_bytes(b"fake-actor-png" * 200)  # >2KB for resolve
    asset = LibraryAsset(
        id=asset_id,
        kind="actors",
        name="Actor",
        notes="",
        pipeline_id="actor",
        job_id="job_seed",
        created_at="2026-01-01T00:00:00+00:00",
        files={"master": "master.png"},
        meta={},
    )
    (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    return asset


def _seed_image_reference(
    library_root: Path,
    *,
    kind: str,
    asset_id: str,
    file_key: str,
) -> LibraryAsset:
    adir = library_root / kind / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    filename = f"{file_key}.png"
    (adir / filename).write_bytes(b"reference-image" * 200)
    asset = LibraryAsset(
        id=asset_id,
        kind=kind,
        name=asset_id,
        notes="",
        pipeline_id="seed",
        job_id="job_seed",
        created_at="2026-01-01T00:00:00+00:00",
        files={file_key: filename},
        meta={},
    )
    (adir / "asset.json").write_text(
        asset.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return asset


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return {"projects": projects, "jobs": jobs, "library": library}


@pytest.fixture
def client(api_env, monkeypatch):
    """FastAPI TestClient with DirectorService that never calls real Ollama/Comfy."""
    from app.agents.director.service import DirectorService
    from app.main import create_app

    class FakePlanProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def complete(
            self,
            system: str,
            user: str,
            *,
            guides: Iterable[str] = (),
        ) -> str:
            self.calls.append((system, user))
            raise AssertionError("plan provider must not be called in cold-path tests")

    class SilentOrch:
        async def release_llm(self) -> None:
            return None

        async def ensure_llm_ready(self) -> None:
            return None

        class _Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        def llm_session(self, *, release_on_exit: bool = True):
            return self._Sess()

    provider = FakePlanProvider()
    svc = DirectorService(plan_provider=provider, orchestrator=SilentOrch())

    async def fake_start(job, *, images=None):
        return job

    import app.agents.director.service as service_mod
    import app.api.projects as projects_api
    from app.core.jobs import runner as jobs_runner

    monkeypatch.setattr(service_mod, "start_pipeline_job", fake_start)
    monkeypatch.setattr(jobs_runner, "start_pipeline_job", fake_start)
    monkeypatch.setattr(projects_api, "start_pipeline_job", fake_start)

    app = create_app()
    app.state.director_service = svc
    app.state.plan_provider = provider
    with TestClient(app) as c:
        yield c


def test_create_and_list_projects(client):
    r = client.post("/api/projects", json={"name": "Demo", "script_text": "INT. CAFE"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Demo"
    assert body["script_text"] == "INT. CAFE"
    assert body["id"].startswith("prj_")

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    items = listed.json()
    assert any(p["id"] == body["id"] for p in items)

    got = client.get(f"/api/projects/{body['id']}")
    assert got.status_code == 200
    assert got.json()["project"]["id"] == body["id"]
    assert "shots" in got.json()


@pytest.mark.parametrize("path_suffix", ["/chat", "/chat/stream"])
def test_generation_lock_rejects_text_chat_before_history_write(
    client,
    monkeypatch,
    path_suffix,
):
    project = client.post(
        "/api/projects",
        json={"name": "Locked chat", "script_text": "INT. ROOM - DAY"},
    ).json()
    orch = _LockedChatOrchestrator()
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: orch)

    response = client.post(
        f"/api/projects/{project['id']}{path_suffix}",
        json={"message": "Change the shot", "history": []},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GPU_GENERATION_ACTIVE"
    assert load_chat_history(project["id"]) == []
    assert orch.ollama_calls == []


def test_generation_lock_rejects_image_chat_before_upload_persistence(
    client,
    api_env,
    monkeypatch,
):
    project = client.post(
        "/api/projects",
        json={"name": "Locked image chat", "script_text": "INT. ROOM - DAY"},
    ).json()
    orch = _LockedChatOrchestrator()
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: orch)
    png = io.BytesIO()
    Image.new("RGB", (8, 8), color=(20, 40, 60)).save(png, format="PNG")

    response = client.post(
        f"/api/projects/{project['id']}/chat/stream/images",
        data={"message": "Inspect this", "history": "[]"},
        files=[("images", ("reference.png", png.getvalue(), "image/png"))],
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GPU_GENERATION_ACTIVE"
    assert load_chat_history(project["id"]) == []
    assert not (
        api_env["projects"] / project["id"] / "agent" / "chat_uploads"
    ).exists()


def test_replace_shot_materials_updates_picture_refs_and_preserves_layout_history(
    client,
    api_env,
):
    actor = _seed_actor(api_env["library"], "act_material_actor")
    scene = _seed_image_reference(
        api_env["library"],
        kind="scenes",
        asset_id="scn_material_scene",
        file_key="wide",
    )
    old_layout = _seed_layout(api_env["library"], "lay_material_old")
    new_layout = _seed_layout(api_env["library"], "lay_material_new")
    project = create_project("Material editor", "The Agent waits.")
    shot = Shot(
        id="sht_material_editor",
        project_id=project.id,
        scene_id="sc01",
        title="Waiting room",
        script_beat="The Agent waits alone.",
        duration_s=6,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
            ),
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id=old_layout.id,
                file_key="layout",
                picture_index=2,
            ),
        ],
        layout_refs=[
            LayoutReference(
                id="lref_material_old",
                asset_id=old_layout.id,
                purpose="old composition",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            )
        ],
        meta={
            "prompt_picture_signature": "fresh-before-edit",
            "prompt_layout_signature": "fresh-before-edit",
            "layout_visual_analyses": {
                old_layout.id: {"analysis": "cached old composition"}
            },
        },
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.put(
        f"/api/shots/{shot.id}/materials",
        json={
            "materials": [
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "wide",
                },
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                },
                {
                    "role": "layout_ref_frame",
                    "asset_id": new_layout.id,
                    "file_key": "layout",
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [
        (ref["role"], ref["asset_id"], ref["picture_index"])
        for ref in body["refs"]
    ] == [
        ("scene", scene.id, 1),
        ("actor", actor.id, 2),
        ("layout_ref_frame", new_layout.id, 3),
    ]
    layouts = {layout["asset_id"]: layout for layout in body["layout_refs"]}
    assert layouts[old_layout.id]["selected_for_h3"] is False
    assert layouts[new_layout.id]["selected_for_h3"] is True
    assert layouts[new_layout.id]["purpose"] == new_layout.name
    assert body["meta"]["prompt_picture_signature"] == ""
    assert body["meta"]["prompt_layout_signature"] == ""
    assert body["meta"]["material_review_pending"] is True
    assert body["meta"]["material_changes"] == {
        "added": [
            {
                "role": "scene",
                "asset_id": scene.id,
                "file_key": "wide",
                "picture_index": 1,
            },
            {
                "role": "layout_ref_frame",
                "asset_id": new_layout.id,
                "file_key": "layout",
                "picture_index": 3,
            },
        ],
        "removed": [
            {
                "role": "layout_ref_frame",
                "asset_id": old_layout.id,
                "file_key": "layout",
                "picture_index": 2,
            }
        ],
        "reordered": [
            {
                "role": "actor",
                "asset_id": actor.id,
                "file_key": "master",
                "from_picture_index": 1,
                "to_picture_index": 2,
            }
        ],
    }
    assert old_layout.id in body["meta"]["layout_visual_analyses"]
    assert new_layout.id not in body["meta"]["layout_visual_analyses"]


def test_replace_shot_materials_can_remove_the_last_selected_layout(
    client,
    api_env,
):
    actor = _seed_actor(api_env["library"], "act_material_keep")
    layout = _seed_layout(api_env["library"], "lay_material_remove")
    project = create_project("Remove material Layout", "The Agent waits.")
    shot = Shot(
        id="sht_material_remove_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Waiting room",
        script_beat="The Agent waits alone.",
        duration_s=6,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
            ),
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id=layout.id,
                file_key="layout",
                picture_index=2,
            ),
        ],
        layout_refs=[
            LayoutReference(
                id="lref_material_remove",
                asset_id=layout.id,
                purpose="old composition",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            )
        ],
        layout_asset_id=layout.id,
        layout_review_status=LayoutReviewStatus.usable,
        ref_frame_job_id="job_material_remove",
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.put(
        f"/api/shots/{shot.id}/materials",
        json={
            "materials": [
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(ref["role"], ref["asset_id"]) for ref in body["refs"]] == [
        ("actor", actor.id)
    ]
    assert body["layout_refs"][0]["selected_for_h3"] is False
    assert body["layout_asset_id"] is None
    assert body["layout_review_status"] is None
    assert body["ref_frame_job_id"] is None
    assert body["meta"]["material_review_pending"] is True
    assert body["meta"]["material_changes"]["removed"] == [
        {
            "role": "layout_ref_frame",
            "asset_id": layout.id,
            "file_key": "layout",
            "picture_index": 2,
        }
    ]


def test_replace_shot_materials_rewrites_prompt_from_the_persisted_new_refs(
    client,
    api_env,
):
    actor = _seed_actor(api_env["library"], "act_material_rewrite")
    scene = _seed_image_reference(
        api_env["library"],
        kind="scenes",
        asset_id="scn_material_rewrite",
        file_key="wide",
    )
    project = create_project("Material prompt rewrite", "The Agent enters.")
    shot = Shot(
        id="sht_material_rewrite",
        project_id=project.id,
        scene_id="sc01",
        title="New room",
        script_beat="The Agent enters the new room.",
        duration_s=6,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
            )
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class PromptRewriteRecorder:
        def __init__(self) -> None:
            self.refs: list[tuple[str, str, str]] = []

        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            current = load_shot(project.id, shot_id)
            assert current is not None
            self.refs = [
                (ref.role.value, ref.asset_id, str(ref.file_key or ""))
                for ref in current.refs
            ]
            meta = dict(current.meta or {})
            meta["material_review_pending"] = False
            updated = current.model_copy(
                update={"prompt_sections": _full_prompt(), "meta": meta}
            )
            save_shot(updated)
            return updated

    recorder = PromptRewriteRecorder()
    client.app.state.director_service = recorder

    response = client.put(
        f"/api/shots/{shot.id}/materials?rewrite_prompt=true",
        json={
            "materials": [
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "wide",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert recorder.refs == [("scene", scene.id, "wide")]
    assert response.json()["prompt_sections"]["summary"] == "summary"
    assert response.json()["meta"]["material_review_pending"] is False


def test_replace_shot_materials_reports_prompt_failure_after_preserving_new_refs(
    client,
    api_env,
):
    scene = _seed_image_reference(
        api_env["library"],
        kind="scenes",
        asset_id="scn_material_rewrite_failure",
        file_key="wide",
    )
    project = create_project("Material prompt failure", "The room changes.")
    shot = Shot(
        id="sht_material_rewrite_failure",
        project_id=project.id,
        scene_id="sc01",
        title="Changed room",
        script_beat="The room changes.",
        duration_s=6,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class FailingPromptRewrite:
        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            raise RuntimeError("LLM unavailable")

    client.app.state.director_service = FailingPromptRewrite()

    response = client.put(
        f"/api/shots/{shot.id}/materials?rewrite_prompt=true",
        json={
            "materials": [
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "wide",
                }
            ]
        },
    )

    assert response.status_code == 503
    assert "Materials saved but prompt rewrite failed" in response.json()["detail"]
    persisted = load_shot(project.id, shot.id)
    assert persisted is not None
    assert [(ref.role.value, ref.asset_id) for ref in persisted.refs] == [
        ("scene", scene.id)
    ]
    assert persisted.meta["material_review_pending"] is True


def test_update_library_metadata_persists_human_notes_and_invalidates_referencing_shot(
    client,
    api_env,
):
    actor = _seed_actor(api_env["library"], "act_metadata_editor")
    project = create_project("Metadata editor", "Mia waits.")
    referenced = Shot(
        id="sht_metadata_referenced",
        project_id=project.id,
        scene_id="sc01",
        title="Waiting",
        script_beat="Mia waits.",
        duration_s=5,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
            )
        ],
        meta={
            "prompt_picture_signature": "fresh-picture-signature",
            "prompt_layout_signature": "fresh-layout-signature",
        },
    )
    untouched = Shot(
        id="sht_metadata_unreferenced",
        project_id=project.id,
        scene_id="sc01",
        title="Empty room",
        script_beat="The room is empty.",
        duration_s=5,
        meta={"prompt_picture_signature": "keep-this-signature"},
    )
    save_shot(referenced)
    save_shot(untouched)
    save_project(
        project.model_copy(update={"shot_ids": [referenced.id, untouched.id]})
    )

    response = client.patch(
        f"/api/library/actors/{actor.id}",
        json={"name": "  Mia close-up  ", "notes": "  Warm cyan smile.  "},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Mia close-up"
    assert body["notes"] == "Warm cyan smile."
    assert body["meta"]["description"] == "Warm cyan smile."

    stored_asset = client.get(f"/api/library/actors/{actor.id}").json()
    assert stored_asset["name"] == "Mia close-up"
    assert stored_asset["notes"] == "Warm cyan smile."
    assert stored_asset["files"] == {"master": "master.png"}

    project_body = client.get(f"/api/projects/{project.id}").json()
    shots = {shot["id"]: shot for shot in project_body["shots"]}
    changed = shots[referenced.id]
    assert changed["meta"]["prompt_picture_signature"] == ""
    assert changed["meta"]["prompt_layout_signature"] == ""
    assert changed["meta"]["material_review_pending"] is True
    assert changed["meta"]["material_changes"]["metadata_updated"] == [
        {
            "kind": "actors",
            "asset_id": actor.id,
            "name": "Mia close-up",
            "notes": "Warm cyan smile.",
        }
    ]
    assert (
        shots[untouched.id]["meta"]["prompt_picture_signature"]
        == "keep-this-signature"
    )


def test_update_library_metadata_rejects_blank_name(client, api_env):
    actor = _seed_actor(api_env["library"], "act_metadata_blank")

    response = client.patch(
        f"/api/library/actors/{actor.id}",
        json={"name": "   ", "notes": "Still valid notes"},
    )

    assert response.status_code == 400
    stored = client.get(f"/api/library/actors/{actor.id}").json()
    assert stored["name"] == "Actor"
    assert stored["notes"] == ""


def test_submit_rejects_materials_that_have_not_been_reviewed(
    client,
    api_env,
    monkeypatch,
):
    actor = _seed_actor(api_env["library"], "act_pending_review")
    project = create_project("Pending material review", "The Agent waits.")
    shot = Shot(
        id="sht_pending_material_review",
        project_id=project.id,
        scene_id="sc01",
        title="Waiting",
        script_beat="The Agent waits.",
        duration_s=5,
        status=ShotStatus.succeeded,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
            )
        ],
        prompt_sections=_full_prompt(),
        h3_job_id="job_previous_success",
        meta={
            "prompt_picture_signature": "",
            "material_review_pending": True,
            "material_changes": {"added": [], "removed": [], "reordered": []},
        },
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    started: list[object] = []

    async def capture_start(job, *, images=None):
        started.append(job)
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)

    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "material_review_required"
    assert response.json()["detail"]["shot_id"] == shot.id
    assert started == []


def test_replace_shot_materials_rejects_more_than_nine_pictures(client, api_env):
    project = create_project("Material limit", "Nine Pictures maximum.")
    shot = Shot(
        id="sht_material_limit",
        project_id=project.id,
        scene_id="sc01",
        title="Limit",
        script_beat="Hold.",
        duration_s=3,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    materials = []
    for index in range(10):
        asset = _seed_actor(api_env["library"], f"act_limit_{index}")
        materials.append(
            {"role": "actor", "asset_id": asset.id, "file_key": "master"}
        )

    response = client.put(
        f"/api/shots/{shot.id}/materials",
        json={"materials": materials},
    )

    assert response.status_code == 422


def test_delete_layout_removes_shot_binding_and_generated_asset(client, api_env):
    project = create_project("Delete Layout", "A door opens.")
    asset = _seed_layout(api_env["library"], "lay_delete_me")
    shot = Shot(
        id="sht_delete_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Delete this composition",
        script_beat="The door opens.",
        duration_s=6,
        status=ShotStatus.needs_review,
        layout_asset_id=asset.id,
        layout_review_status="pending_review",
        layout_refs=[
            LayoutReference(
                id="lref_delete_me",
                asset_id=asset.id,
                job_id="job_done",
                job_status="succeeded",
                review_status=LayoutReviewStatus.pending_review,
                selected_for_h3=False,
            )
        ],
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id=asset.id,
                picture_index=1,
                file_key="layout",
            )
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.delete(
        f"/api/shots/{shot.id}/layouts/lref_delete_me"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["layout_refs"] == []
    assert body["refs"] == []
    assert body["layout_asset_id"] is None
    assert body["layout_review_status"] is None
    assert not (api_env["library"] / "layouts" / asset.id).exists()


def test_delete_current_layout_keeps_history_without_reactivating_it(client, api_env):
    project = create_project("Delete current Layout", "A door opens.")
    previous = _seed_layout(api_env["library"], "lay_previous_history")
    current = _seed_layout(api_env["library"], "lay_current_delete")
    shot = Shot(
        id="sht_delete_current_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Drop the current composition",
        script_beat="The door opens.",
        duration_s=6,
        status=ShotStatus.needs_review,
        layout_asset_id=current.id,
        layout_review_status="pending_review",
        layout_refs=[
            LayoutReference(
                id="lref_history",
                asset_id=previous.id,
                job_status="succeeded",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=False,
            ),
            LayoutReference(
                id="lref_current",
                asset_id=current.id,
                job_status="succeeded",
                review_status=LayoutReviewStatus.pending_review,
                selected_for_h3=True,
            ),
        ],
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id=current.id,
                picture_index=1,
                file_key="layout",
            )
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.delete(
        f"/api/shots/{shot.id}/layouts/lref_current"
    )

    assert response.status_code == 200
    body = response.json()
    assert [layout["asset_id"] for layout in body["layout_refs"]] == [previous.id]
    assert body["layout_refs"][0]["selected_for_h3"] is False
    assert body["layout_asset_id"] is None
    assert body["refs"] == []
    assert (api_env["library"] / "layouts" / previous.id).exists()
    assert not (api_env["library"] / "layouts" / current.id).exists()


def test_delete_layout_preserves_asset_referenced_by_another_project(client, api_env):
    shared = _seed_layout(api_env["library"], "lay_shared_projects")
    first = create_project("First project", "A door opens.")
    second = create_project("Second project", "The same doorway returns.")
    first_shot = Shot(
        id="sht_shared_first",
        project_id=first.id,
        scene_id="sc01",
        title="First use",
        script_beat="The door opens.",
        duration_s=5,
        layout_asset_id=shared.id,
        layout_refs=[
            LayoutReference(
                id="lref_shared_first",
                asset_id=shared.id,
                selected_for_h3=True,
            )
        ],
    )
    second_shot = Shot(
        id="sht_shared_second",
        project_id=second.id,
        scene_id="sc01",
        title="Second use",
        script_beat="The doorway returns.",
        duration_s=5,
        layout_asset_id=shared.id,
        layout_refs=[
            LayoutReference(
                id="lref_shared_second",
                asset_id=shared.id,
                selected_for_h3=True,
            )
        ],
    )
    save_shot(first_shot)
    save_shot(second_shot)
    save_project(first.model_copy(update={"shot_ids": [first_shot.id]}))
    save_project(second.model_copy(update={"shot_ids": [second_shot.id]}))

    response = client.delete(
        f"/api/shots/{first_shot.id}/layouts/lref_shared_first"
    )

    assert response.status_code == 200
    assert (api_env["library"] / "layouts" / shared.id).exists()


def test_director_chat_history_is_persisted_and_reloaded(client, monkeypatch):
    created = client.post(
        "/api/projects",
        json={"name": "Persistent chat", "script_text": "INT. STUDIO - NIGHT"},
    ).json()
    project_id = created["id"]

    first = client.post(
        f"/api/projects/{project_id}/chat",
        json={"message": "status"},
    )
    assert first.status_code == 200

    captured: dict[str, object] = {}

    async def fake_handle_chat(*, project_id, message, history, **kwargs):
        from app.agents.director.chat import ChatResult
        from app.core.projects.store import load_project

        captured["history"] = history
        return ChatResult(reply="History restored.", project=load_project(project_id))

    import app.agents.director.chat as chat_module

    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)
    second = client.post(
        f"/api/projects/{project_id}/chat",
        json={
            "message": "What did we discuss?",
            "history": [{"role": "user", "content": "client-only history"}],
        },
    )
    assert second.status_code == 200
    assert captured["history"] == [
        {"role": "user", "content": "status"},
        {"role": "assistant", "content": first.json()["reply"]},
    ]

    history = client.get(f"/api/projects/{project_id}/chat/history")
    assert history.status_code == 200
    assert [(item["role"], item["content"]) for item in history.json()] == [
        ("user", "status"),
        ("assistant", first.json()["reply"]),
        ("user", "What did we discuss?"),
        ("assistant", "History restored."),
    ]
    assert all(item["id"] and item["created_at"] for item in history.json())


def test_director_chat_image_upload_is_persisted_and_forwarded(client, monkeypatch):
    created = client.post(
        "/api/projects",
        json={"name": "Visual chat", "script_text": "INT. STUDIO - NIGHT"},
    ).json()
    project_id = created["id"]
    captured: dict[str, object] = {}

    async def fake_handle_chat(*, project_id, message, **kwargs):
        from app.agents.director.chat import ChatResult
        from app.core.projects.store import load_project

        captured["message"] = message
        captured["images"] = kwargs.get("user_images_b64")
        captured["captions"] = kwargs.get("user_image_captions")
        return ChatResult(reply="I can see the frame.", project=load_project(project_id))

    import app.agents.director.chat as chat_module

    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)
    png = io.BytesIO()
    Image.new("RGB", (8, 8), color=(20, 40, 60)).save(png, format="PNG")

    response = client.post(
        f"/api/projects/{project_id}/chat/stream/images",
        data={"message": "What do you notice?", "history": "[]"},
        files=[("images", ("reference.png", png.getvalue(), "image/png"))],
    )

    assert response.status_code == 200
    assert '"type": "result"' in response.text
    assert captured["message"] == "What do you notice?"
    assert len(captured["images"]) == 1
    assert captured["captions"] == ["reference.png"]

    history = client.get(f"/api/projects/{project_id}/chat/history").json()
    assert history[0]["role"] == "user"
    assert history[0]["images"][0]["caption"] == "reference.png"
    image_url = history[0]["images"][0]["url"]
    served = client.get(image_url)
    assert served.status_code == 200
    assert served.content == png.getvalue()


def test_director_chat_image_upload_rejects_more_than_four_images(client):
    created = client.post(
        "/api/projects",
        json={"name": "Visual chat limit", "script_text": "INT. STUDIO - NIGHT"},
    ).json()
    project_id = created["id"]
    png = io.BytesIO()
    Image.new("RGB", (2, 2)).save(png, format="PNG")
    files = [
        ("images", (f"reference-{index}.png", png.getvalue(), "image/png"))
        for index in range(5)
    ]

    response = client.post(
        f"/api/projects/{project_id}/chat/stream/images",
        data={"message": "Compare these", "history": "[]"},
        files=files,
    )

    assert response.status_code == 400
    assert "at most 4 images" in response.json()["detail"]


def test_create_json_project(client):
    response = client.post("/api/projects", json={
        "name": "External board", "script_text": "", "mode": "json_production"
    })
    assert response.status_code == 200
    assert response.json()["mode"] == "json_production"


def test_legacy_project_json_defaults_script_lock_to_false():
    project = Project.model_validate(
        {
            "id": "prj_legacy",
            "name": "Legacy",
            "script_text": "INT. ROOM - NIGHT",
            "created_at": "2026-08-26T00:00:00+00:00",
            "updated_at": "2026-08-26T00:00:00+00:00",
            "shot_ids": [],
        }
    )

    assert project.script_locked is False


def test_locked_project_requires_explicit_unlock_before_script_change(client):
    created = client.post(
        "/api/projects",
        json={"name": "Approved", "script_text": "INT. ROOM - NIGHT"},
    ).json()
    project_id = created["id"]

    locked = client.patch(
        f"/api/projects/{project_id}",
        json={"script_locked": True},
    )
    assert locked.status_code == 200
    assert locked.json()["script_locked"] is True
    assert locked.json()["script_text"] == "INT. ROOM - NIGHT"

    rejected = client.patch(
        f"/api/projects/{project_id}",
        json={"script_text": "EXT. STREET - DAY"},
    )
    assert rejected.status_code == 409
    assert "unlock" in rejected.json()["detail"].lower()
    still_locked = client.get(f"/api/projects/{project_id}").json()["project"]
    assert still_locked["script_locked"] is True
    assert still_locked["script_text"] == "INT. ROOM - NIGHT"

    changed = client.patch(
        f"/api/projects/{project_id}",
        json={
            "script_locked": False,
            "script_text": "EXT. STREET - DAY",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["script_locked"] is False
    assert changed.json()["script_text"] == "EXT. STREET - DAY"


def test_previously_unlocked_project_can_change_script(client):
    created = client.post(
        "/api/projects",
        json={"name": "Approved", "script_text": "INT. ROOM - NIGHT"},
    ).json()
    project_id = created["id"]
    assert client.patch(
        f"/api/projects/{project_id}", json={"script_locked": True}
    ).status_code == 200
    unlocked = client.patch(
        f"/api/projects/{project_id}", json={"script_locked": False}
    )
    assert unlocked.status_code == 200

    changed = client.patch(
        f"/api/projects/{project_id}",
        json={"script_text": "EXT. STREET - DAY"},
    )

    assert changed.status_code == 200
    assert changed.json()["script_text"] == "EXT. STREET - DAY"


@pytest.mark.asyncio
async def test_ollama_plan_provider_forwards_requested_guides(monkeypatch):
    from app.api import projects as projects_api

    captured: list[tuple[str, tuple[str, ...]]] = []

    def capture_skill(task: str, *, guides=()):
        captured.append((task, tuple(guides)))
        return "COMPOSED PROMPT"

    class _Client:
        async def generate(self, model: str, prompt: str) -> str:
            assert model == "qwen-test"
            assert prompt == "COMPOSED PROMPT"
            return "ok"

    monkeypatch.setattr(projects_api, "with_director_skill", capture_skill)
    provider = projects_api.OllamaPlanProvider(model="qwen-test")
    provider.client = _Client()

    result = await provider.complete(
        "PLAN SYSTEM",
        "PLAN USER",
        guides=("script-planning",),
    )

    assert result == "ok"
    assert captured == [
        ("PLAN SYSTEM\n\nPLAN USER", ("script-planning",))
    ]


def test_queue_layout_rejects_four_source_images(client):
    project = create_project("Too many Layout sources", "script")
    shot = Shot(
        id="sht_layout_four_refs",
        project_id=project.id,
        scene_id="sc01",
        title="Four refs",
        script_beat="beat",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.post(
        f"/api/shots/{shot.id}/layouts",
        json={
            "purpose": "over-packed",
            "source_refs": [
                {"role": "actor", "asset_id": f"act_{index}"}
                for index in range(4)
            ],
        },
    )

    assert response.status_code == 422
    assert "at most 3 source images" in response.text


def test_submit_blocked_when_no_refs(client, api_env):
    project = create_project("P", "script")
    shot = Shot(
        id="sht_layoutblock1",
        project_id=project.id,
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.approved,
        refs=[],
        prompt_sections=_full_prompt(),
        dialogue=[],
        layout_review_status="pending_review",
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    r = client.post(f"/api/shots/{shot.id}/submit")
    assert r.status_code == 400
    assert "ref" in r.json()["detail"].lower()


def test_import_external_and_insert_layout(client, api_env):
    project = create_project("P", "script")
    # External actor import
    r = client.post(
        "/api/library/import",
        data={
            "kind": "actors",
            "name": "Xiao Qian",
            "notes": "male lead earnest",
            "project_id": project.id,
        },
        files={"file": ("小欠.png", b"fake-actor-png" * 400, "image/png")},
    )
    assert r.status_code == 200, r.text
    actor = r.json()
    assert actor["pipeline_id"] == "external"
    assert actor["name"] == "Xiao Qian"
    assert actor["project_id"] == project.id
    assert actor["meta"].get("source_filename")

    shot = Shot(
        id="sht_insertlayout1",
        project_id=project.id,
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            ShotRef(role=RefRole.actor, asset_id=actor["id"], picture_index=1),
        ],
        prompt_sections=_full_prompt(),
        dialogue=[],
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    r2 = client.post(
        f"/api/shots/{shot.id}/layout/insert",
        data={"name": "shot layout", "approve": "true"},
        files={"file": ("layout.png", b"fake-layout-png" * 400, "image/png")},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["layout_asset_id"]
    assert body["layout_review_status"] == "approved"
    assert body["status"] == ShotStatus.needs_review.value
    actor = next(ref for ref in body["refs"] if ref["role"] == RefRole.actor.value)
    layout = next(
        ref for ref in body["refs"] if ref["role"] == RefRole.layout_ref_frame.value
    )
    assert actor["picture_index"] == 1
    assert layout["picture_index"] == 2
    assert len(body["layout_refs"]) == 1
    inserted = body["layout_refs"][0]
    assert inserted["asset_id"] == body["layout_asset_id"]
    assert inserted["review_status"] == LayoutReviewStatus.usable.value
    assert inserted["selected_for_h3"] is True


def test_insert_unapproved_layout_becomes_current_without_an_approval_gate(
    client,
    api_env,
):
    project = create_project("Pending insert", "script")
    shot = Shot(
        id="sht_insert_pending",
        project_id=project.id,
        scene_id="sc01",
        title="Pending layout",
        script_beat="Hold for review.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    response = client.post(
        f"/api/shots/{shot.id}/layout/insert",
        data={"name": "pending still", "approve": "false"},
        files={"file": ("pending.png", b"pending-layout" * 400, "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["layout_review_status"] == "pending_review"
    assert len(body["layout_refs"]) == 1
    inserted = body["layout_refs"][0]
    assert inserted["asset_id"] == body["layout_asset_id"]
    assert inserted["review_status"] == LayoutReviewStatus.pending_review.value
    assert inserted["selected_for_h3"] is True
    assert any(
        ref["role"] == RefRole.layout_ref_frame.value for ref in body["refs"]
    )
    from app.core.projects.store import load_shot

    persisted = load_shot(project.id, shot.id)
    assert persisted is not None
    assert persisted.layout_refs[0].asset_id == body["layout_asset_id"]
    assert persisted.layout_refs[0].selected_for_h3 is True


@pytest.mark.asyncio
async def test_inserted_approved_layout_survives_prompt_write_and_h3_submit(
    client,
    api_env,
    monkeypatch,
):
    _seed_actor(api_env["library"])
    project = create_project("Inserted Layout H3", "Chen crosses the doorway.")
    shot = Shot(
        id="sht_insert_h3_flow",
        project_id=project.id,
        scene_id="sc01",
        title="Door crossing",
        script_beat="Chen crosses the doorway.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_testactor01",
                picture_index=1,
            )
        ],
        prompt_sections=_full_prompt(),
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    inserted_response = client.post(
        f"/api/shots/{shot.id}/layout/insert",
        data={"name": "approved still", "approve": "true"},
        files={"file": ("approved.png", b"approved-layout" * 400, "image/png")},
    )
    assert inserted_response.status_code == 200, inserted_response.text
    inserted_asset_id = inserted_response.json()["layout_asset_id"]

    provider = client.app.state.plan_provider

    async def return_inserted_layout_prompt(
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        provider.calls.append((system, user))
        return __import__("json").dumps(
            {
                "subject_definitions": (
                    "<Picture 2> controls the inserted doorway composition."
                ),
                "summary": "One continuous doorway scene.",
                "retention_analysis": "Retain Chen and the inserted geography.",
                "detailed_description": "From 0-5 seconds, Chen crosses the doorway.",
                "overall_soundscape": "Footsteps and room tone.",
                "non_diegetic_music": "No music.",
            }
        )

    monkeypatch.setattr(provider, "complete", return_inserted_layout_prompt)
    written = await client.app.state.director_service.write_prompts_after_layout(
        shot.id
    )
    assert [(ref.asset_id, ref.picture_index) for ref in written.refs] == [
        ("act_testactor01", 1),
        (inserted_asset_id, 2),
    ]
    assert written.meta["prompt_layout_asset_ids"] == [inserted_asset_id]

    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)
    submit_response = client.post(f"/api/shots/{shot.id}/submit")

    assert submit_response.status_code == 200, submit_response.text
    assert len(provider.calls) == 1
    assert len(started) == 1
    job = started[0]["job"]
    assert job.params["layout_asset_ids"] == [inserted_asset_id]
    assert job.params["layout_picture_indices"] == [2]
    assert list(started[0]["images"]) == ["ref_0", "ref_1"]


def test_approve_layout_does_not_need_ollama(client, api_env, monkeypatch):
    _seed_layout(api_env["library"])
    project = create_project("P", "script")
    shot = Shot(
        id="sht_approve00001",
        project_id=project.id,
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[],
        prompt_sections=_full_prompt(),
        dialogue=[],
        layout_asset_id="lay_testlayout01",
        layout_review_status="pending_review",
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    generate_calls: list[str] = []

    async def boom_generate(self, model: str, prompt: str) -> str:
        generate_calls.append(model)
        raise AssertionError("approve must not call Ollama generate")

    monkeypatch.setattr(
        "app.core.vram.ollama_client.OllamaClient.generate",
        boom_generate,
    )

    # Default rewrite_prompt=false — cold path
    r = client.post(f"/api/shots/{shot.id}/ref-frame/approve")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == ShotStatus.needs_review.value
    assert body["layout_review_status"] == "approved"
    assert any(
        ref["role"] == RefRole.layout_ref_frame.value and ref["picture_index"] == 1
        for ref in body["refs"]
    )
    assert generate_calls == []

    # Library meta updated
    asset_path = api_env["library"] / "layouts" / "lay_testlayout01" / "asset.json"
    assert asset_path.exists()
    assert "approved" in asset_path.read_text(encoding="utf-8")


def test_reject_layout_with_feedback(client, api_env):
    project = create_project("P", "script")
    shot = Shot(
        id="sht_reject000001",
        project_id=project.id,
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[],
        prompt_sections=_full_prompt(),
        dialogue=[],
        layout_asset_id="lay_x",
        layout_review_status="pending_review",
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    r = client.post(
        f"/api/shots/{shot.id}/ref-frame/reject",
        json={"feedback": "too dark"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["layout_review_status"] == "rejected"
    assert body["feedback"] == "too dark"
    assert body["status"] == ShotStatus.ref_frame_pending.value


def test_legacy_reject_updates_asset_for_mirrored_layout_not_fallback_sibling(
    client, api_env
):
    _seed_layout(api_env["library"], "lay_pending_sibling")
    _seed_layout(api_env["library"], "lay_selected_compat")
    project = create_project("Legacy mirrored reject", "script")
    shot = Shot(
        id="sht_legacy_mirror_reject",
        project_id=project.id,
        scene_id="sc01",
        title="Two Layouts",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_selected_compat",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lr_pending",
                asset_id="lay_pending_sibling",
                review_status=LayoutReviewStatus.pending_review,
            ),
            LayoutReference(
                id="lr_selected",
                asset_id="lay_selected_compat",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.post(
        f"/api/shots/{shot.id}/ref-frame/reject",
        json={"feedback": "selected composition is wrong"},
    )

    assert response.status_code == 200, response.text
    selected_meta = (
        api_env["library"]
        / "layouts"
        / "lay_selected_compat"
        / "asset.json"
    ).read_text(encoding="utf-8")
    sibling_meta = (
        api_env["library"]
        / "layouts"
        / "lay_pending_sibling"
        / "asset.json"
    ).read_text(encoding="utf-8")
    assert '"review_status": "rejected"' in selected_meta
    assert '"review_status": "pending_review"' in sibling_meta


def test_review_and_selection_endpoints_update_only_requested_layout(client, api_env):
    project = create_project("Multiple Layout review", "script")
    shot = Shot(
        id="sht_review_multi",
        project_id=project.id,
        scene_id="sc01",
        title="Doorway",
        script_beat="Chen enters",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="empty doorway",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lr_after",
                asset_id="lay_after",
                purpose="post-entry blocking",
                review_status=LayoutReviewStatus.pending_review,
                selected_for_h3=True,
            ),
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    response = client.post(
        f"/api/shots/{shot.id}/layouts/lr_after/review",
        json={"status": "reject", "feedback": "actor appears too early"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["layout_refs"][0]["review_status"] == "usable"
    assert body["layout_refs"][0]["selected_for_h3"] is True
    assert body["layout_refs"][1]["review_status"] == "reject"
    assert body["layout_refs"][1]["selected_for_h3"] is False

    rejected_selection = client.post(
        f"/api/shots/{shot.id}/layouts/lr_after/selection",
        json={"selected_for_h3": True},
    )
    assert rejected_selection.status_code == 400
    assert "rejected Layout cannot be selected" in rejected_selection.text


def test_repair_selection_requires_review_endpoint_human_override(client, api_env):
    project = create_project("Repair override", "script")
    shot = Shot(
        id="sht_repair_override",
        project_id=project.id,
        scene_id="sc01",
        title="Close-up",
        script_beat="Lu grips the recorder",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_refs=[
            LayoutReference(
                id="lr_hands",
                asset_id="lay_hands",
                purpose="prop grip",
                review_status=LayoutReviewStatus.pending_review,
            )
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    reviewed = client.post(
        f"/api/shots/{shot.id}/layouts/lr_hands/review",
        json={
            "status": "usable_with_repair",
            "feedback": "minor hand artifact",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["layout_refs"][0]["selected_for_h3"] is True

    denied = client.post(
        f"/api/shots/{shot.id}/layouts/lr_hands/selection",
        json={"selected_for_h3": True},
    )
    assert denied.status_code == 400
    assert "human override" in denied.text

    overridden = client.post(
        f"/api/shots/{shot.id}/layouts/lr_hands/review",
        json={
            "status": "usable_with_repair",
            "feedback": "human accepts for this run",
            "human_override": True,
        },
    )
    assert overridden.status_code == 200, overridden.text
    override_body = overridden.json()
    assert override_body["layout_refs"][0]["review_status"] == "usable_with_repair"
    assert override_body["layout_refs"][0]["selected_for_h3"] is True
    assert override_body["meta"]["layout_human_overrides"]["lr_hands"] is True

    selected = client.post(
        f"/api/shots/{shot.id}/layouts/lr_hands/selection",
        json={"selected_for_h3": True},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["layout_refs"][0]["selected_for_h3"] is True


@pytest.mark.asyncio
async def test_make_chat_fn_forwards_stage_guides_to_director_skill(monkeypatch):
    from app.api import projects as projects_api

    captured: list[tuple[str, ...]] = []

    def capture_skill(task: str, *, guides=()):
        captured.append(tuple(guides))
        return task

    class FakeOllama:
        async def chat(self, model, user, *, system, images):
            return "ok"

        async def generate(self, model, prompt):
            return "ok"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeOrchestrator:
        ollama = FakeOllama()

        def llm_session(self, **kwargs):
            return FakeSession()

        async def ensure_llm_ready(self, **kwargs):
            return None

    monkeypatch.setattr(projects_api, "with_director_skill", capture_skill)
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(
        "app.core.vram.director_model.get_director_model", lambda: "vision-model"
    )

    chat_fn = await projects_api._make_chat_fn()
    await chat_fn(
        "SYSTEM",
        "Review the Layout",
        images=["image-data"],
        guides=("visual-qc",),
    )
    await chat_fn("SYSTEM", "Ordinary chat")

    assert captured == [("visual-qc",), ()]


@pytest.mark.asyncio
async def test_make_chat_fn_falls_back_to_text_tool_protocol_on_ollama_xml_error(
    monkeypatch,
):
    from app.api import projects as projects_api

    generated_prompts: list[str] = []

    class FakeOllama:
        async def chat_response(self, *args, **kwargs):
            raise RuntimeError(
                "XML syntax error on line 8: element <function> closed by </parameter>"
            )

        async def generate(self, model, prompt, **kwargs):
            generated_prompts.append(prompt)
            return '```json\n{"tool":"get_status","params":{}}\n```'

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeOrchestrator:
        ollama = FakeOllama()

        def llm_session(self, **kwargs):
            return FakeSession()

        async def ensure_llm_ready(self, **kwargs):
            return None

    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(
        "app.core.vram.director_model.get_director_model", lambda: "qwen-test"
    )

    chat_fn = await projects_api._make_chat_fn()
    result = await chat_fn(
        "SYSTEM",
        "Use the status tool",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_status",
                    "description": "Read status",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert result == '```json\n{"tool":"get_status","params":{}}\n```'
    assert len(generated_prompts) == 1
    assert '"name": "get_status"' in generated_prompts[0]
    assert '"tool":"tool_name","params"' in generated_prompts[0]


@pytest.mark.asyncio
async def test_make_chat_fn_forces_single_gpt_tool_through_structured_output(
    monkeypatch,
):
    from app.api import projects as projects_api

    captured: list[dict] = []

    class FakeOllama:
        async def chat_response(self, *args, **kwargs):
            captured.append(kwargs)
            return {
                "content": (
                    '{"tool":"queue_gpt_ref_frame","params":'
                    '{"shot_id":"shot_1","purpose":"door reveal",'
                    '"state_description":"the door is open","source_refs":'
                    '[{"role":"scene","asset_id":"scene_1"}],'
                    '"generation_prompt":"Image1 controls the set. Return one image."}}'
                ),
                "thinking": "",
                "tool_calls": [],
            }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeOrchestrator:
        ollama = FakeOllama()

        def llm_session(self, **kwargs):
            return FakeSession()

        async def ensure_llm_ready(self, **kwargs):
            return None

    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(
        "app.core.vram.director_model.get_director_model", lambda: "qwen-test"
    )

    gpt_tool = {
        "type": "function",
        "function": {
            "name": "queue_gpt_ref_frame",
            "description": "Generate one GPT Layout",
            "parameters": {
                "type": "object",
                "properties": {
                    "shot_id": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["shot_id", "purpose"],
            },
        },
    }
    chat_fn = await projects_api._make_chat_fn()
    result = await chat_fn("SYSTEM", "Use GPT for shot_1", tools=[gpt_tool])

    assert result["content"].startswith('{"tool":"queue_gpt_ref_frame"')
    assert len(captured) == 1
    assert captured[0].get("tools") is None
    assert captured[0]["format"] == {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "const": "queue_gpt_ref_frame"},
            "params": gpt_tool["function"]["parameters"],
        },
        "required": ["tool", "params"],
        "additionalProperties": False,
    }
    assert "Return exactly one JSON object" in captured[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_make_chat_fn_forces_single_actor_design_tool_through_structured_output(
    monkeypatch,
):
    from app.api import projects as projects_api

    captured: list[dict] = []

    class FakeOllama:
        async def chat_response(self, *args, **kwargs):
            captured.append(kwargs)
            return {
                "content": (
                    '{"tool":"queue_actor_design","params":'
                    '{"name":"Mara","description":"detective",'
                    '"generation_prompt":"Create one character image."}}'
                ),
                "thinking": "",
                "tool_calls": [],
            }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeOrchestrator:
        ollama = FakeOllama()

        def llm_session(self, **kwargs):
            return FakeSession()

        async def ensure_llm_ready(self, **kwargs):
            return None

    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(
        "app.core.vram.director_model.get_director_model", lambda: "qwen-test"
    )
    actor_tool = {
        "type": "function",
        "function": {
            "name": "queue_actor_design",
            "description": "Generate an Actor design",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }

    chat_fn = await projects_api._make_chat_fn()
    result = await chat_fn("SYSTEM", "生成人物设定", tools=[actor_tool])

    assert result["content"].startswith('{"tool":"queue_actor_design"')
    assert captured[0]["tools"] is None
    assert captured[0]["format"]["properties"]["tool"]["const"] == "queue_actor_design"


def test_approve_shot_and_submit_h3(client, api_env, monkeypatch):
    _seed_layout(api_env["library"])
    _seed_actor(api_env["library"])
    project = create_project("P", "script")
    shot = Shot(
        id="sht_submit000001",
        project_id=project.id,
        scene_id="sc01",
        title="Cafe",
        script_beat="walk in",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_testlayout01",
                picture_index=1,
            ),
            ShotRef(role=RefRole.actor, asset_id="act_testactor01", picture_index=2),
        ],
        prompt_sections=PromptSections(
            subject_definitions=(
                "subject; <Picture 2> controls the shot composition"
            ),
            summary="summary",
            retention_analysis="retention",
            detailed_description='Actor says "Hello" and walks in.',
            overall_soundscape="sound",
            non_diegetic_music="music",
        ),
        dialogue=["Hello"],
        layout_asset_id="lay_testlayout01",
        layout_review_status="approved",
    )
    shot = shot.model_copy(update={"meta": _fresh_layout_prompt_meta(shot)})
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    r = client.post(f"/api/shots/{shot.id}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == ShotStatus.approved.value

    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)

    monkeypatch.setattr(projects_api.settings, "h3_minimax_api_key", None)
    missing_key = client.post(
        f"/api/shots/{shot.id}/submit",
        json={"h3_provider": "minimax"},
    )
    assert missing_key.status_code == 400, missing_key.text
    assert "API key is not configured" in missing_key.text
    assert started == []

    monkeypatch.setattr(projects_api.settings, "h3_minimax_api_key", "test-key")

    r2 = client.post(
        f"/api/shots/{shot.id}/submit",
        json={"h3_provider": "minimax", "width": 1280, "height": 704},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == ShotStatus.queued.value
    assert body["h3_job_id"]
    assert len(started) == 1
    assert started[0]["job"].pipeline_id == "h3_ref2va"
    assert started[0]["job"].params["h3_provider"] == "minimax"
    assert started[0]["job"].params["width"] == 1280
    assert started[0]["job"].params["height"] == 704
    assert started[0]["images"]
    keys = list(started[0]["images"].keys())
    assert keys[0].startswith("ref_")


def test_h3_provider_status_reports_api_availability(client, monkeypatch):
    monkeypatch.setattr(settings, "h3_provider", "local")
    monkeypatch.setattr(settings, "h3_minimax_api_key", "configured-key")
    monkeypatch.setattr(settings, "h3_minimax_resolution", "768P")

    response = client.get("/api/h3-ref2va/provider")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "default_provider": "local",
        "minimax_configured": True,
        "minimax_resolution": "768P",
    }


def test_submit_rejects_prompt_picture_tag_without_a_matching_shot_ref(
    client,
    api_env,
    monkeypatch,
):
    _seed_actor(api_env["library"])
    project = create_project("Invalid Picture binding", "An actor waits.")
    shot = Shot(
        id="sht_invalid_picture",
        project_id=project.id,
        scene_id="sc01",
        title="Wait",
        script_beat="The actor waits.",
        duration_s=5.0,
        status=ShotStatus.approved,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_testactor01",
                picture_index=1,
            )
        ],
        prompt_sections=PromptSections(
            subject_definitions=(
                "<Picture 1> controls identity. "
                "<Picture 2> controls the room geography."
            ),
            summary="The actor waits.",
            retention_analysis="Retain identity.",
            detailed_description="From 0-5 seconds, the actor waits.",
            overall_soundscape="Quiet room tone.",
            non_diegetic_music="No music.",
        ),
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    async def fail_if_started(job, *, images=None):
        raise AssertionError("an invalid Picture tag must fail before job start")

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", fail_if_started)

    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 400, response.text
    assert "unsubmitted Picture" in response.text
    assert "<Picture 2>" in response.text


def test_submit_h3_rejects_locked_source_audio_for_official_providers(
    client, api_env, monkeypatch, tmp_path
):
    _seed_layout(api_env["library"])
    audio_path = tmp_path / "shot01.wav"
    audio_path.write_bytes(b"RIFF-test-audio")
    project = create_project("Vertical MV", "成片9:16，原曲音频锁定。")
    shot = Shot(
        id="sht_nativeaudio01",
        project_id=project.id,
        scene_id="sc01",
        title="Song",
        script_beat="sing",
        duration_s=9.06,
        status=ShotStatus.approved,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_testlayout01",
                picture_index=1,
            )
        ],
        prompt_sections=_full_prompt(layout_picture_index=1),
        layout_asset_id="lay_testlayout01",
        layout_review_status="approved",
        source_audio_path=str(audio_path),
    )
    shot = shot.model_copy(update={"meta": _fresh_layout_prompt_meta(shot)})
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)
    monkeypatch.setattr(projects_api.settings, "h3_provider", "minimax")
    monkeypatch.setattr(projects_api.settings, "h3_minimax_api_key", "test-key")
    cloud_response = client.post(f"/api/shots/{shot.id}/submit")

    assert cloud_response.status_code == 400, cloud_response.text
    assert "locked source audio" in cloud_response.text
    assert started == []

    monkeypatch.setattr(projects_api.settings, "h3_provider", "local")
    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 400, response.text
    assert "official H3" in response.text
    assert "locked source audio" in response.text
    assert started == []


def test_submit_refreshes_prompt_when_layout_provenance_is_stale(
    client, api_env, monkeypatch
):
    _seed_layout(api_env["library"])
    project = create_project("P", "script")
    shot = Shot(
        id="sht_staleprompt01",
        project_id=project.id,
        scene_id="sc01",
        title="Cafe",
        script_beat="walk in",
        duration_s=8.0,
        status=ShotStatus.approved,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_testlayout01",
                picture_index=1,
            )
        ],
        prompt_sections=_full_prompt(),
        dialogue=[],
        layout_asset_id="lay_testlayout01",
        layout_review_status="approved",
        meta={"prompt_layout_asset_id": "lay_previous"},
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    fresh_sections = {
        "subject_definitions": (
            "fresh subject; <Picture 1> controls the current composition"
        ),
        "summary": "fresh layout-aware summary",
        "retention_analysis": "retain Picture 1",
        "detailed_description": "fresh action",
        "overall_soundscape": "fresh sound",
        "non_diegetic_music": "fresh music",
    }
    provider = client.app.state.plan_provider

    async def return_fresh_sections(
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        import json

        assert tuple(guides) == ("h3-prompt-writing",)
        return json.dumps(fresh_sections)

    monkeypatch.setattr(provider, "complete", return_fresh_sections)

    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)

    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["prompt_layout_asset_id"] == "lay_testlayout01"
    assert len(started) == 1
    assert "fresh layout-aware summary" in started[0]["job"].params["prompt"]
    assert "\nsummary\n" not in started[0]["job"].params["prompt"]


def test_submit_refreshes_prompt_when_picture_materials_changed(
    client,
    api_env,
    monkeypatch,
):
    actor = _seed_actor(api_env["library"], "act_changed_material")
    project = create_project("Changed materials", "The Agent waits.")
    shot = Shot(
        id="sht_changed_material",
        project_id=project.id,
        scene_id="sc01",
        title="Waiting",
        script_beat="The Agent waits.",
        duration_s=5,
        status=ShotStatus.approved,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
            )
        ],
        prompt_sections=_full_prompt(),
        meta={"prompt_picture_signature": "signature-before-material-edit"},
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    provider = client.app.state.plan_provider

    async def return_material_prompt(
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        return __import__("json").dumps(
            {
                "subject_definitions": "<Picture 1> defines the selected Agent.",
                "summary": "fresh material-aware summary",
                "retention_analysis": "Retain the selected Agent metadata.",
                "detailed_description": "From 0-5 seconds, the Agent waits.",
                "overall_soundscape": "Quiet room tone.",
                "non_diegetic_music": "No music.",
            }
        )

    monkeypatch.setattr(provider, "complete", return_material_prompt)
    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)

    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["prompt_picture_signature"] != "signature-before-material-edit"
    assert "fresh material-aware summary" in started[0]["job"].params["prompt"]


def test_submit_syncs_explicit_multi_layout_set_and_refreshes_stale_signature(
    client, api_env, monkeypatch
):
    _seed_actor(api_env["library"])
    _seed_layout(api_env["library"], "lay_before")
    _seed_layout(api_env["library"], "lay_after")
    project = create_project("Multi Layout Submit", "Chen enters.")
    shot = Shot(
        id="sht_submit_multi",
        project_id=project.id,
        scene_id="sc01",
        title="Door crossing",
        script_beat="Chen crosses the glass doorway.",
        duration_s=6.0,
        status=ShotStatus.approved,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_testactor01",
                picture_index=1,
            )
        ],
        layout_asset_id="lay_before",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="before entry",
                state_description="doorway empty",
                time_hint="before Chen enters",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lr_after",
                asset_id="lay_after",
                purpose="post-entry blocking",
                state_description="Chen outside glass",
                time_hint="after Chen enters",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
        ],
        prompt_sections=_full_prompt(),
        meta={"prompt_layout_signature": "stale"},
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    provider = client.app.state.plan_provider

    async def return_layout_grounded_sections(
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        provider.calls.append((system, user))
        return __import__("json").dumps(
            {
                "subject_definitions": (
                    "<Picture 2> controls empty-doorway geography; "
                    "<Picture 3> controls the compatible two-person blocking."
                ),
                "summary": "Compatible states of one continuous doorway scene.",
                "retention_analysis": "Retain the glass geometry and Chen's identity.",
                "detailed_description": "From 0-6 seconds, Chen enters.",
                "overall_soundscape": "Footsteps and room tone.",
                "non_diegetic_music": "No music.",
            }
        )

    monkeypatch.setattr(provider, "complete", return_layout_grounded_sections)
    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)

    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(ref["asset_id"], ref["picture_index"]) for ref in body["refs"]] == [
        ("act_testactor01", 1),
        ("lay_before", 2),
        ("lay_after", 3),
    ]
    assert body["meta"]["prompt_layout_asset_ids"] == [
        "lay_before",
        "lay_after",
    ]
    assert body["meta"]["prompt_layout_asset_id"] == "lay_before"
    assert body["meta"]["prompt_layout_signature"] != "stale"
    assert len(provider.calls) == 1
    assert len(started) == 1
    assert list(started[0]["images"])[:3] == ["ref_0", "ref_1", "ref_2"]


def test_submit_uses_existing_layout_even_when_legacy_selection_is_false(
    client, api_env, monkeypatch
):
    _seed_actor(api_env["library"])
    _seed_layout(api_env["library"], "lay_old")
    project = create_project("Deselected Layout Submit", "Chen remains alone.")
    shot = Shot(
        id="sht_submit_deselected",
        project_id=project.id,
        scene_id="sc01",
        title="Clean composition",
        script_beat="Chen remains alone.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_testactor01",
                picture_index=1,
            ),
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_old",
                picture_index=2,
            ),
        ],
        layout_asset_id="lay_old",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lr_old",
                asset_id="lay_old",
                purpose="obsolete composition",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=False,
            )
        ],
        prompt_sections=_full_prompt(layout_picture_index=2),
        meta={},
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    provider = client.app.state.plan_provider

    async def return_layout_sections(
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        provider.calls.append((system, user))
        return __import__("json").dumps(
            {
                "subject_definitions": (
                    "Chen's identity remains stable. "
                    "<Picture 2> controls the composition."
                ),
                "summary": "Chen remains alone in one continuous shot.",
                "retention_analysis": "Retain Chen's appearance.",
                "detailed_description": "From 0-5 seconds, Chen remains still.",
                "overall_soundscape": "Quiet room tone.",
                "non_diegetic_music": "No music.",
            }
        )

    monkeypatch.setattr(provider, "complete", return_layout_sections)
    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)

    response = client.post(f"/api/shots/{shot.id}/submit")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(ref["asset_id"], ref["picture_index"]) for ref in body["refs"]] == [
        ("act_testactor01", 1),
        ("lay_old", 2),
    ]
    assert body["meta"]["prompt_layout_asset_ids"] == ["lay_old"]
    assert body["meta"]["prompt_layout_asset_id"] == "lay_old"
    assert body["meta"]["prompt_layout_signature"]
    assert len(provider.calls) == 1
    assert "lay_old" in provider.calls[0][1]
    assert list(started[0]["images"]) == ["ref_0", "ref_1"]


@pytest.mark.asyncio
async def test_legacy_deselect_removes_the_current_layout_from_h3(
    client,
    api_env,
    monkeypatch,
):
    from app.agents.director.context_io import (
        load_agent_context,
        save_agent_context,
    )

    _seed_actor(api_env["library"])
    _seed_image_reference(
        api_env["library"],
        kind="scenes",
        asset_id="scn_context_room",
        file_key="plate",
    )
    _seed_image_reference(
        api_env["library"],
        kind="props",
        asset_id="prp_context_recorder",
        file_key="master",
    )
    _seed_layout(api_env["library"], "lay_context_stale")
    project = create_project("Saved context deselection", "Chen waits with a recorder.")
    shot = Shot(
        id="sht_saved_context_deselect",
        project_id=project.id,
        scene_id="sc01",
        title="Room hold",
        script_beat="Chen waits with a recorder.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_testactor01",
                picture_index=1,
            ),
            ShotRef(
                role=RefRole.scene,
                asset_id="scn_context_room",
                picture_index=2,
                file_key="plate",
            ),
            ShotRef(
                role=RefRole.prop,
                asset_id="prp_context_recorder",
                picture_index=3,
                file_key="master",
            ),
        ],
        layout_asset_id="lay_context_stale",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lr_context_stale",
                asset_id="lay_context_stale",
                purpose="room composition",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=False,
            )
        ],
        prompt_sections=_full_prompt(),
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    selected = client.post(
        f"/api/shots/{shot.id}/layouts/lr_context_stale/selection",
        json={"selected_for_h3": True},
    )
    assert selected.status_code == 200, selected.text

    provider = client.app.state.plan_provider

    async def return_sections(
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        provider.calls.append((system, user))
        has_layout = '\"role\": \"layout_ref_frame\"' in user
        subject = "Chen, the room, and recorder remain coherent."
        if has_layout:
            subject += " <Picture 4> controls the room composition."
        return __import__("json").dumps(
            {
                "subject_definitions": subject,
                "summary": "One continuous room hold.",
                "retention_analysis": "Retain identity, room, and recorder.",
                "detailed_description": "From 0-5 seconds, Chen waits.",
                "overall_soundscape": "Quiet room tone.",
                "non_diegetic_music": "No music.",
            }
        )

    monkeypatch.setattr(provider, "complete", return_sections)
    await client.app.state.director_service.write_prompts_after_layout(shot.id)

    saved = load_agent_context(project.id)
    assert saved is not None
    saved_summary = next(
        summary for summary in saved.shot_summaries if summary["id"] == shot.id
    )
    assert "lay_context_stale" in saved_summary["asset_ids"]
    save_agent_context(
        project.id,
        saved.model_copy(
            update={
                "models_used": [*saved.models_used, "historic-model"],
                "extra": {"history": "retain-general-history"},
            }
        ),
    )

    deselected = client.post(
        f"/api/shots/{shot.id}/layouts/lr_context_stale/selection",
        json={"selected_for_h3": False},
    )
    assert deselected.status_code == 200, deselected.text

    started: list[dict] = []

    async def capture_start(job, *, images=None):
        started.append({"job": job, "images": images})
        return job

    import app.api.projects as projects_api

    monkeypatch.setattr(projects_api, "start_pipeline_job", capture_start)
    submitted = client.post(f"/api/shots/{shot.id}/submit")

    assert submitted.status_code == 200, submitted.text
    assert len(provider.calls) == 2
    body = submitted.json()
    assert body["meta"]["prompt_layout_asset_ids"] == []
    assert body["meta"]["prompt_layout_asset_id"] == ""
    assert list(started[0]["images"]) == ["ref_0", "ref_1", "ref_2"]


def test_patch_shot_prompt(client, api_env):
    project = create_project("P", "script")
    shot = Shot(
        id="sht_patch0000001",
        project_id=project.id,
        scene_id="sc01",
        title="t",
        script_beat="beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        refs=[],
        prompt_sections=_full_prompt(),
        dialogue=[],
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)

    r = client.patch(
        f"/api/shots/{shot.id}",
        json={
            "duration_s": 10.0,
            "prompt_sections": {
                "subject_definitions": "new subject",
                "summary": "summary",
                "retention_analysis": "retention",
                "detailed_description": "detailed",
                "overall_soundscape": "sound",
                "non_diegetic_music": "music",
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["duration_s"] == 10.0
    assert body["prompt_sections"]["subject_definitions"] == "new subject"

    got = client.get(f"/api/shots/{shot.id}")
    assert got.status_code == 200
    assert got.json()["id"] == shot.id


def test_get_shot_404(client):
    r = client.get("/api/shots/sht_missing00000")
    assert r.status_code == 404
