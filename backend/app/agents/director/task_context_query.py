"""Version-pinned project catalog lookup; keys are never filesystem paths."""
import json

from .task_context_models import ContextPage
from .task_context_snapshot import capture_task_snapshot, ContextChanged


def read_task_context(state, query):
    if query.offset and query.expected_version is None:
        raise ValueError("CONTEXT_VERSION_REQUIRED: subsequent pages require the first page's version")
    snapshot = capture_task_snapshot(state.project_id)
    record = snapshot.sources.get(query.source_key)
    if record is None:
        raise ValueError("CONTEXT_SOURCE_NOT_ALLOWED: use this project's source catalog")
    if query.expected_version is not None and record.version != query.expected_version:
        raise ContextChanged(query.source_key)
    is_script = query.source_key == f"script:{state.project_id}"
    text = (record.payload["text"] if is_script else
            json.dumps(record.payload, ensure_ascii=False, sort_keys=True))
    end = min(len(text), query.offset + query.limit)
    next_offset = end if end < len(text) else None
    page = ContextPage(source_key=query.source_key, version=record.version, trust=record.trust,
        format="text" if is_script else "json", offset=query.offset, next_offset=next_offset,
        total_chars=len(text), text=text[query.offset:end], truncated=next_offset is not None)
    identity = (query.source_key, record.version, query.offset, query.limit)
    if identity not in state.read_pages:
        state.context_epoch += 1
        state.read_pages.add(identity)
    state.retrieved_versions[query.source_key] = record.version
    return page


def set_task_context(state, request):
    if request.kind == "shot_prompt":
        snapshot = capture_task_snapshot(state.project_id)
        if request.target_shot_id not in snapshot.shots:
            raise ValueError("CONTEXT_TARGET_NOT_ALLOWED: choose a Shot in this project")
    state.request = request.model_copy(update={"objective": state.request.objective,
        "target_shot_id": request.target_shot_id if request.kind == "shot_prompt" else None})
    return state
