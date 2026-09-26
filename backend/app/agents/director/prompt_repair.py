"""Keep rejected candidates separate from approved prompts, bound to their inputs."""
from __future__ import annotations

import hashlib
import json
import os
import uuid

from ...core.projects.store import project_dir
from ...core.projects.models import PromptSections
from ...core.managed_runs.context import managed_turn_scope
from .planner import _extract_json_payload
from .brief import directing_requests


def repair_key(project, shot, signature, revision_request, model, *, reference_evidence=None):
    from .reference_facts import REFERENCE_POLICY_VERSION
    authored = shot.model_dump(mode="json", exclude={"meta", "status", "h3_job_id", "ref_frame_job_id"})
    payload = [project.script_text, directing_requests(project),
               project.asset_coverage_review.model_dump(mode="json") if project.asset_coverage_review else None,
               authored, signature, model, REFERENCE_POLICY_VERSION, reference_evidence,
               revision_request if managed_turn_scope.get() is None else ""]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _path(shot):
    return project_dir(shot.project_id) / "agent" / "prompt_drafts" / f"{shot.id}.json"


def load_repair(shot, key):
    try:
        saved = json.loads(_path(shot).read_text(encoding="utf-8"))
        return saved["repair"] if saved.get("key") == key else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_repair(shot, key, raw, error, review=None):
    if not isinstance(raw, str) or not raw.strip():
        return
    path = _path(shot)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"key": key, "repair": {
            "rejected_candidate": raw, "error": str(error), "review": review,
            "issues": [item.model_dump(mode="json") for item in getattr(error, "issues", [])],
        }}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_repair(shot):
    _path(shot).unlink(missing_ok=True)


def merge_repair(raw, previous, *, envelope=False):
    """Allow a bounded field patch; all merged content is subsequently revalidated."""
    try:
        patch = _extract_json_payload(raw)
        base = _extract_json_payload(previous)
    except (ValueError, TypeError):
        return raw
    if not isinstance(patch, dict) or not isinstance(base, dict):
        return raw
    if envelope or "prompt_sections" in base:
        merged = {**base, **patch}
        if "prompt_sections" in base and set(patch) <= set(PromptSections.model_fields):
            merged = {**base, "prompt_sections": {**base["prompt_sections"], **patch}}
        for key in ("prompt_sections", "shot_patch"):
            if isinstance(base.get(key), dict) and isinstance(patch.get(key), dict):
                merged[key] = {**base[key], **patch[key]}
        changed_detail = merged.get("prompt_sections", {}).get("detailed_description") != base.get("prompt_sections", {}).get("detailed_description")
        if changed_detail and "dialogue_uses" in base and "dialogue_uses" not in patch:
            merged.pop("dialogue_uses", None)
    else:
        if set(patch) - set(PromptSections.model_fields):
            return raw
        merged = {**base, **patch}
    return raw if merged == patch else json.dumps(merged, ensure_ascii=False)


def repair_request(user, repair):
    return (f"Original shot/context request:\n{user}\n\n"
            f"Previous prompt JSON failed: {repair['error']}\n"
            f"Rejected candidate:\n{repair['rejected_candidate']}\n"
            "Return corrected fields in the same JSON envelope as the candidate. If detailed_description changes, return fresh dialogue_uses when attribution is required. "
            "Keep all valid content, exact dialogue and reference bindings. The merged six sections will be revalidated.")
