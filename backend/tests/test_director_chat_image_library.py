from __future__ import annotations

import base64
import json

import pytest

from app.agents.director.chat import handle_chat
from app.agents.director.tool_schema import director_tool_schemas
from app.config import settings
from app.core.library.store import list_assets
from app.core.projects.store import create_project


@pytest.fixture
def image_library_env(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    library = tmp_path / "library"
    projects.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "library_root", library)
    return projects


def test_chat_image_classification_tool_is_only_offered_for_upload_turns(
    image_library_env,
):
    project = create_project("Image classification", "INT. ROOM - DAY")

    ordinary = {
        item["function"]["name"]
        for item in director_tool_schemas(project)
    }
    upload_tools = director_tool_schemas(
        project,
        include_chat_image_import=True,
    )
    names = {item["function"]["name"] for item in upload_tools}

    assert "classify_chat_image" not in ordinary
    assert names == {"classify_chat_image"}
    parameters = upload_tools[0]["function"]["parameters"]
    assert parameters["required"] == [
        "image_index",
        "kind",
        "name",
        "notes",
        "confidence",
    ]
    assert parameters["properties"]["kind"]["enum"] == [
        "actors",
        "costumes",
        "scenes",
        "props",
        "layouts",
        "chat_only",
    ]


@pytest.mark.asyncio
async def test_visual_agent_classifies_names_describes_and_imports_uploaded_image(
    image_library_env,
):
    project = create_project("Image import", "INT. INTERVIEW ROOM - NIGHT")
    calls: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert {tool["function"]["name"] for tool in kwargs["tools"]} == {
                "classify_chat_image"
            }
            return {
                "content": "",
                "thinking": "The image and request describe an empty interview room.",
                "tool_calls": [
                    {
                        "name": "classify_chat_image",
                        "arguments": {
                            "image_index": 1,
                            "kind": "scenes",
                            "name": "Night Interview Room",
                            "notes": (
                                "Empty interview room with a metal table, two chairs, "
                                "and cool overhead lighting."
                            ),
                            "confidence": 0.96,
                        },
                    }
                ],
            }
        tool_result = json.loads(kwargs["messages"][-1]["content"])
        assert tool_result["imported"] is True
        assert tool_result["kind"] == "scenes"
        assert tool_result["file_key"] == "master"
        assert tool_result["asset_id"].startswith("scn_")
        return {
            "content": (
                f"Saved Night Interview Room as {tool_result['asset_id']} "
                "with file_key master."
            ),
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="把这张空房间图片作为后续审讯场景。",
        svc=object(),
        chat_fn=chat_fn,
        user_images_b64=[base64.b64encode(b"scene-image").decode("ascii")],
        user_image_captions=["room.png"],
    )

    assets = list_assets("scenes", project_id=project.id)
    assert len(assets) == 1
    assert assets[0].name == "Night Interview Room"
    assert "metal table" in assets[0].notes
    assert assets[0].files == {"master": "master.png"}
    assert assets[0].meta["source"] == "director_chat_upload"
    assert assets[0].meta["classification_confidence"] == 0.96
    assert assets[0].id in result.reply


@pytest.mark.asyncio
async def test_low_confidence_chat_image_stays_out_of_library(
    image_library_env,
):
    project = create_project("Unclear image", "INT. ROOM - DAY")
    tool_result: dict = {}

    async def chat_fn(system: str, user: str, **kwargs):
        if "messages" not in kwargs:
            return {
                "content": "",
                "thinking": "The image is too ambiguous to classify safely.",
                "tool_calls": [
                    {
                        "name": "classify_chat_image",
                        "arguments": {
                            "image_index": 1,
                            "kind": "props",
                            "name": "Unclear Object",
                            "notes": "A dark, partially obscured object.",
                            "confidence": 0.4,
                        },
                    }
                ],
            }
        tool_result.update(json.loads(kwargs["messages"][-1]["content"]))
        return {
            "content": "I kept the unclear image as a chat attachment only.",
            "thinking": "",
            "tool_calls": [],
        }

    await handle_chat(
        project_id=project.id,
        message="这是什么？",
        svc=object(),
        chat_fn=chat_fn,
        user_images_b64=[base64.b64encode(b"unclear-image").decode("ascii")],
        user_image_captions=["unclear.png"],
    )

    assert tool_result["imported"] is False
    assert tool_result["reason"] == "low_confidence"
    assert list_assets("props", project_id=project.id) == []
