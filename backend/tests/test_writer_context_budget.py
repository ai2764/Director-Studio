from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest


def test_writer_projection_keeps_requirements_once_and_preserves_evidence():
    from app.agents.director.writer_context import project_writer_context
    source = {"id": "request-1", "text": "Keep the blue paper lobby.", "kind": "user_directing_request"}
    context = {"extra": {"directing_brief": {"sources": [source]}, "other": "keep"}}
    intent = {"directing_requests": [source], "current_request": "Sing softly."}
    refs = [{"reference_evidence": {"sources": [source, {"id": "notes", "text": "A clock."}],
             "facts": [{"source_id": "request-1", "source_quote": "blue paper", "visibility": "observed"}]}}]
    before = json.dumps([context, intent, refs])
    projected_context, projected_refs = project_writer_context(context, intent, refs)
    rendered = json.dumps([projected_context, intent, projected_refs])
    assert rendered.count(source["text"]) == 1
    assert projected_refs[0]["reference_evidence"]["facts"] == refs[0]["reference_evidence"]["facts"]
    assert projected_refs[0]["reference_evidence"]["sources"][1]["text"] == "A clock."
    assert json.dumps([context, intent, refs]) == before


def test_projection_keeps_different_source_versions_with_same_id():
    from app.agents.director.writer_context import project_writer_context
    intent = {"directing_requests": [{"id": "request-1", "text": "New requirement."}]}
    evidence = [{"sources": [{"id": "request-1", "text": "Earlier requirement."}]}]
    _, projected = project_writer_context({}, intent, evidence)
    assert projected == evidence


@pytest.mark.asyncio
async def test_writer_blocks_overflow_before_generation_and_reserves_output():
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.core.prompt_errors import PromptContextOverflow
    lifecycle = SimpleNamespace(context_capacity=AsyncMock(return_value=65536),
        prompt_token_count=AsyncMock(return_value=64000))
    client = SimpleNamespace(generate=AsyncMock())
    provider = SimpleNamespace(client=client, lifecycle=lifecycle)
    plan = DirectorLLMPlanProvider(provider=provider, model="local-model")
    with pytest.raises(PromptContextOverflow):
        await plan.complete("system", "request")
    client.generate.assert_not_called()


@pytest.mark.asyncio
async def test_writer_translates_provider_context_error_without_retry():
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.core.prompt_errors import PromptContextOverflow
    client = SimpleNamespace(generate=AsyncMock(side_effect=RuntimeError(
        "request (79647 tokens) exceeds the available context size (65536 tokens)")))
    plan = DirectorLLMPlanProvider(provider=SimpleNamespace(client=client), model="local-model")
    with pytest.raises(PromptContextOverflow):
        await plan.complete("system", "request")
    assert client.generate.await_count == 1
