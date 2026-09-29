"""Host-owned user-message identity, never a model-provided approval token."""
import uuid
from ...core.projects.chat_history import load_chat_history


def current_user_message_id(project_id: str, text: str) -> str:
    history = load_chat_history(project_id)
    if history and history[-1].role == "user" and history[-1].content == text:
        return history[-1].id
    # Non-chat callers still have a stable identity for the lifetime of their turn.
    return f"turn_{uuid.uuid4().hex}"
