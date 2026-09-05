"""Tests for multipanel actor hair lock postprocess."""

from pathlib import Path

from PIL import Image

from app.pipelines.actor.hair_lock import (
    is_multipanel_hair_sheet,
    postprocess_actor_job_outputs,
    split_equal_panels,
)


def _fake_multipanel(path: Path) -> None:
    # 3 side-by-side head panels, 1.5 aspect
    w, h = 1500, 1000
    im = Image.new("RGB", (w, h), (40, 40, 40))
    # different brightness per panel so column check passes
    for i, color in enumerate([(200, 180, 160), (190, 170, 150), (180, 160, 140)]):
        x0 = i * (w // 3)
        x1 = w if i == 2 else (i + 1) * (w // 3)
        for x in range(x0, x1):
            for y in range(h // 2):
                im.putpixel((x, y), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _fake_threeview(path: Path, *, bust: bool) -> None:
    w, h = (1500, 700) if bust else (1500, 1000)
    im = Image.new("RGB", (w, h), (220, 220, 220))
    # paint fake "bun" blobs on side/back top
    for i in (1, 2):
        x0 = i * (w // 3) + 40
        for x in range(x0, x0 + 80):
            for y in range(30, 90):
                if 0 <= x < w and 0 <= y < h:
                    im.putpixel((x, y), (80, 60, 40))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def test_is_multipanel_detects_wide_sheet(tmp_path: Path):
    p = tmp_path / "actor.png"
    _fake_multipanel(p)
    assert is_multipanel_hair_sheet(Image.open(p))


def test_is_multipanel_rejects_portrait(tmp_path: Path):
    p = tmp_path / "portrait.png"
    Image.new("RGB", (512, 768), (100, 100, 100)).save(p)
    assert not is_multipanel_hair_sheet(Image.open(p))


def test_postprocess_rewrites_threeviews(tmp_path: Path):
    job = tmp_path / "job"
    (job / "inputs").mkdir(parents=True)
    (job / "outputs").mkdir(parents=True)
    _fake_multipanel(job / "inputs" / "actor.png")
    _fake_threeview(job / "outputs" / "fullbody_threeview.png", bust=False)
    _fake_threeview(job / "outputs" / "bust_threeview.png", bust=True)

    before = (job / "outputs" / "fullbody_threeview.png").read_bytes()
    result = postprocess_actor_job_outputs(job)
    assert result.get("fullbody_threeview") is True
    assert result.get("bust_threeview") is True
    after = (job / "outputs" / "fullbody_threeview.png").read_bytes()
    assert after != before
    assert (job / "outputs" / "fullbody_threeview.png.pre_hairlock.png").exists()


def test_split_three_panels():
    im = Image.new("RGB", (900, 400), (0, 0, 0))
    panels = split_equal_panels(im, 3)
    assert len(panels) == 3
    assert panels[0].size[0] == 300
