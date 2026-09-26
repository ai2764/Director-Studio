"""Read-only task tool adapter. No business actions, files or jobs are written."""
from ....core.projects.store import load_project
from ..context_metrics import context_mode
from ..task_context_models import ContextRead, TaskRequest
from ..task_context_query import read_task_context, set_task_context
from ..task_context_runtime import current_task_context
from ..tool_schema import TASK_CONTEXT_TOOL_NAMES


def handle_context_tool(*, name, args, project_id, result_payloads):
    if name not in TASK_CONTEXT_TOOL_NAMES:
        return False
    state = current_task_context(project_id)
    if state is None or context_mode(load_project(project_id)) != "pilot":
        raise ValueError("Task context tools are not enabled in this turn")
    if name == "read_task_context":
        payload = {"page": read_task_context(state, ContextRead.model_validate(args)).model_dump(mode="json")}
    else:
        if set(args) - {"kind", "target_shot_id"}:
            raise ValueError("Only kind and target_shot_id may change")
        set_task_context(state, TaskRequest.model_validate(args))
        payload = {"task": state.request.model_dump(mode="json")}
    result_payloads.append({"ok": True, "read_only": True, "context_epoch": state.context_epoch, **payload})
    return True
