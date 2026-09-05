from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


@dataclass(frozen=True)
class RuntimePaths:
    bundle_root: Path
    install_root: Path
    data_root: Path
    env_file: Path
    frozen: bool


def resolve_runtime_paths(
    *,
    frozen: bool | None = None,
    bundle_root: Path | None = None,
    executable: Path | None = None,
    source_root: Path | None = None,
) -> RuntimePaths:
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
    return RuntimePaths(
        bundle_root=repository_root,
        install_root=repository_root,
        data_root=repository_root / "data",
        env_file=repository_root / "backend" / ".env",
        frozen=False,
    )


runtime_paths = resolve_runtime_paths()
