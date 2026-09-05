from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from ...config import settings
from .models import Project, ProjectMode, Shot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_project_id() -> str:
    return f"prj_{uuid.uuid4().hex[:12]}"


def new_shot_id() -> str:
    return f"sht_{uuid.uuid4().hex[:12]}"


def project_dir(project_id: str) -> Path:
    return settings.projects_dir / project_id


def shots_dir(project_id: str) -> Path:
    return project_dir(project_id) / "shots"


def create_project(
    name: str,
    script_text: str,
    mode: ProjectMode = ProjectMode.director,
) -> Project:
    project_id = new_project_id()
    now = _now()
    project = Project(
        id=project_id,
        name=name.strip(),
        script_text=script_text,
        mode=mode,
        created_at=now,
        updated_at=now,
        shot_ids=[],
    )
    save_project(project)
    return project


def save_project(project: Project) -> None:
    from ..paths import ensure_project_tree

    project.updated_at = _now()
    ensure_project_tree(project.id)
    path = project_dir(project.id) / "project.json"
    path.write_text(project.model_dump_json(indent=2), encoding="utf-8")


def load_project(project_id: str) -> Project | None:
    path = project_dir(project_id) / "project.json"
    if not path.exists():
        return None
    return Project.model_validate_json(path.read_text(encoding="utf-8"))


def list_projects() -> list[Project]:
    items: list[Project] = []
    root = settings.projects_dir
    if not root.exists():
        return items
    for p in sorted(root.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        project = load_project(p.name)
        if project is not None:
            items.append(project)
    return items


def save_shot(shot: Shot) -> None:
    d = shots_dir(shot.project_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{shot.id}.json"
    path.write_text(shot.model_dump_json(indent=2), encoding="utf-8")


def load_shot(project_id: str, shot_id: str) -> Shot | None:
    path = shots_dir(project_id) / f"{shot_id}.json"
    if not path.exists():
        return None
    return Shot.model_validate_json(path.read_text(encoding="utf-8"))


def delete_shot(project_id: str, shot_id: str) -> bool:
    """Delete a single shot JSON. Returns True if a file was removed."""
    path = shots_dir(project_id) / f"{shot_id}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_shot_ids_on_disk(project_id: str) -> list[str]:
    """All shot file stems under projects/<id>/shots/."""
    d = shots_dir(project_id)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if p.is_file())


def replace_project_shots(project_id: str, shots: list[Shot]) -> list[str]:
    """
    Atomically adopt a new shot list for the project.

    - Saves every shot in ``shots``
    - Sets ``project.shot_ids`` to those ids only
    - Deletes any other shot JSON still on disk (old plan leftovers)

    Returns the list of deleted shot ids.
    """
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")

    new_ids = [s.id for s in shots]
    new_id_set = set(new_ids)

    for shot in shots:
        if shot.project_id != project_id:
            shot = shot.model_copy(update={"project_id": project_id})
        save_shot(shot)

    # Prefer known old ids + anything still on disk
    previous = set(project.shot_ids or []) | set(list_shot_ids_on_disk(project_id))
    deleted: list[str] = []
    for sid in sorted(previous - new_id_set):
        if delete_shot(project_id, sid):
            deleted.append(sid)

    project = project.model_copy(update={"shot_ids": new_ids})
    save_project(project)
    return deleted


def list_shots(project_id: str) -> list[Shot]:
    """Return shots for a project ordered by project.shot_ids (source of truth).

    Orphan JSON files not listed in shot_ids are ignored (and should be deleted
    by replace_project_shots on re-plan).
    """
    by_id: dict[str, Shot] = {}
    d = shots_dir(project_id)
    if d.exists():
        for p in d.glob("*.json"):
            try:
                shot = Shot.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            by_id[shot.id] = shot

    project = load_project(project_id)
    if project and project.shot_ids:
        ordered: list[Shot] = []
        for sid in project.shot_ids:
            if sid in by_id:
                ordered.append(by_id[sid])
        return ordered
    # No shot_ids yet: return whatever files exist (legacy / mid-create)
    return [by_id[k] for k in sorted(by_id)]

