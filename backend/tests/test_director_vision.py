"""Vision intent detection, Layout targeting, and stage-guide routing."""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from PIL import Image

from app.agents.director.chat import handle_chat
from app.agents.director.vision import (
    collect_vision_attachments,
    image_bytes_to_b64_jpeg,
    wants_vision,
)
from app.config import settings
from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
from app.core.projects.models import Shot, ShotStatus
from app.core.projects.store import create_project, save_project, save_shot
from app.core.schemas import LibraryAsset


def test_wants_vision_chinese_and_english():
    assert wants_vision("帮我看一下参考帧")
    assert wants_vision("第2镜构图怎么样，看图")
    assert wants_vision("look at the image please")
    assert not wants_vision("拆镜")
    assert not wants_vision("状态")


@pytest.mark.parametrize(
    "message",
    [
        "请用 GPT 生成参考帧",
        "帮我重新生成第三镜参考帧",
        "为这个 shot 新增一张参考帧",
    ],
)
def test_reference_frame_generation_does_not_attach_visual_review_images(message):
    assert not wants_vision(message)


def test_thumbnail_encode_png_header():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(20, 40, 60)).save(buf, format="PNG")
    b64 = image_bytes_to_b64_jpeg(buf.getvalue())
    assert b64 is not None
    assert len(b64) > 8


def _seed_layout_image(root, asset_id: str, color: tuple[int, int, int]) -> None:
    directory = root / "layouts" / asset_id
    directory.mkdir(parents=True)
    image_path = directory / "layout.png"
    Image.new("RGB", (16, 16), color=color).save(image_path)
    image_path.write_bytes(image_path.read_bytes() + b"layout-padding" * 200)
    asset = LibraryAsset(
        id=asset_id,
        kind="layouts",
        name=asset_id,
        pipeline_id="ref_frame",
        job_id=f"job_{asset_id}",
        created_at="2026-08-25T00:00:00+00:00",
        files={"layout": "layout.png"},
    )
    (directory / "asset.json").write_text(
        asset.model_dump_json(indent=2), encoding="utf-8"
    )


def _vision_shot(project_id: str) -> Shot:
    return Shot(
        id="sht_vision_layouts",
        project_id=project_id,
        scene_id="sc01",
        title="Doorway reveal",
        script_beat="Chen enters after the empty doorway beat",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_before",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="empty doorway",
                state_description="Lu is alone inside",
                time_hint="before Chen enters",
                review_status=LayoutReviewStatus.usable,
            ),
            LayoutReference(
                id="lr_after",
                asset_id="lay_after",
                purpose="post-entry blocking",
                state_description="Chen outside the glass, Lu inside",
                time_hint="after Chen enters",
                review_status=LayoutReviewStatus.pending_review,
            ),
        ],
    )


def _decoded_center_rgb(encoded: str) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        return image.convert("RGB").getpixel((image.width // 2, image.height // 2))


def test_requested_layout_id_attaches_actual_image_and_declared_manifest(
    tmp_path, monkeypatch
):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(settings, "library_root", library)
    _seed_layout_image(library, "lay_before", (220, 20, 20))
    _seed_layout_image(library, "lay_after", (20, 20, 220))
    shot = _vision_shot("prj_vision")

    pack = collect_vision_attachments(
        project_id=shot.project_id,
        shots=[shot],
        message="Review lr_after",
        layout_ref_ids=["lr_after"],
    )

    assert len(pack["images_b64"]) == 1
    red, green, blue = _decoded_center_rgb(pack["images_b64"][0])
    assert blue > red and blue > green
    assert "lr_after" in pack["note"]
    assert "purpose: post-entry blocking" in pack["note"]
    assert "state_description: Chen outside the glass, Lu inside" in pack["note"]
    assert "time_hint: after Chen enters" in pack["note"]
    assert "empty doorway" not in pack["note"]


def test_generic_review_targets_the_only_pending_layout(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(settings, "library_root", library)
    _seed_layout_image(library, "lay_before", (220, 20, 20))
    _seed_layout_image(library, "lay_after", (20, 20, 220))
    shot = _vision_shot("prj_vision")

    assert wants_vision("Review")
    pack = collect_vision_attachments(
        project_id=shot.project_id,
        shots=[shot],
        message="Review",
    )

    assert len(pack["images_b64"]) == 1
    red, green, blue = _decoded_center_rgb(pack["images_b64"][0])
    assert blue > red and blue > green
    assert "lr_after" in pack["note"]


@pytest.mark.asyncio
async def test_image_backed_review_uses_visual_qc_but_ordinary_chat_uses_no_guides(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    library = tmp_path / "library"
    projects.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "library_root", library)
    _seed_layout_image(library, "lay_before", (220, 20, 20))
    _seed_layout_image(library, "lay_after", (20, 20, 220))
    project = create_project("Vision guides", "INT. DUTY ROOM")
    shot = _vision_shot(project.id)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    calls: list[dict[str, Any]] = []

    async def recording_chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        return "Reviewed."

    await handle_chat(
        project_id=project.id,
        message="Review",
        svc=object(),
        chat_fn=recording_chat_fn,
    )
    await handle_chat(
        project_id=project.id,
        message="What is this shot about?",
        svc=object(),
        chat_fn=recording_chat_fn,
    )

    assert calls[0]["guides"] == ("visual-qc",)
    assert calls[0]["images"]
    assert calls[1].get("guides", ()) == ()
    assert "images" not in calls[1]


@pytest.mark.asyncio
async def test_user_uploaded_images_are_sent_directly_to_the_visual_agent(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    project = create_project("Uploaded vision", "INT. DUTY ROOM")
    calls: list[dict[str, Any]] = []

    async def recording_chat_fn(system: str, user: str, **kwargs):
        calls.append({"user": user, **kwargs})
        return "I see it."

    await handle_chat(
        project_id=project.id,
        message="Check the blocking in this frame.",
        svc=object(),
        chat_fn=recording_chat_fn,
        user_images_b64=["encoded-user-image"],
        user_image_captions=["blocking.png"],
    )

    assert calls[0]["images"] == ["encoded-user-image"]
    assert calls[0]["guides"] == ("visual-qc",)
    assert "User-uploaded image 1: blocking.png" in calls[0]["user"]


@pytest.mark.asyncio
async def test_review_named_layout_attaches_its_image_and_visual_qc_guide(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    library = tmp_path / "library"
    projects.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "library_root", library)
    _seed_layout_image(library, "lay_before", (220, 20, 20))
    _seed_layout_image(library, "lay_after", (20, 20, 220))
    project = create_project("Named Layout review", "INT. DUTY ROOM")
    shot = _vision_shot(project.id)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    calls: list[dict[str, Any]] = []

    async def recording_chat_fn(system: str, user: str, **kwargs):
        calls.append({"user": user, **kwargs})
        return "Reviewed."

    await handle_chat(
        project_id=project.id,
        message="Review lr_after",
        svc=object(),
        chat_fn=recording_chat_fn,
    )

    assert len(calls) == 1
    assert calls[0].get("guides") == ("visual-qc",)
    assert len(calls[0].get("images") or []) == 1
    red, green, blue = _decoded_center_rgb(calls[0]["images"][0])
    assert blue > red and blue > green
    assert "Layout lr_after" in calls[0]["user"]
    assert "purpose: empty doorway" not in calls[0]["user"]
