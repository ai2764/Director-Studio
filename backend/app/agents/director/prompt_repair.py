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
from ...core.prompt_errors import PromptFailureError, prompt_failure_kind


class PromptRepairNoProgress(PromptFailureError):
    def __init__(self, message, original=None):
        kind = prompt_failure_kind(original) if original else "candidate"
        super().__init__(kind, "Prompt repair made no progress: " + message)
        self.issues = getattr(original, "issues", [])


def require_repair_progress(previous, raw, error):
    """Stop identical candidate + identical failure, independent of creative wording."""
    if not previous or not raw:
        return
    def candidate(value):
        try:
            return _extract_json_payload(value)
        except (ValueError, TypeError):
            return value
    if (candidate(previous.get("rejected_candidate")) == candidate(raw)
            and previous.get("error") == str(error)):
        raise PromptRepairNoProgress(
            "The same candidate failed the same validation. Review its source or provide a new direction. " + str(error), error
        ) from error


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
        patch_sections = patch.get("prompt_sections", patch)
        fresh_detail = patch_sections.get("detailed_description") if isinstance(patch_sections, dict) else None
        fresh_placeholders = isinstance(fresh_detail, str) and "{{speech:" in fresh_detail
        if (changed_detail or fresh_placeholders) and "dialogue_uses" in base and "dialogue_uses" not in patch:
            merged.pop("dialogue_uses", None)
    else:
        if set(patch) - set(PromptSections.model_fields):
            return raw
        merged = {**base, **patch}
    return raw if merged == patch else json.dumps(merged, ensure_ascii=False)


def repair_request(user, repair, *, dialogue_bindings=True):
    issues = repair.get("issues") or []
    issue_details = ("Structured validation issues:\n"
                     + json.dumps(issues, ensure_ascii=False) + "\n") if issues else ""
    dialogue_instructions = (
        "For source-backed dialogue, return detailed_description with {{speech:line_id}} references "
        "and omit dialogue_uses; the backend compiles attribution. "
        if dialogue_bindings else
        "Write exact scripted words in <d>[Language] words</d> blocks in detailed_description. "
        "Use the final H3 dialogue format; no internal speech placeholders are supported for this shot. "
    )
    return (f"Original shot/context request:\n{user}\n\n"
            f"Previous prompt JSON failed: {repair['error']}\n"
            f"{issue_details}"
            f"Rejected candidate:\n{repair['rejected_candidate']}\n"
            "Return corrected fields in the same JSON envelope as the candidate. "
            f"{dialogue_instructions}"
            "Repair the listed defects while applying the latest explicit shot-specific directing requirements. "
            "A rejected candidate is a draft, not authority to cancel a requested revision. "
            "Preserve its creative prose only where consistent with those requirements; reconcile conflicting "
            "camera/action descriptions across all six sections. Keep exact dialogue and reference bindings. "
            "The merged six sections will be revalidated.")
