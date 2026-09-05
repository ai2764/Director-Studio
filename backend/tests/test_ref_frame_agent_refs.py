"""Agent-cast shot.refs drive ref_frame image pack (not hardcoded role filter)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from app.agents.director.service import DirectorService
from app.core.projects.models import PromptSections, RefRole, Shot, ShotRef, ShotStatus
from app.core.schemas import LibraryAsset


class _NoopProvider:
    async def complete(
        self,
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        return "[]"


def _png_bytes() -> bytes:
    # Minimal valid-ish payload; collector only needs non-empty bytes
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> DirectorService:
    s = DirectorService(plan_provider=_NoopProvider())
    def _asset(id_: str, kind: str, name: str, files: dict[str, str]) -> LibraryAsset:
        return LibraryAsset(
            id=id_,
            kind=kind,
            name=name,
            files=files,
            pipeline_id="test",
            job_id="job_test",
            created_at="2026-01-01T00:00:00+00:00",
        )

    assets = {
        "act_a": _asset("act_a", "actors", "girl", {"master": "master.png"}),
        "scn_b": _asset("scn_b", "scenes", "corridor", {"master": "master.png"}),
        "prp_c": _asset("prp_c", "props", "deodorant", {"master": "master.jpg"}),
    }

    def fake_load(kind: str, asset_id: str):
        a = assets.get(asset_id)
        if a and a.kind == kind:
            return a
        # allow cross-kind search loop
        return assets.get(asset_id)

    def fake_read(asset: LibraryAsset, *, role: str | None = None, file_key: str | None = None):
        return ("x.png", _png_bytes())

    def fake_actor(asset, *, preferred_key=None):
        return ("actor.png", _png_bytes(), preferred_key or "master")

    def fake_scene(asset, *, preferred_key=None):
        return ("scene.png", _png_bytes(), preferred_key or "master")

    monkeypatch.setattr(
        "app.agents.director.service.load_asset", fake_load
    )
    monkeypatch.setattr(
        "app.agents.director.service._read_asset_image_bytes", fake_read
    )
    monkeypatch.setattr(
        "app.agents.director.service._actor_image_for_ref_frame", fake_actor
    )
    monkeypatch.setattr(
        "app.agents.director.service._scene_image_for_ref_frame", fake_scene
    )
    return s


def test_collect_uses_scene_actor_and_action_prop_images(svc: DirectorService):
    """A selected handled prop uses the third Qwen image slot when available."""
    shot = Shot(
        id="sht_agent_cast",
        project_id="prj_t",
        scene_id="sc01",
        title="spray",
        script_beat="spray deodorant",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            # Deliberately out of role-priority order in the list
            ShotRef(role=RefRole.prop, asset_id="prp_c", picture_index=3, file_key="master"),
            ShotRef(role=RefRole.actor, asset_id="act_a", picture_index=2, file_key="master"),
            ShotRef(role=RefRole.scene, asset_id="scn_b", picture_index=1, file_key="master"),
        ],
        prompt_sections=PromptSections(),
    )
    packed = svc._collect_ref_frame_refs(shot)
    assert list(packed["images"].keys()) == ["ref_0", "ref_1", "ref_2"]
    img_labels = [L for L in packed["labels"] if L.startswith("Image")]
    assert len(img_labels) == 3
    assert "SCENE" in packed["labels"][0] and "corridor" in packed["labels"][0]
    assert "CHARACTER" in packed["labels"][1] and "girl" in packed["labels"][1]
    assert "PROP" in packed["labels"][2] and "deodorant" in packed["labels"][2]
    assert "according to the shot action" in packed["labels"][2]
    assert "in the character's hand" not in packed["labels"][2]


def test_collect_honors_agent_selected_actor_and_scene_file_keys(svc: DirectorService):
    shot = Shot(
        id="sht_exact_files",
        project_id="prj_t",
        scene_id="sc01",
        title="selected angles",
        script_beat="actor crosses the corridor",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            ShotRef(
                role=RefRole.scene,
                asset_id="scn_b",
                picture_index=1,
                file_key="right_view",
            ),
            ShotRef(
                role=RefRole.actor,
                asset_id="act_a",
                picture_index=2,
                file_key="fullbody_threeview",
            ),
        ],
        prompt_sections=PromptSections(),
    )

    packed = svc._collect_ref_frame_refs(shot)

    assert "right_view" in packed["image_labels"][0]
    assert "fullbody_threeview" in packed["image_labels"][1]


def test_collect_skips_layout_output_role(svc: DirectorService):
    shot = Shot(
        id="sht_layout_skip",
        project_id="prj_t",
        scene_id="sc01",
        title="t",
        script_beat="b",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            ShotRef(role=RefRole.layout_ref_frame, asset_id="lay_x", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_b", picture_index=2),
            ShotRef(role=RefRole.actor, asset_id="act_a", picture_index=3),
        ],
        prompt_sections=PromptSections(),
    )
    packed = svc._collect_ref_frame_refs(shot)
    assert len(packed["images"]) == 2
    assert all("lay_" not in lab for lab in packed["labels"] if lab.startswith("Image"))
