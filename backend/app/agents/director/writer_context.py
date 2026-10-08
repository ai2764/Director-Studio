"""Inference-only projections; authoritative source records remain untouched."""
from copy import deepcopy


def project_writer_context(context, intent, references):
    sources = {item["id"]: item for item in intent.get("directing_requests", [])
               if isinstance(item, dict) and "id" in item and "text" in item}

    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: project(item) for key, item in value.items()}
        source = sources.get(value.get("id")) if isinstance(value.get("id"), str) else None
        if source is not None and value.get("text") == source["text"]:
            result.pop("text", None)
            result["text_ref"] = f"intent.directing_requests[id={source['id']}].text"
        return result

    projected = project(deepcopy(context))
    target_id = intent.get("current_shot", {}).get("id")
    duration = intent.get("execution_duration_s")
    if target_id and duration is not None:
        # Only current target records, never historical quotes or neighboring shots.
        targets = list(projected.get("shot_summaries", []))
        canonical = projected.get("facts", {}).get("target")
        if isinstance(canonical, dict):
            targets.append(canonical)
        for target in targets:
            if isinstance(target, dict) and target.get("id") == target_id:
                if target.get("duration_s") != duration:
                    target["storyboard_duration_s"] = target.get("duration_s")
                target["duration_s"] = duration
    return projected, project(deepcopy(references))
