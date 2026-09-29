from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "windows-portable.yml"
BUILDER = REPO_ROOT / "scripts" / "build-windows-portable.ps1"


def test_windows_portable_ci_builds_and_publishes_verified_artifacts() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: windows-2022" in text
    assert "pwsh -NoProfile -File scripts/build-windows-portable.ps1" in text
    assert "dist/Director-Studio-Windows-x64.zip" in text
    assert "dist/Director-Studio-Windows-x64.zip.sha256" in text
    assert "if-no-files-found: error" in text
    assert "startsWith(github.ref, 'refs/tags/v')" in text


def test_windows_builder_emits_checksum_file() -> None:
    text = BUILDER.read_text(encoding="utf-8")

    assert '$checksumPath = "$zipPath.sha256"' in text
    assert "Set-Content -LiteralPath $checksumPath" in text
