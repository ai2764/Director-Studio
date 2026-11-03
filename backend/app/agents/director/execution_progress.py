"""Durable, observed execution stages. Presentation never grants execution authority."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import uuid

from ...core.projects.store import project_dir, load_project
from ...core.managed_runs.store import _project_lock

STAGES = ("plan", "materials", "prompt", "generation")
PHASES = {
    "reference_cache": "materials", "reference_observation": "materials",
    "material_review": "materials", "source_tail_frame": "materials",
    "source_ending": "materials", "tail_compatibility": "materials",
    "prompt_writing": "prompt", "prompt_repair": "prompt", "tail_draft": "prompt",
    "prompt_review": "prompt", "motion_review": "prompt",
}
TOOLS = {
    "set_script": "plan", "save_storyboard": "plan", "revise_shot": "plan",
    "inspect_asset": "materials", "queue_ref_frame": "materials",
    "queue_gpt_ref_frame": "materials", "cast_refs": "materials",
    "configure_video_context": "materials", "write_prompt": "prompt",
    "rewrite_prompt": "prompt", "write_prompts": "prompt",
    "start_h3_video": "generation",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path(project_id):
    return project_dir(project_id) / "agent" / "execution_progress.json"


def load_execution_progress(project_id):
    try:
        result = json.loads(_path(project_id).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) and result.get("version") == 1 else None
    except (OSError, ValueError):
        return None


def refresh_generation_progress(project_id, state):
    if not state:
        return state
    from ...core.jobs import load_job
    for stage in ("materials", "generation"):
        item = state["stages"][stage]
        if not item.get("job_id") or item["status"] not in {"submitted", "running"}:
            continue
        job = load_job(item["job_id"])
        if (job is None or job.project_id != project_id
                or (job.params.get("shot_id") and job.params["shot_id"] != state.get("shot_id"))):
            continue
        status = str(getattr(job.status, "value", job.status))
        mapped = {"queued": "submitted", "uploading": "running", "running": "running",
                  "succeeded": "completed" if stage == "generation" else "ready",
                  "failed": "failed", "cancelled": "cancelled"}.get(status)
        if mapped and mapped != item["status"]:
            item.update(status=mapped, finished_at=job.updated_at if mapped in
                        {"completed", "ready", "failed", "cancelled"} else None, quality_status="not_reviewed")
            activity = ({
                "submitted": "Video queued.", "running": "Generating video.",
                "completed": "Video generated; quality review not performed.",
                "failed": "Video generation failed.", "cancelled": "Video generation cancelled.",
            } if stage == "generation" else {
                "submitted": "Reference image queued.", "running": "Generating reference image.",
                "ready": "Reference image generated; visual review is still pending.",
                "failed": "Reference generation failed.", "cancelled": "Reference generation cancelled.",
            })[mapped]
            state.update(updated_at=job.updated_at, activity=activity)
            ExecutionProgress(project_id, state)._save()
    return state


class ExecutionProgress:
    def __init__(self, project_id, state):
        self.project_id, self.state = project_id, state

    @classmethod
    def start(cls, project_id, run_id):
        now = _now()
        progress = cls(project_id, {
            "version": 1, "run_id": run_id, "status": "running", "started_at": now,
            "updated_at": now, "current_stage": "plan", "shot_id": None,
            "activity": "Understanding the request", "stages": {
                stage: {"status": "running" if stage == "plan" else "not_requested",
                        "started_at": now if stage == "plan" else None, "finished_at": None}
                for stage in STAGES}, "shots": {}, "active_operations": {}})
        progress._save(start=True)
        return progress

    def _save(self, *, start=False):
        try:
            with _project_lock(self.project_id):
                current = load_execution_progress(self.project_id)
                if not start and (not current or current.get("run_id") != self.state["run_id"]):
                    return False
                if start:
                    self.state["run_sequence"] = (current or {}).get("run_sequence", 0) + 1
                self.state["revision"] = (current or {}).get("revision", 0) + 1
                path = _path(self.project_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            return True
        except Exception:
            logging.getLogger(__name__).exception("Could not persist Director execution progress")
            return False

    def apply_event(self, event):
        operation = event.get("type") == "execution_operation"
        stage = TOOLS.get(event.get("tool")) if operation else PHASES.get(event.get("phase"))
        state = event.get("state")
        if stage is None or state not in {"started", "completed", "failed", "cancelled", "reused", "hit", "miss", "unconfirmed", "submitted"}:
            return False
        stored = load_execution_progress(self.project_id)
        if not stored or stored.get("run_id") != self.state["run_id"]:
            return False
        if stored.get("revision", 0) > self.state.get("revision", 0):
            self.state = stored
        now = _now()
        shot_id = event.get("shot_id")
        if shot_id and shot_id != self.state["shot_id"]:
            if self.state["shot_id"]:
                self.state["shots"][self.state["shot_id"]] = deepcopy(self.state["stages"])
            # Stage receipts belong to a shot, not the next shot in the run.
            if shot_id in self.state["shots"]:
                self.state["stages"] = deepcopy(self.state["shots"][shot_id])
            elif self.state["shot_id"]:
                self.state["stages"] = {key: {"status": "not_requested", "started_at": None,
                    "finished_at": None} for key in STAGES}
            self.state["shot_id"] = shot_id
            self.state["active_operations"] = {}
        target = self.state["stages"][stage]
        self.state.update(current_stage=stage, updated_at=now,
            activity=event.get("text") or {"plan": "Planning shots", "materials": "Preparing references",
                "prompt": "Writing and validating prompt", "generation": "Submitting video"}[stage])
        active = self.state.setdefault("active_operations", {}).setdefault(stage, [])
        operation_key = ("tool:" + str(event.get("tool"))) if operation else ("phase:" + str(event.get("phase")))
        if state == "started" and operation_key not in active:
            active.append(operation_key)
        elif state in {"completed", "failed", "cancelled", "submitted"} and operation_key in active:
            active.remove(operation_key)
        if state in {"started", "miss"}:
            target.update(status="running", started_at=target["started_at"] or now, finished_at=None)
        elif state == "hit":
            # A hit for one image is not completion of the entire reference pack.
            target.update(status="running", started_at=target["started_at"] or now, finished_at=None)
        else:
            target.update(status="submitted" if stage == "generation" and state == "completed" else state,
                          started_at=target["started_at"] or now, finished_at=now)
            if state == "completed" and active:
                target.update(status="running", finished_at=None)
        if event.get("job_id") and stage in {"materials", "generation"}:
            target["job_id"] = event["job_id"]
            target["quality_status"] = "not_reviewed"
        project = load_project(self.project_id)
        if project and self.state["shot_id"] in project.shot_ids:
            self.state.update(shot_index=project.shot_ids.index(self.state["shot_id"]) + 1,
                              shot_total=len(project.shot_ids))
        return self._save()

    def finish(self, status):
        stored = load_execution_progress(self.project_id)
        if not stored or stored.get("run_id") != self.state["run_id"]:
            return False
        if stored.get("revision", 0) > self.state.get("revision", 0):
            self.state = stored
        now = _now()
        self.state.update(status=status, updated_at=now)
        for stage, item in self.state["stages"].items():
            if item["status"] == "running":
                # Overall chat completion is not proof a tool or video completed.
                item.update(status=status if status != "completed" else
                            ("completed" if stage == "plan" else "unconfirmed"), finished_at=now)
        return self._save()
