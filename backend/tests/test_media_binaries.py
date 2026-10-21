import os
import stat

from app.config import settings
from app.core.media import binaries


def _fake(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_configured_dir_wins_over_path(tmp_path, monkeypatch):
    _fake(tmp_path / "custom" / "bin" / "ffmpeg")
    monkeypatch.setattr(settings, "ffmpeg_dir", str(tmp_path / "custom"))
    monkeypatch.setattr(binaries.shutil, "which", lambda name: f"/usr/bin/{name}")
    path, source = binaries.locate_tool("ffmpeg")
    assert source == "configured" and path == str(tmp_path / "custom" / "bin" / "ffmpeg")


def test_bundled_before_path(tmp_path, monkeypatch):
    from types import SimpleNamespace

    _fake(tmp_path / "runtime" / "ffmpeg" / "bin" / "ffprobe")
    monkeypatch.setattr(settings, "ffmpeg_dir", "")
    monkeypatch.setattr(binaries, "runtime_paths", SimpleNamespace(install_root=tmp_path))
    monkeypatch.setattr(binaries.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert binaries.locate_tool("ffprobe") == (str(tmp_path / "runtime" / "ffmpeg" / "bin" / "ffprobe"), "bundled")
    # not bundled -> PATH
    assert binaries.locate_tool("ffmpeg") == ("/usr/bin/ffmpeg", "path")


def test_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(settings, "ffmpeg_dir", "")
    monkeypatch.setattr(binaries, "runtime_paths", SimpleNamespace(install_root=tmp_path))
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    assert binaries.find_tool("ffmpeg") is None


def test_non_executable_is_ignored(tmp_path, monkeypatch):
    from types import SimpleNamespace

    target = tmp_path / "runtime" / "ffmpeg" / "bin" / "ffmpeg"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    os.chmod(target, 0o644)
    monkeypatch.setattr(settings, "ffmpeg_dir", "")
    monkeypatch.setattr(binaries, "runtime_paths", SimpleNamespace(install_root=tmp_path))
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    assert binaries.find_tool("ffmpeg") is None
