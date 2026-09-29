"""Compatibility wrapper for the shared portable Harness stager."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Sequence

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from stage_portable_harness import (
    RuntimeConfig,
    StageError,
    extract_node_runtime,
    load_runtime_config as _load_runtime_config,
    stage_harness,
    stage_portable_harness,
    verify_sha256,
    write_portable_manifest,
)


CONFIG_PATH = Path(__file__).resolve().parents[1] / "packaging" / "portable-harness-runtimes.json"


def load_runtime_config(path: Path = CONFIG_PATH) -> RuntimeConfig:
    return _load_runtime_config(path, target="windows-x64")


def stage_windows_harness(
    repo_root: Path,
    destination: Path,
    *,
    node_archive: Path | None = None,
) -> None:
    stage_portable_harness(
        repo_root,
        destination,
        target="windows-x64",
        node_archive=node_archive,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage Windows Harness portable runtime")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--node-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        stage_windows_harness(
            args.repo_root.resolve(),
            args.destination.resolve(),
            node_archive=args.node_archive.resolve() if args.node_archive else None,
        )
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError, StageError) as exc:
        parser.exit(1, f"Windows Harness staging failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
