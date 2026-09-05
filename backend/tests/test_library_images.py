from app.core.library.images import preferred_file_keys, resolve_asset_image
from app.core.schemas import LibraryAsset


def test_actor_prefers_fullbody_threeview():
    keys = preferred_file_keys("actor")
    assert keys[0] == "fullbody_threeview"
    assert "master" in keys
    assert keys.index("fullbody_threeview") < keys.index("master")


def test_resolve_skips_tiny_wardrobe(tmp_path, monkeypatch):
    from app.core.library import images as img_mod
    from app.config import settings

    adir = tmp_path / "act_test"
    adir.mkdir()
    (adir / "wardrobe_ref.png").write_bytes(b"x" * 100)
    (adir / "fullbody_threeview.png").write_bytes(b"y" * 5000)
    (adir / "master.png").write_bytes(b"z" * 5000)

    monkeypatch.setattr(img_mod, "find_asset_dir", lambda kind, aid: adir)
    monkeypatch.setattr(
        img_mod,
        "asset_dir",
        lambda kind, aid, project_id=None: adir,
    )

    asset = LibraryAsset(
        id="act_test",
        kind="actors",
        name="t",
        pipeline_id="actor",
        job_id="j",
        created_at="t",
        files={
            "wardrobe_ref": "wardrobe_ref.png",
            "master": "master.png",
            "fullbody_threeview": "fullbody_threeview.png",
        },
    )
    hit = resolve_asset_image(asset, role="actor")
    assert hit is not None
    name, data, key = hit
    assert key == "fullbody_threeview"
    assert name == "fullbody_threeview.png"
    assert len(data) == 5000
