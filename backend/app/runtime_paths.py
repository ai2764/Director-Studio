from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

# Marker written by installer/build_linux.py at the root of a self-contained
# package ("installed" layout: source backend + bundled runtimes, user data
# kept outside the application directory so it survives upgrades).
INSTALL_MARKER = "ds-install.json"


@dataclass(frozen=True)
class RuntimePaths:
    bundle_root: Path
    install_root: Path
    data_root: Path
    env_file: Path
    frozen: bool
    installed: bool = False
    # Package-level defaults (e.g. DS_COMFY_MODE=managed) declared in the marker.
    install_defaults: Mapping[str, str] = field(default_factory=dict)
    install_info: Mapping[str, Any] = field(default_factory=dict)


def default_user_data_dir(
    *, environ: Mapping[str, str] | None = None, platform: str | None = None,
) -> Path:
    """Per-user data directory for installed packages (outside the app dir)."""
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):  # Windows packaging (batch 3): %LOCALAPPDATA%\DirectorStudio
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "DirectorStudio"
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / "DirectorStudio"
    base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "DirectorStudio"


def read_install_marker(root: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((root / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def resolve_runtime_paths(
    *,
    frozen: bool | None = None,
    bundle_root: Path | None = None,
    executable: Path | None = None,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimePaths:
    env = os.environ if environ is None else environ
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        executable_path = Path(executable or sys.executable).resolve()
        resources = Path(
            bundle_root or getattr(sys, "_MEIPASS", executable_path.parent)
        ).resolve()
        install_root = executable_path.parent
        return RuntimePaths(
            bundle_root=resources,
            install_root=install_root,
            data_root=install_root / "data",
            env_file=install_root / ".env",
            frozen=True,
        )

    repository_root = Path(
        source_root or Path(__file__).resolve().parents[2]
    ).resolve()
    marker = read_install_marker(repository_root)
    if marker is not None:
        # Installed package: DS_HOME (or the per-user data dir) holds .env + data.
        home = Path(env.get("DS_HOME") or default_user_data_dir(environ=env)).expanduser()
        defaults = marker.get("defaults") if isinstance(marker.get("defaults"), dict) else {}
        return RuntimePaths(
            bundle_root=repository_root,
            install_root=repository_root,
            data_root=home / "data",
            env_file=home / ".env",
            frozen=False,
            installed=True,
            install_defaults={str(k): str(v).replace("${INSTALL_ROOT}", str(repository_root))
                              .replace("${DS_HOME}", str(home)) for k, v in defaults.items()},
            install_info=marker,
        )
    return RuntimePaths(
        bundle_root=repository_root,
        install_root=repository_root,
        data_root=repository_root / "data",
        env_file=repository_root / "backend" / ".env",
        frozen=False,
    )


runtime_paths = resolve_runtime_paths()
