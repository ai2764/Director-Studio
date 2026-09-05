"""Behavior and compatibility contracts for Director domain-service seams."""

from app.agents.director import service
from app.core.projects.layouts import LayoutReference
from app.core.projects.models import Project, RefRole, Shot, ShotStatus
from app.core.schemas import LibraryAsset
from app.agents.director.planner import AssetMatchDraft, ShotDraft


def _asset(
    *,
    kind: str,
    files: dict[str, str | None],
    asset_id: str = "asset-1",
) -> LibraryAsset:
    return LibraryAsset(
        id=asset_id,
        kind=kind,
        name="Asset",
        pipeline_id="external",
        job_id="job-1",
        created_at="2026-08-28T00:00:00Z",
        files=files,
    )


def test_asset_catalog_owns_hash_and_file_selection_with_facade_compatibility():
    from app.agents.director import asset_catalog

    actor = _asset(
        kind="actors",
        files={"master": "master.png", "fullbody_threeview": "three.png"},
    )
    scene = _asset(
        kind="scenes",
        files={"master": "master.png", "angle_00": "angle.png"},
    )

    assert asset_catalog._script_hash("abc") == "ba7816bf8f01cfea"
    assert asset_catalog._default_file_key(RefRole.actor, actor) == "fullbody_threeview"
    assert asset_catalog._default_file_key(RefRole.scene, scene) == "angle_00"
    assert service._script_hash is asset_catalog._script_hash
    assert service._inventory is asset_catalog._inventory
    assert service._asset_index is asset_catalog._asset_index
    assert service._default_file_key is asset_catalog._default_file_key
    assert service._repair_unique_file_key_typo is asset_catalog._repair_unique_file_key_typo
    assert service._read_asset_image_bytes is asset_catalog._read_asset_image_bytes


def test_casting_service_materializes_explicit_actor_and_scene_bindings():
    from app.agents.director import casting_service

    actor = _asset(
        kind="actors",
        files={"fullbody_threeview": "actor.png"},
        asset_id="actor-1",
    )
    scene = _asset(
        kind="scenes",
        files={"angle_00": "scene.png"},
        asset_id="scene-1",
    )
    inventory = [
        {"id": actor.id, "kind": actor.kind, "h3_ready": None},
        {"id": scene.id, "kind": scene.kind, "h3_ready": None},
    ]
    draft = ShotDraft(
        shot_type="medium shot",
        camera_angle="eye level",
        camera_motion="locked-off",
        composition="actor and entrance readable in one frame",
        scene_id="scene-1",
        title="Entrance",
        script_beat="The actor enters the room.",
        duration_s=5.0,
        asset_matches=[
            AssetMatchDraft(role="actor", asset_id=actor.id),
            AssetMatchDraft(role="scene", asset_id=scene.id),
        ],
    )

    shot = casting_service._shot_from_draft(
        "project-1",
        draft,
        inventory=inventory,
        index={actor.id: actor, scene.id: scene},
    )

    assert shot.status == ShotStatus.ref_frame_pending
    assert [(ref.role, ref.asset_id, ref.file_key) for ref in shot.refs] == [
        (RefRole.actor, actor.id, "fullbody_threeview"),
        (RefRole.scene, scene.id, "angle_00"),
    ]


def test_reference_service_owns_brief_builders_with_facade_compatibility():
    from app.agents.director import reference_service

    project = Project(
        id="project-1",
        name="Project",
        script_text="A door opens.",
        created_at="2026-08-28T00:00:00Z",
        updated_at="2026-08-28T00:00:00Z",
    )
    shot = Shot(
        id="shot-1",
        project_id=project.id,
        scene_id="scene-1",
        title="Door",
        script_beat="The actor opens the door.",
        duration_s=4.0,
    )
    layout = LayoutReference(
        id="layout-ref-1",
        asset_id="layout-asset-1",
        purpose="continuity",
        state_description="Door half open",
    )

    brief = reference_service.build_ref_frame_brief(project, shot)
    revision = reference_service.build_tail_frame_revision_brief(layout)

    assert "A door opens." in brief
    assert "The actor opens the door." in brief
    assert revision.source_refs[0].asset_id == "layout-asset-1"
    assert service.build_ref_frame_brief is reference_service.build_ref_frame_brief
    assert (
        service.build_tail_frame_revision_brief
        is reference_service.build_tail_frame_revision_brief
    )
