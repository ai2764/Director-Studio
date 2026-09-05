"""Durable, project-owned Director conversation history."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..paths import ensure_project_tree


class DirectorChatImage(BaseModel):
    url: str
    caption: str = ""
    shot_id: str | None = None


class DirectorChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str
    images: list[DirectorChatImage] = Field(default_factory=list)


def chat_history_path(project_id: str) -> Path:
    return ensure_project_tree(project_id) / "agent" / "chat.jsonl"


def load_chat_history(project_id: str) -> list[DirectorChatMessage]:
    path = chat_history_path(project_id)
    if not path.is_file():
        return []
    messages: list[DirectorChatMessage] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            messages.append(DirectorChatMessage.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(
                f"Invalid Director chat history at line {line_number}: {path}"
            ) from exc
    return messages


def append_chat_message(
    project_id: str,
    *,
    role: Literal["user", "assistant"],
    content: str,
    images: list[DirectorChatImage] | None = None,
) -> DirectorChatMessage:
    message = DirectorChatMessage(
        id=f"msg_{uuid.uuid4().hex}",
        role=role,
        content=content,
        created_at=datetime.now(timezone.utc).isoformat(),
        images=list(images or []),
    )
    path = chat_history_path(project_id)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(message.model_dump_json() + "\n")
    return message


def agent_history(messages: list[DirectorChatMessage]) -> list[dict[str, str]]:
    return [
        {"role": message.role, "content": message.content}
        for message in messages
        if message.content.strip()
    ]
