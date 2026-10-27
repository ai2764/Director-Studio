"""Reversible storyboard removal; Library assets and generation files stay intact."""
import json
import re
import uuid
from datetime import datetime, timezone

from .models import Shot
from .store import load_project, load_shot, list_shots, project_dir, save_project, save_shot, shots_dir
from ..managed_runs.store import _project_lock, active_run_for_project
from ..jobs.store import load_job


def _write_record(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _guard(project, shot):
    run = active_run_for_project(project.id)
    if run and any(step.shot_id == shot.id for step in run.steps):
        raise ValueError("This shot belongs to an active run. Stop the run before deleting it.")
    from ...shot_workspace.tasks import shot_has_active_prompt_task
    if shot_has_active_prompt_task(shot):
        raise ValueError("This shot has an active prompt task. Finish or cancel it before deleting.")
    for job_id in {shot.h3_job_id, shot.ref_frame_job_id, *(layout.job_id for layout in shot.layout_refs)} - {None}:
        job = load_job(job_id)
        if job and job.status in {"queued", "uploading", "running"}:
            raise ValueError("This shot is generating. Finish or cancel generation before deleting.")
    shots = list_shots(project.id)
    for index, other in enumerate(shots):
        context = other.video_context
        if other.id == shot.id or not context or context.mode != "previous_shot":
            continue
        source_id = context.source_shot_id or (shots[index - 1].id if index else None)
        if source_id == shot.id:
            raise ValueError(f"{other.title} continues this shot. Change or disable that continuity link first.")


def remove_shot(project_id, shot_id, expected=None):
    with _project_lock(project_id):
        project, shot = load_project(project_id), load_shot(project_id, shot_id)
        if not project or not shot or shot_id not in project.shot_ids:
            raise ValueError("The shot no longer exists. Refresh the project.")
        if expected is not None and expected != shot:
            raise ValueError("The shot changed. Refresh before deleting.")
        _guard(project, shot)
        index = project.shot_ids.index(shot_id)
        archive_id = "arc_" + uuid.uuid4().hex[:12]
        folder = project_dir(project_id) / "removed_shots"
        folder.mkdir(parents=True, exist_ok=True)
        record = {"shot": shot.model_dump(mode="json"), "index": index,
            "previous_id": project.shot_ids[index - 1] if index else None,
            "next_id": project.shot_ids[index + 1] if index + 1 < len(project.shot_ids) else None,
            "removed_at": datetime.now(timezone.utc).isoformat()}
        _write_record(folder / f"{archive_id}.json", record)
        path = shots_dir(project_id) / f"{shot_id}.json"
        path.unlink()
        try:
            save_project(project.model_copy(update={"shot_ids": [sid for sid in project.shot_ids if sid != shot_id]}))
        except Exception:
            save_shot(shot)
            raise
        return {"project_id": project_id, "shot_id": shot_id, "archive_id": archive_id,
                "shots": list_shots(project_id)}


def restore_shot(project_id, archive_id):
    if not re.fullmatch(r"arc_[a-f0-9]{12}", archive_id):
        raise ValueError("Invalid undo record.")
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("Project not found")
        path = project_dir(project_id) / "removed_shots" / f"{archive_id}.json"
        if not path.is_file():
            raise ValueError("Undo record not found.")
        record = json.loads(path.read_text(encoding="utf-8"))
        shot = Shot.model_validate(record["shot"])
        if shot.project_id != project_id:
            raise ValueError("This undo record belongs to another project.")
        if shot.id in project.shot_ids:
            return {"shot": load_shot(project_id, shot.id), "shots": list_shots(project_id)}
        if record.get("restored"):
            raise ValueError("This deletion was already undone. Use the latest undo record.")
        if load_shot(project_id, shot.id) is not None:
            raise ValueError("A shot with this ID already exists and was not overwritten.")
        ids = list(project.shot_ids)
        index = (ids.index(record["next_id"]) if record["next_id"] in ids else
                 ids.index(record["previous_id"]) + 1 if record["previous_id"] in ids else
                 min(record["index"], len(ids)))
        ids.insert(index, shot.id)
        current = {item.id: item for item in list_shots(project_id)}
        current[shot.id] = shot
        from .video_context import ensure_video_context_order
        validation = []
        for position, sid in enumerate(ids):
            item = current[sid]
            context = item.video_context
            if context and context.mode == "previous_shot" and not context.source_shot_id and position:
                item = item.model_copy(update={"video_context": context.model_copy(update={"source_shot_id": ids[position - 1]})})
            validation.append(item)
        ensure_video_context_order(validation)
        # Inserting must not retarget a legacy implicit previous-shot binding.
        old_ids = project.shot_ids
        for sid, item in current.items():
            context = item.video_context
            if sid == shot.id or not context or context.mode != "previous_shot" or context.source_shot_id:
                continue
            old_index, new_index = old_ids.index(sid), ids.index(sid)
            old_source = old_ids[old_index - 1] if old_index else None
            new_source = ids[new_index - 1] if new_index else None
            if old_source != new_source:
                raise ValueError("Restoring would change an existing continuity source. Select an explicit source first.")
        save_shot(shot)
        try:
            save_project(project.model_copy(update={"shot_ids": ids}))
        except Exception:
            (shots_dir(project_id) / f"{shot.id}.json").unlink()
            raise
        record["restored"] = True
        _write_record(path, record)
        return {"shot": shot, "shots": list_shots(project_id)}
