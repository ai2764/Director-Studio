"""Conservative execution views; no new planning or approval authority."""
from copy import deepcopy
import hashlib
import json
import re

from .reference_facts import reference_context_signature, reference_intent_signature


def focused_execution_enabled(project):
    from ...config import settings
    return bool(settings.director_focused_execution and project is not None
                and project.mode.value == "director" and project.authoring_mode == "director_agent")


def execution_scope_signature(context, project):
    value = deepcopy(context)
    value.get("extra", {}).pop("execution_scope_certificate", None)
    return hashlib.sha256(json.dumps([project.script_text, project.shot_ids, value],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def focused_writer_context(context, project, shot):
    """Narrow shot summaries only. Global sources and unknown fields remain intact."""
    summaries = context.get("shot_summaries")
    certificate = context.get("extra", {}).get("execution_scope_certificate", {})
    # Legacy projects have no certified dependency graph. Keep every summary:
    # retaining a cross-shot instruction without its authored source is not enough.
    if (certificate.get("complete") is not True
            or certificate.get("target_shot_id") != shot.id
            or certificate.get("signature") != execution_scope_signature(context, project)
            or not isinstance(certificate.get("dependencies"), list)):
        return context
    if not isinstance(summaries, list) or shot.id not in project.shot_ids:
        return context
    order = project.shot_ids
    index = order.index(shot.id)
    selected = set(order[max(0, index - 1):index + 2])
    dependencies = [*certificate["dependencies"], *(shot.meta or {}).get("continuity_dependencies", [])]
    if shot.video_context and shot.video_context.source_shot_id:
        dependencies.append(shot.video_context.source_shot_id)
    for layout in shot.layout_refs:
        origin = getattr(layout, "origin", None)
        source = getattr(origin, "source_shot_id", None)
        if source:
            dependencies.append(source)
    if any(not isinstance(dep, str) or dep not in order for dep in dependencies):
        return context
    selected.update(dependencies)
    # A known dependency without a summary is a gap, never silently omitted.
    available = {item.get("id") for item in summaries if isinstance(item, dict)}
    if not selected <= available:
        return context
    result = deepcopy(context)
    result["shot_summaries"] = [item for item in result["shot_summaries"] if item.get("id") in selected]
    result["execution_scope"] = {
        "kind": "shot_prompt", "target_shot_id": shot.id,
        "shot_order": [{"id": sid, "index": i} for i, sid in enumerate(order, 1)],
        "omitted_shot_ids": [sid for sid in order if sid not in selected],
        "global_sources": "retained_in_full",
    }
    return result


def review_execution_snapshot(shot):
    data = shot.model_dump(mode="json")
    return {key: data[key] for key in (
        "scene_id", "title", "script_beat", "shot_type", "camera_angle",
        "camera_motion", "composition", "duration_s", "dialogue", "feedback",
        "dialogue_lines", "voice_refs", "music_segment", "source_audio_path")}


def _motion_without_speed(value):
    # Closed grammar: never infer that a free-form new camera path is equivalent.
    match = re.fullmatch(
        r"(?:(?:very )?(?:slow|slower|slowly|fast|faster|quickly) )?"
        r"(pan left|pan right|tilt up|tilt down|dolly forward|dolly backward)",
        value.strip().lower())
    return match.group(1) if match else None


def reusable_execution_review(project, shot, records, review, revision_request):
    if (not isinstance(review, dict) or revision_request.strip() or shot.video_context
            or not review.get("execution_input")
            or review.get("facts_signature") != reference_context_signature(project, records)
            or review.get("handoff_signature")):
        return None
    decision = review.get("decision", {})
    if decision.get("blocking_question") or decision.get("configuration_issues"):
        return None
    previous = review["execution_input"]
    current = review_execution_snapshot(shot)
    if not isinstance(previous, dict) or set(previous) != set(current):
        return None
    if any(previous[key] != current[key] for key in current if key != "camera_motion"):
        return None
    before = _motion_without_speed(previous["camera_motion"])
    after = _motion_without_speed(current["camera_motion"])
    if previous["camera_motion"] == current["camera_motion"] or not before or before != after:
        return None  # Existing unchanged-review logic already owns exact cache hits.
    result = deepcopy(review)
    result.update(intent_signature=reference_intent_signature(shot),
                  execution_input=current, execution_reuse="camera_speed_only")
    result["decision"]["rewrite_prompt"] = True
    return result
