"""Versioned, single-shot prompt repair authority; never a general agent turn."""
from __future__ import annotations

import json
import os
import uuid
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field

from ...core.projects.dialogue import digest
from ...core.projects.store import load_project, load_shot, project_dir
from ...core.managed_runs.store import _project_lock
from ...core.prompt_errors import PromptFailureError, MaterialReviewError, is_context_overflow


class PromptRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry_id: str = Field(min_length=1)
    shot_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)


_scope: ContextVar[dict | None] = ContextVar("prompt_only_retry", default=None)
PROMPT_ONLY_INSTRUCTIONS = (
    "\nThis is a scoped prompt-only retry. Preserve authored shot fields, script, dialogue, "
    "references and neighboring shots. Only repair prompt content and dialogue bindings. "
    "Keep shot_patch empty ({}) when the response envelope requires it, and brief null. "
    "Creative staging/prose within that scope is unrestricted."
)


def prompt_only_repair(repair):
    """Do not carry an earlier ordinary draft's authored edits into scoped recovery."""
    if not repair or not prompt_only_retry_active():
        return repair
    from .planner import _extract_json_payload
    try:
        candidate = _extract_json_payload(repair["rejected_candidate"])
    except (ValueError, TypeError, KeyError):
        return repair
    if isinstance(candidate, dict) and "shot_patch" in candidate:
        candidate = {**candidate, "shot_patch": {}}
        return {**repair, "rejected_candidate": json.dumps(candidate, ensure_ascii=False)}
    return repair


def authored_payload(shot):
    return shot.model_dump(mode="json", exclude={
        "meta", "prompt_sections", "status", "blocked_reasons", "h3_job_id", "ref_frame_job_id",
    })


def source_version(project, shot):
    from .brief import directing_requests
    from .material_review import capture_references
    try:
        references = capture_references(shot)[0]
    except ValueError as exc:
        references = {"unavailable": str(exc)}
    return digest([project.model_dump(mode="json", exclude={"updated_at"}),
        authored_payload(shot), shot.prompt_sections.model_dump(mode="json"), references, directing_requests(project),
        shot.meta.get("prompt_revision_requests"), shot.meta.get("prompt_revision_request")])


def _path(project_id):
    return project_dir(project_id) / "agent" / "prompt_retry.json"


def _load(project_id):
    try:
        return json.loads(_path(project_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(project_id, record):
    path = _path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_prompt_failure(shot, revision_request, error):
    from .prompt_repair import PromptRepairNoProgress
    from .dialogue_metadata import DialogueMetadataError
    project = load_project(shot.project_id)
    current = load_shot(shot.project_id, shot.id)
    if project is None or current is None:
        return None
    receipt = PromptRetryRequest(retry_id=f"prtry_{uuid.uuid4().hex}", shot_id=shot.id,
                                 source_version=source_version(project, current)).model_dump()
    with _project_lock(project.id):
        blocked = is_context_overflow(error) or isinstance(error, (PromptRepairNoProgress, DialogueMetadataError, MaterialReviewError))
        _save(project.id, {**receipt, "state": "blocked" if blocked else "pending", "revision_request": revision_request,
                           "error": str(error)})
    return receipt


def pending_prompt_retry(project_id):
    record = _load(project_id)
    if record and record.get("state") == "pending":
        return PromptRetryRequest.model_validate({k: record[k] for k in PromptRetryRequest.model_fields}).model_dump()
    return None


def assert_prompt_only_candidate(shot):
    scope = _scope.get()
    if scope and (shot.id != scope["shot_id"] or authored_payload(shot) != scope["authored"]):
        raise PromptFailureError("contract", "prompt_retry_scope: candidate changed authored shot fields. "
                                 "Repair only prompt content and bindings; keep the original shot unchanged.")


def assert_prompt_retry_inputs_current():
    scope = _scope.get()
    if scope:
        project = load_project(scope["project_id"])
        current = load_shot(scope["project_id"], scope["shot_id"])
        if project is None or current is None or source_version(project, current) != scope["source_version"]:
            raise PromptFailureError("contract", "Prompt retry inputs changed during repair; inspect current sources before retrying")


def prompt_only_retry_active():
    return _scope.get() is not None


def complete_prompt_retry(shot):
    with _project_lock(shot.project_id):
        record = _load(shot.project_id)
        if record and record.get("shot_id") == shot.id:
            record.update(state="completed", result=shot.model_dump(mode="json"),
                          completed_version=source_version(load_project(shot.project_id), shot))
            _save(shot.project_id, record)


async def run_prompt_retry(project_id, request, svc, *, on_progress=None):
    request = PromptRetryRequest.model_validate(request)
    with _project_lock(project_id):
        record = _load(project_id)
        if not record or any(record.get(key) != value for key, value in request.model_dump().items()):
            raise ValueError("Prompt retry changed or expired; refresh the latest failure")
        project = load_project(project_id)
        shot = load_shot(project_id, request.shot_id)
        expected_version = record.get("completed_version") if record["state"] == "completed" else request.source_version
        if project is None or shot is None or source_version(project, shot) != expected_version:
            raise ValueError("Prompt retry inputs changed; review current sources before requesting a new prompt")
        if record["state"] == "completed":
            # Only acknowledge a still-current saved result; never overwrite later work.
            if shot.model_dump(mode="json") != record.get("result"):
                raise ValueError("Prompt changed after this retry completed; inspect the current result")
            return shot
        if record["state"] != "pending":
            if record["state"] == "blocked":
                raise ValueError("Prompt retry is blocked; resolve the input failure before writing a new prompt. " + record.get("error", ""))
            raise ValueError("Prompt retry is already executing; inspect its outcome")
        record["state"] = "executing"
        _save(project_id, record)
    token = _scope.set({"shot_id": shot.id, "project_id": project_id,
                       "source_version": request.source_version, "authored": authored_payload(shot)})
    try:
        return await svc.write_prompts_after_layout(shot.id, revision_request=record["revision_request"],
                                                    **({"on_progress": on_progress} if on_progress else {}))
    except BaseException as exc:
        with _project_lock(project_id):
            latest = _load(project_id)
            if latest and latest.get("retry_id") == request.retry_id and latest.get("state") == "executing":
                latest["state"] = "blocked" if is_context_overflow(exc) else "pending"
                _save(project_id, latest)
        from .task_context_builder import ContextRequired
        if isinstance(exc, ContextRequired):
            raise
        if isinstance(exc, Exception) and not isinstance(exc, PromptFailureError):
            raise PromptFailureError("unknown", str(exc)) from exc
        raise
    finally:
        _scope.reset(token)
