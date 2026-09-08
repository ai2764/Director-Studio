from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "build-linux-portable.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "linux-portable.yml"
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"
PACKAGE_NAME = "Director-Studio-Linux-x86_64"


def _bash_executable() -> str:
    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
        pytest.skip("Git Bash is required for Linux build safety tests on Windows")

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for Linux build safety tests")
    return bash


def _bash_path(path: Path) -> str:
    value = str(path).replace("\\", "/")
    if os.name == "nt" and len(value) >= 2 and value[1] == ":":
        return f"/{value[0].lower()}{value[2:]}"
    return value


def _run_sourced(
    body: str,
    *arguments: Path | str,
    uname_s: str | None = None,
    uname_m: str = "x86_64",
) -> subprocess.CompletedProcess[str]:
    bash = _bash_executable()
    script_path = _bash_path(SCRIPT)
    command = (
        "set -e; "
        "uname() { case \"$1\" in "
        f"-s) printf '%s\\n' '{uname_s or 'Linux'}' ;; "
        f"-m) printf '%s\\n' '{uname_m}' ;; "
        "*) return 1 ;; esac; }; "
        "source \"$1\"; shift; "
        + body
    )
    command_arguments = [
        bash,
        "-c",
        command,
        "bash",
        script_path,
        *(_bash_path(Path(argument)) for argument in arguments),
    ]
    environment = os.environ.copy()
    environment["DS_BUILD_TEST_MODE"] = "1"
    return subprocess.run(
        command_arguments,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_non_linux_host_is_rejected():
    result = _run_sourced("validate_linux_host", uname_s="Darwin")

    assert result.returncode != 0
    assert "Linux" in result.stderr


def test_non_x86_64_host_is_rejected():
    result = _run_sourced("validate_linux_host", uname_m="aarch64")

    assert result.returncode != 0
    assert "x86_64" in result.stderr


@pytest.mark.parametrize(
    "candidate",
    ["/", REPO_ROOT, REPO_ROOT.parent / "outside-linux-build"],
)
def test_cleanup_rejects_unsafe_generated_paths(candidate: Path | str):
    result = _run_sourced("remove_generated_directory \"$1\"", candidate)

    assert result.returncode != 0
    assert "Refusing" in result.stderr


def test_package_filenames_match_linux_portable_interface():
    result = _run_sourced(
        'printf "%s\\n" "$package_name" "$archive" "$checksum"'
    )

    assert result.returncode == 0, result.stderr
    package, archive, checksum = result.stdout.splitlines()
    assert package == PACKAGE_NAME
    assert archive == _bash_path(REPO_ROOT / "dist" / f"{PACKAGE_NAME}.tar.gz")
    assert checksum == f"{archive}.sha256"


def test_copied_linux_wrappers_are_required_and_executable(tmp_path: Path):
    built_executable = tmp_path / "DirectorStudio"
    built_executable.write_bytes(b"ELF fixture")
    package_root = tmp_path / PACKAGE_NAME

    result = _run_sourced(
        'copy_package_files "$1" "$2"; test -x "$2/launch.sh"; test -x "$2/install-tools.sh"',
        built_executable,
        package_root,
    )

    assert result.returncode == 0, result.stderr


def test_test_mode_only_loads_helpers_without_running_build_commands():
    result = _run_sourced(
        'command -v npm >/dev/null && command -v python >/dev/null; '
        'validate_generated_path "$1" >/dev/null',
        REPO_ROOT / "build" / "test-only",
    )

    assert result.returncode == 0, result.stderr


def test_linux_ci_installs_backend_test_dependencies():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    requirements = REQUIREMENTS.read_text(encoding="utf-8")

    assert "ffmpeg" in workflow
    assert "pytest-asyncio" in requirements
