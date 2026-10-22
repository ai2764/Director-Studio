import json

import pytest


@pytest.mark.asyncio
async def test_call_measurement_attributes_phase_and_counts_without_content(tmp_path, monkeypatch):
    from app.config import settings
    from app.agents.director.progress import report_phase
    from app.core.llm.performance import measure_call
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    events = []
    async def progress(event):
        events.append(event)
    async with report_phase(progress, "prompt_writing", "Writing"):
        async with measure_call("writer.generate", model="local", image_count=2, text_chars=120) as call:
            call["result"] = {"content": "PRIVATE ANSWER", "usage": {"input_tokens": 30, "output_tokens": 7}}
    rows = [json.loads(line) for line in (tmp_path / "logs" / "llm-performance.jsonl").read_text().splitlines()]
    row = rows[0]
    assert row["phase"] == "prompt_writing"
    assert row["image_count"] == 2
    assert row["input_tokens"] == 30
    assert row["output_tokens"] == 7
    assert row["elapsed_ms"] >= 0
    assert row["status"] == "completed"
    assert "PRIVATE" not in json.dumps(rows)
    assert any(event.get("phase") == "llm_call" for event in events)
    assert any(row["kind"] == "phase" and row["phase"] == "prompt_writing" for row in rows)


@pytest.mark.asyncio
async def test_failed_calls_are_measured_and_telemetry_cannot_fail_inference(tmp_path, monkeypatch):
    from app.config import settings
    from app.core.llm.performance import measure_call
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with pytest.raises(ValueError, match="private provider error"):
        async with measure_call("chat.turn", model="local", image_count=0, text_chars=12):
            raise ValueError("private provider error")
    path = tmp_path / "logs" / "llm-performance.jsonl"
    assert json.loads(path.read_text())["status"] == "failed"
    assert "private provider error" not in path.read_text()
    # A broken telemetry directory cannot stop a model request.
    blocked = tmp_path / "blocked"
    blocked.write_text("file")
    monkeypatch.setattr(settings, "data_dir", blocked)
    async with measure_call("writer.vision", model="local", image_count=1, text_chars=12):
        pass


@pytest.mark.asyncio
async def test_writer_and_chat_calls_emit_performance_with_provider_usage(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.core.llm.usage import ChatUsageReporter
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.agents.director.progress import report_phase
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    async def generate(model, prompt):
        return "text result"
    async def chat(model, prompt, **kwargs):
        return "vision result"
    async def response(model, **kwargs):
        return {"content": "bounded result", "usage": {"input_tokens": 80, "output_tokens": 12}}
    client = SimpleNamespace(generate=generate, chat=chat, chat_response=response)
    provider = DirectorLLMPlanProvider(SimpleNamespace(client=client), model="local")
    async with report_phase(None, "prompt_writing", "Writing"):
        assert await provider.complete("SYSTEM", "USER") == "text result"
    assert await provider.complete_with_images("SYSTEM", "USER", images=["PRIVATE_BASE64"]) == "vision result"
    assert await provider.complete_bounded("SYSTEM", "USER", max_tokens=100) == "bounded result"
    await ChatUsageReporter(None, provider="local").call(client, "local", messages=[{"role": "user", "content": "PRIVATE_INPUT"}])
    rows = [json.loads(line) for line in (tmp_path / "logs" / "llm-performance.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["kind"] == "llm_call"]
    assert [(r["path"], r["image_count"]) for r in rows] == [
        ("writer.generate", 0), ("writer.vision", 1), ("writer.bounded", 0), ("chat.turn", 0)]
    assert rows[0]["phase"] == "prompt_writing"
    assert rows[0]["input_tokens"] is None
    assert rows[-1]["input_tokens"] == 80
    assert rows[-1]["output_tokens"] == 12
    assert "PRIVATE" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_chat_measurement_accepts_null_assistant_content_and_null_usage(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.core.llm.usage import ChatUsageReporter
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    async def response(model, **kwargs):
        return {"content": "ok", "usage": None}
    result = await ChatUsageReporter(None, provider="local").call(SimpleNamespace(chat_response=response),
        "local", messages=[{"role": "assistant", "content": None, "tool_calls": []},
                           {"role": "user", "content": "Hello"}])
    assert result["content"] == "ok"
    row = json.loads((tmp_path / "logs" / "llm-performance.jsonl").read_text())
    assert row["text_chars"] == 5
    assert row["input_tokens"] is None
