"""Provenance and publication contracts; creative/visual interpretation stays with the model."""
from __future__ import annotations

from typing import Literal
import json
import os
import uuid
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ...core.projects.dialogue import digest
from ...core.prompt_errors import PromptFailureError

REFERENCE_POLICY_VERSION = 2


class VisualFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    attribute: str = Field(min_length=1, max_length=160)
    value: str | None = Field(default=None, max_length=600, coerce_numbers_to_str=True)
    visibility: Literal["observed", "not_visible", "uncertain"]
    evidence: str = Field(min_length=1, max_length=800)
    source_id: str | None = None
    source_quote: str | None = None
    source_kind: str = "model_observation"
    cited_source_kind: str | None = None
    source_id_repaired_from: str | None = None

    @model_validator(mode="after")
    def visible_value(self):
        if self.visibility != "observed" and self.value is not None:
            raise ValueError("Unseen or uncertain facts must use value=null, not assert an appearance")
        if self.visibility == "observed" and not self.value:
            raise ValueError("Observed facts require a value")
        return self


class ObservationConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    attribute: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=2400)
    reason: str = Field(min_length=1, max_length=800)


class ObservationSourceError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(json.dumps(issues, ensure_ascii=False))


def validate_observation_sources(observation, sources):
    by_id = {source["id"]: source for source in sources}
    issues = []
    for index, fact in enumerate(observation.facts):
        # Repair provenance is computed here, never asserted by model output.
        fact.source_id_repaired_from = None
        if fact.source_id:
            source = by_id.get(fact.source_id)
            if not source or not fact.source_quote or fact.source_quote not in source["text"]:
                matches = [sid for sid, candidate in by_id.items()
                           if fact.source_quote and fact.source_quote in candidate["text"]]
                if len(matches) == 1:
                    # Repair only an unambiguous pointer to verbatim supplied evidence.
                    # This does not certify the model's interpretation of that evidence.
                    fact.source_id_repaired_from = fact.source_id
                    fact.source_id = matches[0]
                    continue
                issues.append(dict(code="reference_fact_source_invalid", path=f"facts[{index}].source_quote",
                    attribute=fact.attribute, source_id=fact.source_id, source_quote=fact.source_quote,
                    reason="source quote must exist verbatim in the supplied source" if source else "unknown source_id",
                    available_source_ids=list(by_id), matching_source_ids=matches))
        elif fact.source_quote or fact.source_kind != "model_observation":
            issues.append(dict(code="reference_fact_source_invalid", path=f"facts[{index}].source_id",
                attribute=fact.attribute, reason="model observations cannot claim user authority without a valid source citation"))
    for index, conflict in enumerate(observation.conflicts):
        if conflict.quote not in observation.description:
            issues.append(dict(code="reference_conflict_quote_invalid", path=f"conflicts[{index}].quote",
                attribute=conflict.attribute, quote=conflict.quote, reason="cite exact disputed description text"))
    if issues:
        raise ObservationSourceError(issues)


def sanitize_observation(observation, sources):
    """Do not pass unresolved assertions forward as accepted appearance prose."""
    by_id = {source["id"]: source for source in sources}
    payload = observation.model_dump()
    disputed = {x.attribute for x in observation.conflicts}
    for conflict in observation.conflicts:
        payload["description"] = payload["description"].replace(conflict.quote, "")
    payload["description"] = payload["description"].strip() or "Disputed appearance omitted; consult uncertainties."
    payload["facts"] = [
        {**fact.model_dump(), "source_kind": "model_observation",
         "cited_source_kind": by_id[fact.source_id]["kind"] if fact.source_id else None}
        for fact in observation.facts if fact.attribute not in disputed]
    # Bounded evidence window, prioritizing the unresolved disputes from this inspection.
    uncertainties = [*payload["conflicts"], *payload.get("uncertainties", [])]
    payload["uncertainties"] = list({json.dumps(item, sort_keys=True): item for item in uncertainties}.values())[:16]
    payload["conflicts"] = []
    return payload


def reference_sources(project, record):
    """Source authority comes from storage, never from the model's own label."""
    from .brief import directing_request_sources
    sources = []
    for key in ("approved_notes", "approved_description"):
        if record.get(key):
            sources.append(dict(id=key, kind="library_metadata", text=record[key]))
    if project is not None:
        sources.extend(directing_request_sources(project))
    return sources


def sourced_records(project, records):
    return [{**r, "sources": reference_sources(project, r)} for r in records]


def reference_context_signature(project, records):
    return digest([REFERENCE_POLICY_VERSION, project.script_text, project.shot_ids,
        project.asset_coverage_review.model_dump(mode="json") if project.asset_coverage_review else None,
        sourced_records(project, records)])


def reference_intent_signature(shot):
    payload = {k: getattr(shot, k) for k in ("scene_id", "title", "script_beat", "shot_type",
        "camera_angle", "camera_motion", "composition", "duration_s", "dialogue", "feedback")}
    payload["music_segment"] = shot.music_segment.model_dump(mode="json") if shot.music_segment else None
    return digest(payload)


def persist_reference_facts(project_id, references, check_current):
    from ...core.projects.store import project_dir
    from ...core.managed_runs.store import _project_lock
    path = project_dir(project_id) / "agent" / "reference_facts.json"
    with _project_lock(project_id):
        check_current()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {"version": REFERENCE_POLICY_VERSION, "references": {}}
        for ref in references:
            # The same asset's views retain asset identity; Picture order is shot-local.
            key = digest([ref["asset_id"], ref.get("file_key", "")])
            saved["references"][key] = {k: v for k, v in ref.items() if k != "picture_index"}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def reference_review_current(project, shot, records):
    review = shot.meta.get("material_review") or {}
    return (review.get("facts_signature") == reference_context_signature(project, records)
            and review.get("intent_signature") == reference_intent_signature(shot))


def certify_reference_prompt(project, shot):
    if project.mode.value != "director" or not shot.refs:
        return None
    from .material_review import capture_references
    records, _, _ = capture_references(shot)
    if not reference_review_current(project, shot, records):
        raise PromptFailureError("contract", "reference_facts_stale: review the current images and source requirements")
    review = shot.meta["material_review"]
    bindings = lambda refs: [(r.get("asset_id"), r.get("file_key"), r.get("picture_index"), r.get("content_sha256")) for r in refs]
    if bindings(review.get("references", [])) != bindings(records):
        raise PromptFailureError("contract", "reference_facts_stale: reviewed image bindings differ from current inputs")
    return {"version": REFERENCE_POLICY_VERSION, "signature": digest([
        review["facts_signature"], review["intent_signature"], review["references"],
        shot.prompt_sections.model_dump(mode="json")])}


def reference_contract_current(project, shot):
    try:
        certificate = certify_reference_prompt(project, shot)
        return certificate is None or certificate == shot.meta.get("prompt_reference_contract")
    except (ValueError, OSError, TypeError, KeyError):
        return False


def require_current_reference_contract(project, shot):
    if not reference_contract_current(project, shot):
        raise PromptFailureError("contract", "reference_facts_stale: refresh the prompt against current reference evidence")


REFERENCE_WRITER_CONTRACT = """
Reference evidence carries separate facts, sources, concerns and uncertainties. All visual facts
remain model observations; cited_source_kind identifies only a quoted source, not confirmation
or proof that the model interpretation is true. Follow the latest applicable explicit
user choice; do not let an older script or a speculative visual label silently replace it.
User request sources retain their original message identity and script version. They are
chronological requests, not blanket current approvals. A script change alone does not revoke
a request; apply later replacements and removals to earlier intent. Reference notes describe
the intended contribution and limits of that particular image, not facts visible in its pixels.
Resolve overlapping contributions using the user's selected roles: an explicit costume
reference controls the worn outfit over incidental clothing on an actor identity sheet.
Do not copy unrelated appearance details merely because a reference shows the same person.
If a crop hides a detail, preserve established design from its applicable reference; do
not infer absence or replace it with incidental identity-sheet clothing or footwear.
Do not assert values for not_visible/uncertain attributes. Conflicting or omitted descriptions
are unresolved evidence, not alternative appearance instructions. Preserve one narrative identity
across that person's different views. Choose camera, movement, staging and performance freely.
Keep the shot's composition and action consistent with all six prompt sections, including
opening/ending orientation. Remove superseded descriptions when revising the plan.
If an essential current requirement truly conflicts with pixels, report the specific conflict;
do not rewrite the user's choice or pretend the image shows something it does not.
"""
