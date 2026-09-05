from __future__ import annotations

from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

from dotenv import dotenv_values


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "Install-Tools.py"
TEMPLATE = REPO_ROOT / "backend" / ".env.example"


def _run_configure_only(tmp_path: Path, env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--configure-only",
            "--install-root",
            str(tmp_path),
            "--env-file",
            str(env_file),
            "--template",
            str(TEMPLATE),
            "--mcp-command",
            r"C:\Director Studio\tools\venv\Scripts\comfy-mcp.exe",
            "--comfy-command",
            r"C:\Director Studio\tools\venv\Scripts\comfy.exe",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_installer_creates_env_with_local_tool_paths(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"

    completed = _run_configure_only(tmp_path, env_file)

    assert completed.returncode == 0, completed.stderr
    configured = dotenv_values(env_file)
    assert configured["DS_COMFY_MCP_COMMAND"] == (
        r"C:\Director Studio\tools\venv\Scripts\comfy-mcp.exe"
    )
    assert configured["DS_COMFY_MCP_COMFY_BIN"] == (
        r"C:\Director Studio\tools\venv\Scripts\comfy.exe"
    )


def test_installer_paths_survive_dotenv_parsing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"

    completed = _run_configure_only(tmp_path, env_file)

    assert completed.returncode == 0, completed.stderr
    parsed = dotenv_values(env_file)
    assert parsed["DS_COMFY_MCP_COMMAND"] == (
        r"C:\Director Studio\tools\venv\Scripts\comfy-mcp.exe"
    )
    assert parsed["DS_COMFY_MCP_COMFY_BIN"] == (
        r"C:\Director Studio\tools\venv\Scripts\comfy.exe"
    )


def test_installer_replaces_existing_mcp_configuration(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    original = (
        "DS_COMFY_MCP_COMMAND=existing-mcp\n"
        "DS_COMFY_MCP_ARGS=--transport stdio\n"
        "DS_COMFY_MCP_COMFY_BIN=existing-comfy\n"
    )
    env_file.write_text(original, encoding="utf-8")

    completed = _run_configure_only(tmp_path, env_file)

    assert completed.returncode == 0, completed.stderr
    configured = dotenv_values(env_file)
    assert configured["DS_COMFY_MCP_COMMAND"] == (
        r"C:\Director Studio\tools\venv\Scripts\comfy-mcp.exe"
    )
    assert configured["DS_COMFY_MCP_ARGS"] == "--transport stdio"
    assert configured["DS_COMFY_MCP_COMFY_BIN"] == (
        r"C:\Director Studio\tools\venv\Scripts\comfy.exe"
    )


def test_dependency_verification_does_not_start_mcp_server(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spec = importlib.util.spec_from_file_location("portable_tools_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    scripts = tmp_path / "tools" / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("python.exe", "comfy-mcp.exe", "comfy.exe"):
        (scripts / name).touch()
    requirements = tmp_path / "requirements.txt"
    requirements.touch()
    commands: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", commands.append)

    installer.install_dependencies(tmp_path, requirements)

    assert [str(scripts / "comfy-mcp.exe"), "--help"] not in commands
    assert [str(scripts / "python.exe"), "-c", "import comfy_mcp"] in commands


def test_cmd_wrapper_returns_python_installer_failure(tmp_path: Path) -> None:
    shutil.copy2(REPO_ROOT / "Install-Tools.cmd", tmp_path / "Install-Tools.cmd")
    shutil.copy2(INSTALLER, tmp_path / "Install-Tools.py")

    completed = subprocess.run(
        [
            os.environ["ComSpec"],
            "/d",
            "/c",
            "call Install-Tools.cmd",
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
