"""Authoring UI is project-owned and independent of Agent tuning settings."""
def is_workspace(project):
    if isinstance(project, str):
        from .store import load_project
        project = load_project(project)
    return project is not None and project.authoring_mode == "shot_workspace"
