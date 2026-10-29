"""Project-scoped workspace size, seeded from the first shot's generation."""
from ..core.projects.store import load_shot
from ..core.jobs import load_job
from ..pipelines.h3_ref2va.resolutions import LOCAL_H3_PRESETS


def default_resolution(project):
    if project.workspace_default_resolution in LOCAL_H3_PRESETS:
        return project.workspace_default_resolution
    first = load_shot(project.id, project.shot_ids[0]) if project.shot_ids else None
    job = load_job(first.h3_job_id) if first and first.h3_job_id else None
    if job:
        dimensions = (job.params.get("width"), job.params.get("height"))
        for name, size in LOCAL_H3_PRESETS.items():
            if size == dimensions:
                return name
    portrait = any(marker in project.script_text.lower() for marker in ("9:16", "vertical"))
    return "portrait-480" if portrait else "landscape-480"
