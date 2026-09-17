"""Opt-in real Qwen single-shot review; synthetic visual fixtures, isolated storage.

Requires an already running local Ollama. Never starts media generation or edits
user projects/settings. Exercises the real Python tool capability, not Node's loop.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".tmp" / f"material-review-live-{time.strftime('%Y%m%d-%H%M%S')}"
RUN.mkdir(parents=True)
os.environ.update(DS_DATA_DIR=str(RUN / "data"), DS_LLM_PROVIDER="ollama",
                  DS_OLLAMA_BASE_URL="http://127.0.0.1:11434", DS_DIRECTOR_PLAN_MODEL="qwen3.8:27b",
                  DS_DIRECTOR_NUM_CTX="32768", DS_DIRECTOR_NUM_PREDICT="4096")
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from PIL import Image, ImageDraw
from app.config import settings
from app.agents.director.context_io import save_agent_context
from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
from app.agents.director.service import DirectorService, _script_hash
from app.core.llm import get_llm_provider
from app.core.projects.models import AgentContext, Shot, ShotRef, RefRole
from app.core.projects.store import create_project, save_project, save_shot, load_shot
from app.core.schemas import LibraryAsset
from app.core.vram import get_orchestrator
from app.core.vram import ollama_client


def write(name, data):
    (RUN / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


CALLS = []
original_sdk = ollama_client.AsyncClient


class RecordingSDK(original_sdk):
    async def chat(self, **kwargs):
        kwargs.setdefault("options", {})["seed"] = 42
        index = len(CALLS) + 1
        record = {"index": index, "kind": "vision", "images": sum(len(m.get("images", [])) for m in kwargs["messages"])}
        CALLS.append(record)
        write(f"call-{index:02}-request.json", kwargs)
        print(json.dumps(record), flush=True)
        begin = time.perf_counter()
        result = await super().chat(**kwargs)
        raw = result.model_dump(mode="json")
        record.update({k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "done_reason")})
        record["seconds"] = round(time.perf_counter() - begin, 3)
        write(f"call-{index:02}-response.json", raw)
        print(json.dumps(record), flush=True)
        return result


ollama_client.AsyncClient = RecordingSDK
original_send = httpx.AsyncClient.send


async def record_generate(client, request, **kwargs):
    if request.url.port != 11434 or request.url.path != "/api/generate":
        return await original_send(client, request, **kwargs)
    body = json.loads(request.content)
    body.setdefault("options", {})["seed"] = 42
    request = httpx.Request(request.method, request.url, json=body)
    index = len(CALLS) + 1
    record = {"index": index, "kind": "warmup" if body["options"].get("num_predict") == 1 else "text", "images": 0}
    CALLS.append(record)
    write(f"call-{index:02}-request.json", body)
    begin = time.perf_counter()
    result = await original_send(client, request, **kwargs)
    raw = result.json()
    record.update({k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "done_reason")})
    record["seconds"] = round(time.perf_counter() - begin, 3)
    write(f"call-{index:02}-response.json", raw)
    print(json.dumps(record), flush=True)
    return result


httpx.AsyncClient.send = record_generate


async def main():
    async with httpx.AsyncClient(timeout=5) as client:
        queue = (await client.get("http://127.0.0.1:8188/queue")).json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("Comfy has active work; stop this test")
        shown = (await client.post(settings.ollama_base_url + "/api/show", json={"model": "qwen3.8:27b"})).json()
        if "vision" not in shown.get("capabilities", []):
            raise RuntimeError("Configured model has no advertised vision capability")
        write("environment.json", {"model": "qwen3.8:27b", "ctx": 32768, "predict": 4096, "seed": 42,
                                   "capabilities": shown.get("capabilities")})
    brief = "A fixed overhead view of nine separate colored square tiles laid on a neutral tabletop. No people, no text in the final clip; all tiles remain still."
    project = create_project("Isolated nine-reference review", brief)
    refs = []
    colors = ["red", "blue", "green", "yellow", "purple", "orange", "pink", "brown", "black"]
    for index, color in enumerate(colors, 1):
        adir = settings.library_root / "props" / f"prop_card_{index}"
        adir.mkdir(parents=True)
        # Deterministic test stimulus, not generated production media.
        card = Image.new("RGB", (600, 600), "white")
        draw = ImageDraw.Draw(card)
        draw.rectangle((120, 120, 480, 480), fill=color, outline="gray", width=4)
        card.save(adir / "selected.png", compress_level=0)
        asset = LibraryAsset(id=adir.name, kind="props", name=f"Tile {index}",
                             pipeline_id="fixture", job_id="fixture", created_at="2026-09-12T00:00:00Z",
                             files={"master": "selected.png"})
        (adir / "asset.json").write_text(asset.model_dump_json(), encoding="utf-8")
        refs.append(ShotRef(role=RefRole.prop, asset_id=asset.id, picture_index=index, file_key="master"))
    shot = Shot(id="sht_nine_refs", project_id=project.id, scene_id="sc01", title="Nine tiles",
                script_beat=brief, duration_s=6, refs=refs, meta={"material_review_pending": True})
    neighbor = shot.model_copy(update={"id": "sht_neighbor", "title": "Do not edit"}, deep=True)
    save_shot(shot)
    save_shot(neighbor)
    save_project(project.model_copy(update={"shot_ids": [shot.id, neighbor.id]}))
    save_agent_context(project.id, AgentContext(project_id=project.id, script_hash=_script_hash(brief)))
    write("fixture.json", {"shot": shot.model_dump(mode="json"), "expected_colors": colors})
    svc = DirectorService(plan_provider=DirectorLLMPlanProvider(get_llm_provider(), model="qwen3.8:27b"))
    turn = BackendTurn(project.id, "Review every current ref for shot 1, then prepare its brief and H3 prompt.", svc, None)
    await turn.dispatch("context", {})
    begin = time.perf_counter()
    result = await asyncio.wait_for(turn.dispatch("tool", {"call_id": "review", "name": "write_prompt",
                                                           "arguments": {"shot_id": shot.id}}), 600)
    after = load_shot(project.id, shot.id)
    reviewed = (after.meta.get("material_review") or {}).get("references", [])
    checks = {"tool_ok": result.get("ok") is True, "all_nine": len(reviewed) == 9,
              "pending_cleared": after.meta.get("material_review_pending") is False,
              "neighbor_unchanged": load_shot(project.id, neighbor.id) == neighbor,
              "refs_unchanged": after.refs == shot.refs,
              "colors_observed": len(reviewed) == 9 and all(color in r["description"].lower() for color, r in zip(colors, reviewed)),
              "single_image_requests": len([c for c in CALLS if c["kind"] == "vision"]) == 9 and all(c["images"] <= 1 for c in CALLS),
              "lease_released": get_orchestrator().owner is None}
    summary = {"result": result, "checks": checks, "passed": all(checks.values()),
               "seconds": round(time.perf_counter() - begin, 3), "calls": CALLS}
    write("after.json", after.model_dump(mode="json"))
    write("summary.json", summary)
    print(json.dumps({"path": str(RUN), **summary}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
