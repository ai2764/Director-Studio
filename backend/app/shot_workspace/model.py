"""Narrow model adapter. No Director skill, chat history, casting or tool loop."""
import asyncio
import json

from ..config import settings
from ..agents.director.llm_plan_provider import DirectorLLMPlanProvider


class WorkspaceModel:
    def __init__(self):
        self.adapter = DirectorLLMPlanProvider()

    async def complete(self, system, payload, *, schema, images=None, max_tokens=4096):
        text = json.dumps(payload, ensure_ascii=False)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        if images:
            messages[-1]["images"] = images
        deadline = min(180, settings.llm_timeout_sec)
        async with asyncio.timeout(deadline), self.adapter._input_budget(system + text, output_tokens=max_tokens, images=bool(images)):
            result = await self.adapter.client.chat_response(self.adapter.model, messages=messages, format=schema,
                options={"num_predict": max_tokens, "temperature": 0.1}, **({"require_vision": True} if images else {}))
        if result.get("finish_reason") in {"length", "max_tokens"} or result.get("done_reason") in {"length", "max_tokens"}:
            raise ValueError("The model response was truncated. No partial result was applied.")
        return result.get("content") or ""

    async def complete_shot_description(self, system, user, *, schema):
        return await self.complete(system, json.loads(user), schema=schema, max_tokens=3072)
