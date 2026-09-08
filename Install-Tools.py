from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys


MCP_COMMAND_KEY = "DS_COMFY_MCP_COMMAND"
COMFY_COMMAND_KEY = "DS_COMFY_MCP_COMFY_BIN"


@dataclass(frozen=True)
class ToolPaths:
    python: Path
    mcp: Path
    comfy: Path


def resolve_tool_paths(
    install_root: Path,
    platform_name: str | None = None,
) -> ToolPaths:
    selected = platform_name or os.name
    venv_root = install_root / "tools" / "venv"
    if selected == "nt":
        commands = venv_root / "Scripts"
        return ToolPaths(
            python=commands / "python.exe",
            mcp=commands / "comfy-mcp.exe",
            comfy=commands / "comfy.exe",
        )
    if selected == "posix":
        commands = venv_root / "bin"
        return ToolPaths(
            python=commands / "python",
            mcp=commands / "comfy-mcp",
            comfy=commands / "comfy",
        )
    raise RuntimeError(f"Unsupported operating system: {selected}")


def _quoted(value: Path | str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert_active_key(text: str, key: str, value: Path | str) -> tuple[str, str]:
    assignment = f"{key}={_quoted(value)}"
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*$")
    if pattern.search(text):
        return pattern.sub(lambda _match: assignment, text), assignment

    separator = "" if not text or text.endswith(("\n", "\r")) else "\n"
    return text + separator + assignment + "\n", assignment


def configure_env(
    *,
    env_file: Path,
    template: Path,
    mcp_command: Path | str,
    comfy_command: Path | str,
) -> list[str]:
    if env_file.is_file():
        original = env_file.read_text(encoding="utf-8")
    elif template.is_file():
        original = template.read_text(encoding="utf-8")
    else:
        original = ""

    configured = original
    changed: list[str] = []
    for key, value in (
        (MCP_COMMAND_KEY, mcp_command),
        (COMFY_COMMAND_KEY, comfy_command),
    ):
        updated, assignment = _upsert_active_key(configured, key, value)
        if updated != configured:
            changed.append(assignment)
        configured = updated

    if configured == original:
        return []

    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(configured, encoding="utf-8")
    return changed


def _run(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True)


def install_dependencies(install_root: Path, requirements: Path) -> tuple[Path, Path]:
    venv_root = install_root / "tools" / "venv"
    paths = resolve_tool_paths(install_root)
    if not paths.python.is_file():
        _run([sys.executable, "-m", "venv", str(venv_root)])
    _run([str(paths.python), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(paths.python), "-m", "pip", "install", "-r", str(requirements)])

    for executable in (paths.mcp, paths.comfy):
        if not executable.is_file():
            raise RuntimeError(f"Installation did not create {executable}")
    _run([str(paths.python), "-c", "import comfy_mcp"])
    _run([str(paths.comfy), "--help"])
    return paths.mcp, paths.comfy


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Install Director Studio's local Comfy MCP tools.",
    )
    parser.add_argument("--configure-only", action="store_true")
    parser.add_argument("--install-root", type=Path, default=root)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--mcp-command", type=Path)
    parser.add_argument("--comfy-command", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    install_root = args.install_root.resolve()
    env_file = (args.env_file or install_root / ".env").resolve()
    template = (args.template or env_file).resolve()
    requirements = (
        args.requirements or install_root / "portable-tools-requirements.txt"
    ).resolve()

    try:
        if args.configure_only:
            if args.mcp_command is None or args.comfy_command is None:
                raise ValueError(
                    "--configure-only requires --mcp-command and --comfy-command"
                )
            mcp_command = args.mcp_command
            comfy_command = args.comfy_command
        else:
            if not requirements.is_file():
                raise FileNotFoundError(f"Requirements file not found: {requirements}")
            mcp_command, comfy_command = install_dependencies(
                install_root,
                requirements,
            )

        changed = configure_env(
            env_file=env_file,
            template=template,
            mcp_command=mcp_command,
            comfy_command=comfy_command,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1

    if changed:
        print(f"Configured MCP tool paths in {env_file}")
    else:
        print(f"MCP tool paths already configured in {env_file}")
    print("Comfy MCP tools are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
