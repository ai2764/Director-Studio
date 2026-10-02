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

    return project(deepcopy(context)), project(deepcopy(references))
