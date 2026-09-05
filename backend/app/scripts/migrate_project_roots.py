"""Move library assets + jobs into data/projects/<prj>/ tree.

Usage (from backend/)::

    python -m app.scripts.migrate_project_roots
    python -m app.scripts.migrate_project_roots --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ..config import settings
from ..core.paths import ensure_project_tree, project_jobs_dir, project_library_kind_dir


def _move(src: Path, dest: Path, *, dry: bool) -> None:
    if not src.exists():
        return
    if dest.exists():
        print(f"  skip (exists): {dest}")
        return
    print(f"  move {src} -> {dest}")
    if dry:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def migrate(*, dry_run: bool = False) -> None:
    lib_root = settings.library_root
    jobs_root = settings.jobs_dir
    projects_root = settings.projects_dir

    print(f"library_root={lib_root}")
    print(f"jobs_root={jobs_root}")
    print(f"projects_dir={projects_root}")

    # --- assets ---
    if lib_root.exists():
        for kind_dir in sorted(lib_root.iterdir()):
            if not kind_dir.is_dir():
                continue
            kind = kind_dir.name
            for asset_dir in sorted(kind_dir.iterdir()):
                if not asset_dir.is_dir():
                    continue
                meta = asset_dir / "asset.json"
                if not meta.exists():
                    continue
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"  bad asset.json {asset_dir}: {e}")
                    continue
                pid = (data.get("project_id") or "").strip() or None
                if not pid:
                    print(f"  keep global (no project): {kind}/{asset_dir.name}")
                    continue
                ensure_project_tree(pid)
                dest = project_library_kind_dir(pid, kind) / asset_dir.name
                _move(asset_dir, dest, dry=dry_run)

    # --- jobs ---
    if jobs_root.exists():
        for job_dir in sorted(jobs_root.iterdir()):
            if not job_dir.is_dir():
                continue
            meta = job_dir / "job.json"
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  bad job.json {job_dir}: {e}")
                continue
            pid = (data.get("project_id") or "").strip() or None
            if not pid:
                # try params
                params = data.get("params") or {}
                pid = (params.get("project_id") or "").strip() or None
            if not pid:
                print(f"  keep global (no project): jobs/{job_dir.name}")
                continue
            ensure_project_tree(pid)
            dest = project_jobs_dir(pid) / job_dir.name
            _move(job_dir, dest, dry=dry_run)

    # ensure all existing projects have tree
    if projects_root.exists():
        for p in projects_root.iterdir():
            if p.is_dir() and (p / "project.json").exists():
                if not dry_run:
                    ensure_project_tree(p.name)
                print(f"  ensure tree: {p.name}")

    print("done" + (" (dry-run)" if dry_run else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
