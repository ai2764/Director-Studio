from __future__ import annotations

from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

from dotenv import dotenv_values
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "Install-Tools.py"
TEMPLATE = REPO_ROOT / "backend" / ".env.example"


def _load_installer():
    spec = importlib.util.spec_from_file_location("portable_tools_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)
    return installer


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


def test_linux_tool_paths_use_private_bin_directory(tmp_path: Path) -> None:
    installer = _load_installer()

    paths = installer.resolve_tool_paths(tmp_path, platform_name="posix")

    assert paths.python == tmp_path / "tools" / "venv" / "bin" / "python"
    assert paths.mcp == tmp_path / "tools" / "venv" / "bin" / "comfy-mcp"
    assert paths.comfy == tmp_path / "tools" / "venv" / "bin" / "comfy"


def test_windows_tool_paths_keep_scripts_executables(tmp_path: Path) -> None:
    installer = _load_installer()

    paths = installer.resolve_tool_paths(tmp_path, platform_name="nt")

    scripts = tmp_path / "tools" / "venv" / "Scripts"
    assert paths.python == scripts / "python.exe"
    assert paths.mcp == scripts / "comfy-mcp.exe"
    assert paths.comfy == scripts / "comfy.exe"


def test_linux_paths_survive_dotenv_parsing(tmp_path: Path) -> None:
    installer = _load_installer()
    env_file = tmp_path / ".env"
    mcp = tmp_path / "Director Studio" / "tools" / "venv" / "bin" / "comfy-mcp"
    comfy = tmp_path / "Director Studio" / "tools" / "venv" / "bin" / "comfy"

    installer.configure_env(
        env_file=env_file,
        template=TEMPLATE,
        mcp_command=mcp,
        comfy_command=comfy,
    )
    first = env_file.read_bytes()
    installer.configure_env(
        env_file=env_file,
        template=TEMPLATE,
        mcp_command=mcp,
        comfy_command=comfy,
    )

    configured = dotenv_values(env_file)
    assert configured["DS_COMFY_MCP_COMMAND"] == str(mcp)
    assert configured["DS_COMFY_MCP_COMFY_BIN"] == str(comfy)
    assert env_file.read_bytes() == first


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process semantics")
def test_posix_wrapper_uses_sibling_installer(tmp_path: Path) -> None:
    wrapper = REPO_ROOT / "install-tools.sh"
    fake_python = tmp_path / "fake-python"
    arguments = tmp_path / "arguments"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > {arguments!s}\n"
        "exit 17\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    completed = subprocess.run(
        [str(wrapper)],
        cwd=REPO_ROOT,
        env={**os.environ, "DS_PYTHON_EXE": str(fake_python)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 17
    assert arguments.read_text(encoding="utf-8").splitlines() == [
        str(REPO_ROOT / "Install-Tools.py")
    ]


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
