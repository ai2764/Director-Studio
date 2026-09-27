"""Bounded, independently reviewed recovery of a managed planning conflict."""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from ...core.managed_runs.models import RunPlan


CameraField = Literal["shot_type", "camera_angle", "camera_motion", "composition"]


class CameraRefinement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_id: str
    changes: dict[CameraField, Annotated[str, Field(strict=True, min_length=1, max_length=3000)]]
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.changes or any(not value.strip() for value in self.changes.values()):
            raise ValueError("Camera refinement must contain nonempty values")
        return self


class PlanningRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: RunPlan
    camera_refinements: list[CameraRefinement]
    reason: str = Field(min_length=1, max_length=2000)


class PlanningReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid: StrictBool
    issues: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(max_length=10)

    @model_validator(mode="after")
    def consistent(self):
        if self.valid == bool(self.issues):
            raise ValueError("Review must accept without issues or reject with a concrete issue")
        return self


REPAIR_INSTRUCTIONS = """Reconcile a managed-run planning conflict. Return the requested JSON.
The earlier claims are hypotheses, not authoritative requirements. Re-evaluate them against
the script and directing_requests. Distinguish explicit user constraints from current camera
design and automatic continuity decisions. A saved shot_type can describe a destination framing;
it does not by itself mandate fixed framing for the whole clip. Feasible moves remain allowed.
Editorial cuts, reactions and narrative continuity do not require inheriting a previous tail.
Automatic review provenance is evidence of earlier system decisions, never a user instruction.
Honor saved revision_request/revision_history and confirmed_project_review decisions. Do not
infer user authority from a coordinator-generated request; if provenance is ambiguous and
would change an explicit instruction, leave the conflict unresolved for the user.
First reconsider unnecessary tail_handoffs. Retract a false conflict without rewriting the shot.
If camera fields genuinely contradict each other or an authored edit, propose the smallest
coherent camera_refinements on the reported shots only. Preserve the beat, dialogue, language,
duration, casting, references and explicit user camera constraints. Do not redesign other shots.
Do not change an authored cut into a continuous move merely to justify an automatic handoff.
Only shot_type, camera_angle, camera_motion and composition may change. Do not force a specific
framing, movement or aesthetic. Return the full candidate plan, with only visually necessary
tail_handoffs. A genuine unresolved explicit requirement stays in plan.storyboard_issues;
never erase it merely to pass validation. No tools, generation or business writes occur here.
"""

REVIEW_INSTRUCTIONS = """Independently review a managed planning repair. Return {valid, issues}.
Compare original_shots, candidate_shots, original_claims, candidate_plan, script and
directing_requests. The proposal's explanation is not proof. Verify every original claim:
it must be resolved by the candidate or be a defensible false positive. Verify every changed
camera field preserves explicit requirements and the authored beat. Check all candidate tail
handoffs for concrete visual necessity; shared actors, conversation and editorial continuity
alone do not require an inherited tail. Cuts may change framing without a bridging camera move.
Check the combined camera fields as a time-varying shot: a close-up destination and wider
opening are not inherently contradictory. Do not invent fixed-framing or aesthetic rules.
Check saved revision requests/history and applicable confirmed_project_review too. Automatic
provenance is not user authority; ambiguous authority must not waive a user constraint.
Reject if a real conflict remains, a camera change
violates an explicit constraint, or essential evidence is missing. Do not waive conflicts
just because the proposal says they are repaired. No additional changes may be proposed here.
"""


def shot_evidence(shot):
    fields = ("id", "title", "scene_id", "script_beat", "shot_type", "camera_angle",
              "camera_motion", "composition", "duration_s", "dialogue", "feedback")
    return {**{key: getattr(shot, key) for key in fields},
            "refs": [ref.model_dump(mode="json") for ref in shot.refs],
            "revision_request": shot.meta.get("prompt_revision_request", ""),
            "revision_history": shot.meta.get("prompt_revision_requests", []),
            "automatic_review_provenance": (shot.meta.get("material_review") or {}).get("decision")}


def parse_response(response, model):
    content = response.get("content") if isinstance(response, dict) and "content" in response else response
    return model.model_validate(json.loads(content) if isinstance(content, str) else content)


async def recover_plan(chat_fn, project, shots, plan, claims, requests, check_current):
    """One proposal and one independent review; nothing is persisted here."""
    from .asset_catalog import _script_hash
    confirmed = project.asset_coverage_review
    confirmed_data = None
    if confirmed and confirmed.script_hash == _script_hash(project.script_text):
        confirmed_data = {"notes": confirmed.notes, "recommendations": [
            item.model_dump(mode="json") for item in confirmed.recommendations
            if item.resolution != "pending"]}
    evidence = {"script": project.script_text, "directing_requests": requests,
                "confirmed_project_review": confirmed_data,
                "original_shots": [shot_evidence(shot) for shot in shots],
                "original_claims": [claim.model_dump(mode="json") for claim in claims],
                "previous_plan": plan.model_dump(mode="json")}
    raw = await chat_fn(REPAIR_INSTRUCTIONS, json.dumps(evidence, ensure_ascii=False),
                        format=PlanningRepair.model_json_schema())
    check_current()
    repair = parse_response(raw, PlanningRepair)
    if repair.plan.storyboard_issues:
        raise ValueError("Planning needs your decision: " + "; ".join(
            issue.reason for issue in repair.plan.storyboard_issues))
    allowed = {claim.shot_id for claim in claims}
    patches = {}
    for refinement in repair.camera_refinements:
        if refinement.shot_id not in allowed or refinement.shot_id in patches:
            raise ValueError("Planning repair targets an unrelated or duplicate Shot")
        patches[refinement.shot_id] = refinement.changes
    candidates = [shot.model_copy(update=patches.get(shot.id, {})) for shot in shots]
    review_input = {**evidence, "candidate_shots": [shot_evidence(shot) for shot in candidates],
                    "candidate_plan": repair.plan.model_dump(mode="json"),
                    "proposal": repair.model_dump(mode="json")}
    raw = await chat_fn(REVIEW_INSTRUCTIONS, json.dumps(review_input, ensure_ascii=False),
                        format=PlanningReview.model_json_schema())
    check_current()
    review = parse_response(raw, PlanningReview)
    if not review.valid:
        raise ValueError("Planning needs your decision: " + "; ".join(review.issues))
    receipt = {"kind": "planning_refinement", "claims": evidence["original_claims"],
               "proposal": repair.model_dump(mode="json"), "review": review.model_dump(mode="json"),
               "changes": [{"shot_id": shot.id, "fields": {
                   key: {"before": getattr(shot, key), "after": value}
                   for key, value in patches.get(shot.id, {}).items() if getattr(shot, key) != value}}
                   for shot in shots if patches.get(shot.id)]}
    return repair.plan, candidates, receipt
