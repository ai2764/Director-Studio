from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_CHECK = REPO_ROOT / "scripts" / "test-packaged-workflow-assets.ps1"
H3_CHECK = REPO_ROOT / "scripts" / "test-packaged-h3-profiles.ps1"

WORKFLOW_ENTRIES = (
    "workflows\\\\qwen_actor_asset_workbench.api.json\n"
    "workflows\\\\qwen_prop_master.api.json\n"
    "workflows\\\\QwenEdit2511_MultiAngle_SceneRef.api.json\n"
    "workflows\\\\ref_frame_layout.api.json\n"
    "workflows\\\\h3_ref2va.api.json"
)


def _write_fake_archive_viewer(tmp_path: Path, listing: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    listing_path = tmp_path / "archive-listing.txt"
    listing_path.write_text(listing, encoding="utf-8")
    (bin_dir / "py.cmd").write_text(
        "@echo off\r\ntype \"%FAKE_ARCHIVE_LISTING%\"\r\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_ARCHIVE_LISTING"] = str(listing_path)
    return env


def _write_portable_zip(tmp_path: Path, executable: Path, *extra: str) -> Path:
    zip_path = tmp_path / "Director-Studio-Legacy-Windows-x64.zip"
    package = "Director-Studio-Legacy-Windows-x64"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(executable, f"{package}/DirectorStudio.exe")
        archive.writestr(f"{package}/README.md", "portable fixture")
        for entry in extra:
            archive.writestr(f"{package}/{entry}", "must not ship")
    return zip_path


def _run_script(script: Path, *arguments: str, env: dict[str, str]):
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), *arguments],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_workflow_archive_check_rejects_external_h3_profile_state(tmp_path: Path):
    executable = tmp_path / "DirectorStudio.exe"
    executable.write_bytes(b"fixture")
    env = _write_fake_archive_viewer(
        tmp_path,
        f"{WORKFLOW_ENTRIES}\nworkflow_profiles\\h3\\profiles\\custom\\profile.json",
    )

    result = _run_script(
        WORKFLOW_CHECK,
        "-ExecutablePath",
        str(executable),
        env=env,
    )

    assert result.returncode != 0
    assert "workflow_profiles/h3/profiles" in f"{result.stdout}\n{result.stderr}"


def test_h3_archive_check_accepts_official_only_executable_and_zip(tmp_path: Path):
    executable = tmp_path / "DirectorStudio.exe"
    executable.write_bytes(b"same packaged executable")
    zip_path = _write_portable_zip(tmp_path, executable)
    env = _write_fake_archive_viewer(tmp_path, WORKFLOW_ENTRIES)

    result = _run_script(
        H3_CHECK,
        "-ExecutablePath",
        str(executable),
        "-ZipPath",
        str(zip_path),
        env=env,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "official H3-only packaging test passed" in result.stdout


def test_h3_archive_check_rejects_external_state_in_zip(tmp_path: Path):
    executable = tmp_path / "DirectorStudio.exe"
    executable.write_bytes(b"same packaged executable")
    zip_path = _write_portable_zip(tmp_path, executable, "data/active.json")
    env = _write_fake_archive_viewer(tmp_path, WORKFLOW_ENTRIES)

    result = _run_script(
        H3_CHECK,
        "-ExecutablePath",
        str(executable),
        "-ZipPath",
        str(zip_path),
        env=env,
    )

    assert result.returncode != 0
    assert "data" in f"{result.stdout}\n{result.stderr}"


def test_h3_archive_check_rejects_external_data_in_executable(tmp_path: Path):
    executable = tmp_path / "DirectorStudio.exe"
    executable.write_bytes(b"same packaged executable")
    zip_path = _write_portable_zip(tmp_path, executable)
    env = _write_fake_archive_viewer(
        tmp_path,
        f"{WORKFLOW_ENTRIES}\ndata/library/picture.png",
    )

    result = _run_script(
        H3_CHECK,
        "-ExecutablePath",
        str(executable),
        "-ZipPath",
        str(zip_path),
        env=env,
    )

    assert result.returncode != 0
    assert "data" in f"{result.stdout}\n{result.stderr}"
