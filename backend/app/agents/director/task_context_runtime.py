"""Temporary per-turn view. Not a new workflow, budget or project authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json

from ...config import settings
from ...core.projects.store import load_project
from .context_metrics import context_mode, metrics_scope, observe_request
from .task_context_models import TaskContextState, TaskRequest
from .task_context_builder import build_task_packet
from .task_context_snapshot import capture_task_snapshot, ContextChanged

_task_state = ContextVar("director_task_context", default=None)


def task_context_enabled(project) -> bool:
    return context_mode(project) != "off"


@contextmanager
def task_context_scope(state):
    token = _task_state.set(state)
    try:
        yield state
    finally:
        _task_state.reset(token)


def current_task_context(project_id):
    state = _task_state.get()
    return state if state is not None and state.project_id == project_id else None


def scoped_task_turn(function):
    """Wrap existing entry points, including all exceptional/cancellation exits."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        project = load_project(kwargs["project_id"])
        from .intent import explicit_gpt_image_intent
        if not task_context_enabled(project) or explicit_gpt_image_intent(kwargs.get("message", "")):
            return await function(*args, **kwargs)
        state = current_task_context(project.id) or TaskContextState(project_id=project.id,
            request=TaskRequest(objective=kwargs.get("message", "")), retrieved_versions={})
        with task_context_scope(state), metrics_scope(project.id):
            return await function(*args, **kwargs)
    return wrapped


def present_task_tools(authorized_tools, state):
    from .tool_schema import TASK_CONTEXT_TOOLS
    selected = authorized_tools
    if state.request.kind == "shot_prompt":
        selected = [tool for tool in authorized_tools
                    if tool["function"]["name"] in {"write_prompt", "get_status", "inspect_asset"}]
    return [*selected, *TASK_CONTEXT_TOOLS]


def task_authority(project, tools):
    from ...core.managed_runs.context import managed_turn_scope
    managed = managed_turn_scope.get()
    return {"script_locked": project.script_locked,
        "managed_shot_id": managed.shot_id if managed and managed.project_id == project.id else None,
        "authorized_tools": [{"name": t["function"]["name"], "parameters": t["function"]["parameters"]}
                             for t in tools]}


def available_packet_chars(*, system, messages, tools, image_count=0, context_capacity=None):
    """Same labelled 4 chars/token heuristic and image allowance as Harness."""
    capacity = context_capacity or settings.director_num_ctx
    chars = (capacity - settings.director_num_predict - image_count * 2048) * 4
    text_messages = [{k: v for k, v in item.items() if k != "images"} for item in messages]
    used = len(system) + len(json.dumps(text_messages, ensure_ascii=False)) + len(json.dumps(tools, ensure_ascii=False))
    return max(0, chars - used)


def render_task_context(project, *, objective, authority, legacy_state, max_chars):
    mode = context_mode(project)
    if mode == "off":
        return legacy_state
    state = current_task_context(project.id)
    request = state.request if state else TaskRequest(objective=objective)
    try:
        snapshot = capture_task_snapshot(project.id)
        packet = build_task_packet(snapshot, request, authority=authority, max_chars=max_chars,
                                   extra_sources=tuple(state.retrieved_versions) if state else ())
        if state is not None:
            state.source_keys = list(packet.source_versions)
        rendered = json.dumps(packet.model_dump(mode="json"), ensure_ascii=False)
        observe_request(f"task_context.{mode}", [{"role": "system", "content": rendered}],
                        source_keys=packet.source_versions)
    except Exception:
        if mode == "shadow":
            return legacy_state  # Shadow cannot change the inference/operation outcome.
        raise
    return legacy_state if mode == "shadow" else rendered


def scoped_prompt_writer(function):
    @wraps(function)
    async def wrapped(self, shot_id, *, revision_request="", on_progress=None):
        from .service import _find_shot
        from ...core.projects.models import Shot
        shot = _find_shot(shot_id)
        project = load_project(shot.project_id) if shot else None
        if not task_context_enabled(project):
            return await function(self, shot_id, revision_request=revision_request, on_progress=on_progress)
        state = current_task_context(project.id) or TaskContextState(project_id=project.id,
            request=TaskRequest(kind="shot_prompt", target_shot_id=shot_id, objective=revision_request), retrieved_versions={})
        with task_context_scope(state), metrics_scope(project.id):
            receipt = state.writer_receipts.get(shot_id, {}) if context_mode(project) == "pilot" else {}
            if receipt.get("status") == "saved":
                return Shot.model_validate(receipt["shot"])
            saved = await function(self, shot_id, revision_request=revision_request, on_progress=on_progress)
            if context_mode(project) == "pilot":
                state.writer_receipts[shot_id] = {"status": "saved", "shot": saved.model_dump(mode="json")}
            return saved
    return wrapped


def build_writer_packet(project, shot_id, revision_request=""):
    from . import prompts
    from .skill_loader import with_director_skill
    from .reference_facts import REFERENCE_WRITER_CONTRACT
    from .dialogue_preflight import WRITER_CONTRACT
    from .prompt_retry import prompt_only_retry_active
    from .task_context_snapshot import ContextChanged
    snapshot = capture_task_snapshot(project.id)
    state = current_task_context(project.id)
    if state:
        for key, version in state.retrieved_versions.items():
            if not key.startswith("catalog:") and (key not in snapshot.sources or snapshot.sources[key].version != version):
                current = snapshot.sources.get(key)
                raise ContextChanged(key, expected_version=version,
                                     actual_version=current.version if current else None)
    target = snapshot.shots.get(shot_id, {})
    # Reserve existing template duplication; no outer chat history is sent to a writer.
    overhead = prompts.PROMPT_SECTIONS_USER_TEMPLATE + json.dumps(target, ensure_ascii=False) * 2
    if any(layout.get("origin_kind") == "clip_tail_frame" for layout in target.get("selected_layouts", [])):
        overhead += project.script_text
    max_chars = available_packet_chars(
        system=with_director_skill(prompts.H3_PROMPT_INSTRUCTIONS + REFERENCE_WRITER_CONTRACT + WRITER_CONTRACT,
                                   guides=("h3-prompt-writing",), writer_only=True),
        messages=[{"role": "user", "content": overhead}], tools=[])
    return build_task_packet(snapshot, TaskRequest(kind="shot_prompt", target_shot_id=shot_id,
        objective=state.request.objective if state else revision_request),
        authority={"allowed_mutations": ["write_prompt"], "prompt_only_retry": prompt_only_retry_active(),
                   "script_locked": project.script_locked}, max_chars=max_chars,
        extra_sources=tuple(state.retrieved_versions) if state else ())


def _resolved_context_gap(state, previous, packet):
    return packet.complete and packet.source_versions != previous.source_versions and any(
        state.retrieved_versions.get(item.get("source_key")) == packet.source_versions.get(item.get("source_key"))
        and packet.source_versions.get(item.get("source_key")) is not None
        and packet.source_versions.get(item.get("source_key")) != previous.source_versions.get(item.get("source_key"))
        for item in previous.missing)


def context_retry_ready(project_id, shot_id):
    state = current_task_context(project_id)
    if state is None:
        return False
    receipt = state.writer_receipts.get(shot_id, {})
    if receipt.get("status") != "context_required":
        return False
    try:
        packet = build_writer_packet(load_project(project_id), shot_id)
        return _resolved_context_gap(state, receipt["packet"], packet)
    except Exception:
        return False


def prepare_writer_packet(project, shot_id, revision_request=""):
    from .task_context_builder import ContextRequired
    mode = context_mode(project)
    if mode == "off":
        return None
    if mode == "shadow":
        try:
            comparison = build_writer_packet(project, shot_id, revision_request)
            observe_request("writer_context.shadow", [{"role": "user", "content": comparison.model_dump_json()}],
                            source_keys=comparison.source_versions)
        except Exception:
            pass  # Never enforce pilot completeness or mutate writer receipts in shadow.
        return None
    state = current_task_context(project.id)
    receipt = state.writer_receipts.get(shot_id, {}) if state else {}
    if receipt.get("status") == "started":
        raise ValueError("Writer already attempted in this turn; inspect its outcome before another request")
    try:
        packet = build_writer_packet(project, shot_id, revision_request)
    except ContextChanged as exc:
        from .task_context_models import TaskPacket
        if exc.expected_version is None or state is None:
            raise
        # No candidate or review has started. Preserve the stale read identity so
        # only an explicit refresh of this source can unlock the rejected preflight.
        gap = TaskPacket(project_id=project.id, task=TaskRequest(kind="shot_prompt",
            target_shot_id=shot_id, objective=state.request.objective), authority={}, facts={},
            source_versions=dict(state.retrieved_versions), complete=False,
            missing=[{"code": "CONTEXT_SOURCE_CHANGED", "source_key": exc.source_key,
                "expected_version": exc.expected_version, "current_version": exc.actual_version,
                "read": {"source_key": exc.source_key, "offset": 0},
                "action": "Refresh this source from its first page before retrying the prompt."}],
            available_context=[{"source_key": exc.source_key}])
        state.writer_receipts[shot_id] = {"status": "context_required", "packet": gap}
        raise ContextRequired(gap) from exc
    if state:
        state.source_keys = list(packet.source_versions)
    if receipt.get("status") == "context_required" and not _resolved_context_gap(state, receipt["packet"], packet):
        raise ContextRequired(receipt["packet"])
    if not packet.complete:
        if state:
            state.writer_receipts[shot_id] = {"status": "context_required", "packet": packet}
        raise ContextRequired(packet)
    if state:
        state.writer_receipts[shot_id] = {"status": "started"}
    return packet
