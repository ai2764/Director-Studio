"""Opt-in neutral real-Ollama probe; all project/session writes are temporary."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings
from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.service import DirectorService
from app.core.projects.models import Shot
from app.core.projects.store import create_project, save_shot
from app.core.vram.ollama_client import OllamaClient


async def main(model: str):
    with tempfile.TemporaryDirectory(prefix="director-context-probe-") as temporary:
        settings.projects_dir = Path(temporary) / "projects"
        settings.projects_dir.mkdir()
        project = create_project("Neutral context probe", "A cat waits near a blue bench. A bird lands.")
        shots = []
        for index in range(12):
            shot = Shot(id=f"sht_probe{index:03d}", project_id=project.id, scene_id="scene",
                        title=f"Bench {index}", duration_s=4,
                        script_beat=f"A cat waits by bench {index}. Preserve BLUE.",
                        composition="Wide shot, calm daylight.")
            save_shot(shot)
            shots.append(shot)
        client = OllamaClient()
        calls = []
        async def chat_fn(system, user, **kwargs):
            assert kwargs["prepared_system"] is True
            messages = [{"role": "system", "content": system}, *kwargs["messages"]]
            purpose = kwargs["inference_purpose"]
            started = time.monotonic()
            print(json.dumps({"event": "request", "purpose": purpose, "system_chars": len(system),
                              "message_chars": sum(len(m.get("content", "")) for m in messages[1:]),
                              "tools": len(kwargs["tools"])}), flush=True)
            result = await client.chat_response(
                model, messages=messages, tools=kwargs["tools"] or None,
                options={"num_ctx": 32768, "num_predict": kwargs.get("max_output_tokens", 4096)},
            )
            data = {"purpose": purpose, "usage": result.get("usage"), "finish": result.get("done_reason"),
                    "seconds": round(time.monotonic() - started, 2),
                    "thinking_chars": len(result.get("thinking", "")), "reply_chars": len(result.get("content", "")),
                    "tool_calls": [c["name"] for c in result.get("tool_calls", [])]}
            calls.append(data)
            print(json.dumps({"event": "response", **data}), flush=True)
            return result
        history = [{"role": "user" if i % 2 == 0 else "assistant",
                    "content": ("User decision: the bench color stays BLUE. " if i == 0 else f"Resolved discussion {i}. ")
                    + "The neutral scene has a cat resting by a bench in daylight. No generation or edits were requested. " * 18}
                   for i in range(80)]
        steps = [
            ("chat", "Read only: what bench color did we agree on? Reply with the color. Do not modify anything."),
            ("chat", f"Read only: call get_status with shot_id {shots[1].id}, then tell me its saved duration. Do not modify anything."),
            ("chat", "What bench color remains agreed? Answer briefly, no changes."),
            ("compact", ""),
            ("chat", "What bench color remains agreed after the checkpoint? Answer briefly, no changes."),
        ]
        tool_reads = 0
        for index, (operation, message) in enumerate(steps):
            turn = BackendTurn(project.id, message, DirectorService(plan_provider=None), chat_fn,
                               history=history if index == 0 else [], compact_only=operation == "compact")
            process = await asyncio.create_subprocess_exec(
                "node", "--import", "tsx", "src/context-probe.ts",
                cwd=ROOT / "harness", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                limit=8 * 1024 * 1024,
            )
            config = {"sessionRoot": str(Path(temporary) / "sessions"),
                      "input": {"message": message, "history": [], "session_id": "neutral-probe",
                                "context_window": 28672, "max_steps": 6, "operation": operation}}
            process.stdin.write((json.dumps(config) + "\n").encode())
            await process.stdin.drain()
            try:
                while line := await process.stdout.readline():
                    event = json.loads(line)
                    if "result" in event:
                        if operation == "compact":
                            compacted = event["result"]["compaction"]
                            assert compacted["compacted"] and compacted["after_tokens"] < compacted["before_tokens"]
                            print(json.dumps({"event": "manual_compaction", **compacted}), flush=True)
                            break
                        reply = event["result"]["reply"]
                        assert reply.strip()
                        if index != 1:
                            assert "blue" in reply.lower(), reply
                        print(json.dumps({"event": "turn_complete", "index": index, "reply": reply}), flush=True)
                        break
                    if "error" in event:
                        raise RuntimeError(event["error"])
                    try:
                        if event["method"] == "tool":
                            if event["params"]["name"] != "get_status":
                                raise ValueError("Read-only probe rejects every mutation")
                            assert index == 1 and event["params"]["arguments"] == {"shot_id": shots[1].id}
                            tool_reads += 1
                        data = await turn.dispatch(event["method"], event["params"])
                        if event["method"] == "tool":
                            assert data["shot"]["id"] == shots[1].id and data["shot"]["duration_s"] == 4
                        response = {"ok": True, "data": data}
                    except Exception as exc:
                        response = {"ok": False, "error": str(exc)}
                    process.stdin.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
                    await process.stdin.drain()
                assert await process.wait() == 0
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        assert tool_reads > 0
        assert any(call["purpose"] == "compaction" for call in calls)
        print(json.dumps({"event": "complete", "model": model, "turns": sum(op == "chat" for op, _ in steps),
                          "tool_reads": tool_reads, "mutations": 0, "calls": calls}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.model))
