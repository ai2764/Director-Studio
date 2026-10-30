"""Permanent deletion of an explicitly confirmed, idle project."""
import re
import shutil
import stat
from ...config import settings
from ..jobs.store import list_jobs, job_dir
from ..managed_runs.store import _project_lock, active_run_for_project
from .store import load_project, project_dir


def _linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def delete_project(project_id, confirmation):
    if not re.fullmatch(r"prj_[A-Za-z0-9_-]+", project_id):
        raise ValueError("Invalid project ID")
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise FileNotFoundError("Project not found")
        if confirmation != project.name:
            raise ValueError("Type the project name exactly to confirm permanent deletion.")
        from ...shot_workspace import tasks
        from ...shot_workspace.timings import has_active_operation
        if has_active_operation(project_id):
            raise ValueError("Finish or cancel the active assistant before deleting this project.")
        task = tasks.status(project_id)
        if active_run_for_project(project_id) or (task and task["status"] == "running"):
            raise ValueError("Finish or cancel the active project task before deleting.")
        jobs = list_jobs(limit=None, project_id=project_id)
        if any(job.status in {"queued", "uploading", "running"} for job in jobs):
            raise ValueError("Finish or cancel generation before deleting this project.")
        root = settings.projects_dir.resolve()
        target = project_dir(project_id)
        if target.resolve().parent != root or _linked(target):
            raise ValueError("Project storage path is unsafe to delete.")
        targets = [target]
        for job in jobs:
            path = job_dir(job.id)
            if path.resolve().is_relative_to(target.resolve()):
                continue
            if path.resolve().parent != settings.jobs_dir.resolve() or _linked(path):
                raise ValueError("Job storage path is unsafe to delete.")
            targets.insert(0, path)
        # Validate every final target and nested link before deleting any files.
        for path in targets:
            if any(_linked(entry) for entry in path.rglob("*")):
                raise ValueError("Project contains linked files. Remove links before deleting.")
        for path in targets:
            shutil.rmtree(path)
