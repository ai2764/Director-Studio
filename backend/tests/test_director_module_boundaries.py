"""Compatibility contracts for the Director chat module split."""

from app.agents.director import (
    chat,
    chat_context,
    chat_orchestrator,
    intent,
    tool_execution,
    tool_schema,
)
from app.core.projects.store import create_project
from app.agents.director.tool_handlers import actor as actor_tools
from app.agents.director.tool_handlers import project as project_tools
from app.agents.director.tool_handlers import layout as layout_tools
from app.agents.director.tool_handlers import media as media_tools
from app.agents.director.tool_handlers import casting as casting_tools


def test_chat_facade_reexports_intent_owners():
    assert chat.detect_intent is intent.detect_intent
    assert chat._explicit_gpt_image_intent is intent.explicit_gpt_image_intent
    assert chat._actor_design_intent is intent.actor_design_intent
    assert chat._validate_gpt_generation_prompt is intent.validate_gpt_generation_prompt


def test_chat_facade_reexports_tool_schema_owners(tmp_projects_dir):
    project = create_project("Boundary", "A door opens.")

    assert chat.DIRECTOR_TOOL_SCHEMAS is tool_schema.DIRECTOR_TOOL_SCHEMAS
    assert chat.GPT_REF_FRAME_TOOL is tool_schema.GPT_REF_FRAME_TOOL
    assert chat.ACTOR_DESIGN_TOOL is tool_schema.ACTOR_DESIGN_TOOL
    assert chat._director_tool_schemas(project) == tool_schema.director_tool_schemas(
        project
    )


def test_chat_facade_reexports_context_owners():
    assert chat._project_context_blob is chat_context.project_context_blob
    assert chat._gpt_generation_context_blob is chat_context.gpt_generation_context_blob


def test_chat_facade_exposes_tool_executor_compatibility_wrapper():
    assert callable(tool_execution.execute_tools)
    assert callable(chat._run_tools)


def test_chat_facade_reexports_orchestrator_result_contract():
    assert chat.ChatResult is chat_orchestrator.ChatResult
    assert callable(chat_orchestrator.orchestrate_chat)


def test_actor_tools_have_a_domain_handler():
    assert callable(actor_tools.handle_actor_tool)


def test_project_tools_have_a_domain_handler():
    assert callable(project_tools.handle_project_tool)


def test_layout_tools_have_a_domain_handler():
    assert callable(layout_tools.handle_layout_tool)


def test_media_and_casting_tools_have_domain_handlers():
    assert callable(media_tools.handle_media_tool)
    assert callable(casting_tools.handle_casting_tool)
