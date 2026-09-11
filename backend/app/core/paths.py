"""Project-rooted storage layout.

Canonical tree (project is the unit of context for third-party agents)::

    data/projects/<prj_id>/
      project.json
      agent/
      shots/
      library/
        actors|scenes|layouts|costumes|props/<asset_id>/
      jobs/
        <job_id>/inputs|outputs|job.json

Legacy global pools (unassigned / pre-migration)::

    data/library/<kind>/<asset_id>/
    data/jobs/<job_id>/
"""

from __future__ import annotations

from pathlib import Path

from ..config import settings

LIBRARY_KINDS = ("actors", "costumes", "scenes", "props", "layouts")


def project_root(project_id: str) -> Path:
    return settings.projects_dir / project_id


def ensure_project_tree(project_id: str) -> Path:
    """Create standard subdirs under a project. Returns project root."""
    root = project_root(project_id)
    for sub in ("agent", "shots", "jobs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    lib = root / "library"
    lib.mkdir(parents=True, exist_ok=True)
    for kind in LIBRARY_KINDS:
        (lib / kind).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Director Studio project\n\n"
            "This folder is the **project root** — hand it to a third-party agent as full context.\n\n"
            "| Path | Contents |\n"
            "|------|----------|\n"
            "| `project.json` | Name, script, shot ids |\n"
            "| `shots/` | Per-shot JSON (status, refs, prompts) |\n"
            "| `library/` | Actors, scenes, layouts, costumes, props (images + asset.json) |\n"
            "| `jobs/` | Generation jobs (inputs/outputs) |\n"
            "| `json-production/` | Persisted JSON Production Picture and Audio selections |\n"
            "| `agent/` | Director agent context |\n",
            encoding="utf-8",
        )
    return root


def project_library_kind_dir(project_id: str, kind: str) -> Path:
    d = project_root(project_id) / "library" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_jobs_dir(project_id: str) -> Path:
    d = project_root(project_id) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def global_library_kind_dir(kind: str) -> Path:
    d = settings.library_root / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def asset_write_dir(kind: str, asset_id: str, *, project_id: str | None) -> Path:
    """Where a new/updated asset should live."""
    if project_id:
        return project_library_kind_dir(project_id, kind) / asset_id
    return global_library_kind_dir(kind) / asset_id


def find_asset_dir(kind: str, asset_id: str) -> Path | None:
    """Locate an asset folder under project libraries or global pool."""
    # Prefer project-owned copies (scan projects first, newest first)
    root = settings.projects_dir
    if root.exists():
        for p in sorted(root.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            cand = p / "library" / kind / asset_id
            if (cand / "asset.json").is_file() or (cand / "actor.json").is_file():
                return cand
    # Legacy global
    cand = settings.library_root / kind / asset_id
    if (cand / "asset.json").is_file() or (cand / "actor.json").is_file():
        return cand
    return None


def job_write_dir(job_id: str, *, project_id: str | None) -> Path:
    if project_id:
        return project_jobs_dir(project_id) / job_id
    return settings.jobs_dir / job_id


def find_job_dir(job_id: str) -> Path | None:
    # Project jobs first
    root = settings.projects_dir
    if root.exists():
        for p in sorted(root.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            cand = p / "jobs" / job_id
            if (cand / "job.json").is_file():
                return cand
    cand = settings.jobs_dir / job_id
    if (cand / "job.json").is_file():
        return cand
    return None


def iter_asset_dirs(kind: str) -> list[Path]:
    """All asset directories for a kind (project + global)."""
    out: list[Path] = []
    seen: set[str] = set()
    root = settings.projects_dir
    if root.exists():
        for p in sorted(root.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            kdir = p / "library" / kind
            if not kdir.is_dir():
                continue
            for ad in sorted(kdir.iterdir(), reverse=True):
                if ad.is_dir() and ad.name not in seen:
                    if (ad / "asset.json").is_file() or (ad / "actor.json").is_file():
                        out.append(ad)
                        seen.add(ad.name)
    g = settings.library_root / kind
    if g.is_dir():
        for ad in sorted(g.iterdir(), reverse=True):
            if ad.is_dir() and ad.name not in seen:
                if (ad / "asset.json").is_file() or (ad / "actor.json").is_file():
                    out.append(ad)
                    seen.add(ad.name)
    return out


def iter_job_dirs() -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    root = settings.projects_dir
    if root.exists():
        for p in sorted(root.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            jroot = p / "jobs"
            if not jroot.is_dir():
                continue
            for jd in sorted(jroot.iterdir(), reverse=True):
                if jd.is_dir() and (jd / "job.json").is_file() and jd.name not in seen:
                    out.append(jd)
                    seen.add(jd.name)
    if settings.jobs_dir.exists():
        for jd in sorted(settings.jobs_dir.iterdir(), reverse=True):
            if jd.is_dir() and (jd / "job.json").is_file() and jd.name not in seen:
                out.append(jd)
                seen.add(jd.name)
    return out


def resolve_under_data(rel: str) -> Path | None:
    """
    Resolve a path under data/ with project-root awareness.

    Supports:
    - projects/<prj>/library/...
    - projects/<prj>/jobs/...
    - library/<kind>/<id>/...  (search projects then global)
    - jobs/<job_id>/...        (search projects then global)
    """
    rel = rel.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        return None
    parts = rel.split("/")

    # Direct path under data/
    direct = (settings.data_dir / rel).resolve()
    try:
        direct.relative_to(settings.data_dir.resolve())
    except ValueError:
        return None
    if direct.is_file():
        return direct

    # library/<kind>/<asset_id>/<file...>
    if len(parts) >= 4 and parts[0] == "library":
        kind, asset_id = parts[1], parts[2]
        rest = Path(*parts[3:])
        adir = find_asset_dir(kind, asset_id)
        if adir:
            full = (adir / rest).resolve()
            try:
                full.relative_to(settings.data_dir.resolve())
            except ValueError:
                return None
            if full.is_file():
                return full

    # jobs/<job_id>/<file...>
    if len(parts) >= 3 and parts[0] == "jobs":
        job_id = parts[1]
        rest = Path(*parts[2:])
        jdir = find_job_dir(job_id)
        if jdir:
            full = (jdir / rest).resolve()
            try:
                full.relative_to(settings.data_dir.resolve())
            except ValueError:
                return None
            if full.is_file():
                return full

    return None
