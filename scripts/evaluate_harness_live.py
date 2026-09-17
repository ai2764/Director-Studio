"""Opt-in live Qwen A/B. Isolated data; no media/tool networking permitted.

Run from repository root: python scripts/evaluate_harness_live.py --cases edit4_short
Raw fixture-only requests, provider metrics and persisted snapshots are retained.
This is measurement instrumentation, not a production runtime change.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--cases", default="edit4_short,create4_short,create12_short,edit24_short,edit24_long,edit24_overflow")
parser.add_argument("--runtimes", default="legacy,harness")
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--ctx", type=int, default=32768)
parser.add_argument("--predict", type=int, default=4096)
parser.add_argument("--timeout", type=int, default=900)
parser.add_argument("--label", default="qwen38-27b")
parser.add_argument("--title-only", action="store_true", help="Isolate context/batching from numeric tool-argument compatibility")
parser.add_argument("--repeat-constraint", action="store_true", help="Repeat old title suffix in current request as a matched-information control")
ARGS = parser.parse_args()
RUN = ROOT / ".tmp" / "harness-live-20260912" / f"{ARGS.label}-{time.strftime('%H%M%S')}"
RUN.mkdir(parents=True, exist_ok=False)
os.environ.update(DS_DATA_DIR=str(RUN / "data"), DS_LLM_PROVIDER="ollama", DS_OLLAMA_BASE_URL="http://127.0.0.1:11434", DS_DIRECTOR_PLAN_MODEL="qwen3.8:27b", DS_DIRECTOR_NUM_CTX=str(ARGS.ctx), DS_DIRECTOR_NUM_PREDICT=str(ARGS.predict), DS_HARNESS_TURN_TIMEOUT_SEC=str(ARGS.timeout))
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from app.config import settings
from app.api.projects import _make_chat_fn
from app.agents.director import chat, harness_runtime
from app.agents.director.service import DirectorService, _script_hash, _build_context
from app.agents.director.context_io import save_agent_context
from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
from app.core.llm import get_llm_provider
from app.core.projects.models import Shot, AssetCoverageReview
from app.core.projects.store import create_project, save_project, save_shot, list_shots, load_project
from app.core.projects.chat_history import append_chat_message
from app.core.vram import ollama_client
from app.core.vram import get_orchestrator

ACTIVE = None
PURPOSE = "turn"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def log(event, **values):
    row = {"event": event, "at": time.strftime("%H:%M:%S"), **values}
    with (RUN / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(row, ensure_ascii=False, default=str), flush=True)


OriginalSDK = ollama_client.AsyncClient


class RecordingSDK(OriginalSDK):
    async def chat(self, **kwargs):
        kwargs.setdefault("options", {})["seed"] = ARGS.seed
        case = ACTIVE
        if case is None:
            return await super().chat(**kwargs)
        index = len(case["llm_calls"]) + 1
        record = {"index": index, "purpose": PURPOSE, "request_chars": len(json.dumps(kwargs, ensure_ascii=False)), "message_count": len(kwargs.get("messages", []))}
        case["llm_calls"].append(record)
        write(case["path"] / f"llm-{index:02}-request.json", kwargs)
        log("llm_start", case=case["name"], runtime=case["runtime"], **record)
        begin = time.perf_counter()
        try:
            response = await super().chat(**kwargs)
            raw = response.model_dump(mode="json")
            write(case["path"] / f"llm-{index:02}-response.json", raw)
            record.update({k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "load_duration", "prompt_eval_duration", "eval_duration", "done_reason")})
            record["tool_names"] = [c["function"]["name"] for c in (raw.get("message", {}).get("tool_calls") or [])]
            return response
        except BaseException as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - begin, 3)
            log("llm_end", case=case["name"], runtime=case["runtime"], **record)


ollama_client.AsyncClient = RecordingSDK
ORIGINAL_SEND = httpx.AsyncClient.send
ORIGINAL_GENERATE = ollama_client.OllamaClient.generate


async def seeded_generate(self, model, prompt, **kwargs):
    kwargs.setdefault("options", {})["seed"] = ARGS.seed
    return await ORIGINAL_GENERATE(self, model, prompt, **kwargs)


async def record_generate_http(self, request, **kwargs):
    if request.url.port != 11434 or request.url.path != "/api/generate" or ACTIVE is None:
        return await ORIGINAL_SEND(self, request, **kwargs)
    body = json.loads(request.content)
    case = ACTIVE
    index = len(case["llm_calls"]) + 1
    record = {"index": index, "purpose": "warmup" if body.get("options", {}).get("num_predict") == 1 else "tool_internal_generate", "request_chars": len(request.content.decode()), "message_count": 1}
    case["llm_calls"].append(record)
    write(case["path"] / f"llm-{index:02}-request.json", body)
    begin = time.perf_counter()
    log("llm_start", case=case["name"], runtime=case["runtime"], **record)
    try:
        response = await ORIGINAL_SEND(self, request, **kwargs)
        raw = response.json()
        write(case["path"] / f"llm-{index:02}-response.json", raw)
        record.update({k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "load_duration", "prompt_eval_duration", "eval_duration", "done_reason")})
        return response
    except BaseException as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record["seconds"] = round(time.perf_counter() - begin, 3)
        log("llm_end", case=case["name"], runtime=case["runtime"], **record)


ollama_client.OllamaClient.generate = seeded_generate
httpx.AsyncClient.send = record_generate_http
ORIGINAL_TOOLS = chat._run_tools
SAFE_TOOLS = {"get_status", "save_storyboard", "revise_shot", "review_asset_coverage", "plan_shots", "plan"}


async def isolated_tools(**kwargs):
    requested = kwargs["tools"]
    if any(t["name"] not in SAFE_TOOLS for t in requested):
        raise RuntimeError("Evaluation blocks non-storyboard tools: " + ",".join(t["name"] for t in requested))
    row = {"tools": requested}
    ACTIVE["tool_batches"].append(row)
    result = await ORIGINAL_TOOLS(**kwargs)
    row["notes"] = result[0]
    row["payloads"] = list(kwargs.get("result_payloads") or [])
    log("tools", case=ACTIVE["name"], runtime=ACTIVE["runtime"], names=[t["name"] for t in requested], notes=result[0])
    return result


chat._run_tools = isolated_tools
ORIGINAL_DISPATCH = harness_runtime.BackendTurn.dispatch


async def record_dispatch(self, method, params):
    global PURPOSE
    previous = PURPOSE
    if method == "llm":
        PURPOSE = params.get("purpose", "turn")
    try:
        result = await ORIGINAL_DISPATCH(self, method, params)
        if method == "tool":
            ACTIVE["harness_tool_results"].append({"params": params, "result": result})
        return result
    finally:
        PURPOSE = previous


harness_runtime.BackendTurn.dispatch = record_dispatch


def history_for(kind):
    if kind in {"short", "all"}:
        return []
    rows = [{"role": "user", "content": "已确认的项目约定：所有本轮修改的镜头标题必须以【蓝灯】结尾。这是仍然有效的硬约束；不是角色姓名。不要改变其他镜头。"}, {"role": "assistant", "content": "确认，我会保留【蓝灯】标题后缀约定。"}]
    count = 80 if kind == "long" else 160
    for index in range(count):
        # Naturalistic resolved production chatter, not duplicated target commands.
        text = (f"Archive note {index:03}: workshop location discussion, already resolved. "
                "The crew measured the north corridor, checked the window diffusion, logged the old brass door and cleaned the storage shelves. "
                "A desk lamp was moved during rehearsal, then restored before the continuity photograph. "
                "Sound recorded room tone after the crew left; no new dialogue was requested. "
                "The art department compared faded paper samples, tested removable tape on a spare panel, and returned unused cloth to storage. "
                "These are archival observations, not requests to change the active storyboard. "
                f"Inventory batch {index*17+23} and rehearsal take {index+4} were closed without changing the agreed story. "
                "The next team confirmed receipt of these notes and scheduled its ordinary equipment inspection for a later day.")
        rows.append({"role": "user" if index % 2 == 0 else "assistant", "content": text})
    return rows


def fixture(name):
    task, kind = name.split("_", 1)
    create = task.startswith("create")
    count = int(task.removeprefix("create").removeprefix("edit"))
    beats = [f"事件{i:02}：修表匠在雨夜车站的桌前检查编号{i:02}的齿轮，放入对应抽屉。" for i in range(1, count+1)]
    script = "雨夜车站，一个修表匠独自整理旧钟表。无人说话，没有旁白。\n" + "\n".join(beats)
    project = create_project(f"LIVE EVAL {name}", script)
    project.script_locked = True
    project.asset_coverage_review = AssetCoverageReview(script_hash=_script_hash(script), status="skipped", recommendations=[], notes="Isolated text-only evaluation; empty asset inventory is intentional.")
    if not create:
        for i, beat in enumerate(beats, 1):
            shot = Shot(id=f"sht_eval_{i:03}", project_id=project.id, scene_id="sc01", title=f"原镜头{i:02}", script_beat=beat, duration_s=5, shot_type="medium close-up", camera_angle="eye level, left side", camera_motion="locked-off", composition="The watchmaker at left, numbered gear drawer at right.")
            save_shot(shot)
            project.shot_ids.append(shot.id)
    save_project(project)
    if not create:
        save_agent_context(project.id, _build_context(project, list_shots(project.id), phase="planned"))
    history = history_for(kind)
    if create:
        message = f"请直接调用 save_storyboard 保存恰好 {count} 个分镜，一一对应剧本中的事件01到事件{count:02}，每镜5秒，总计{count*5}秒。每个 script_beat 保留对应事件编号，每镜填写完整景别、机位、运镜、构图。全部无对白，dialogue=[]；没有可用素材，asset_matches=[]、voice_matches=[]，不要虚构素材ID。素材审查已经明确跳过。只做文本分镜，不启动图像、音频、视频任务，不调用旧 plan_shots。不要只写文字声称保存。"
        targets = []
    else:
        targets = list(range(1, count+1)) if kind == "all" else [2] if count == 4 else [3, 12, 24]
        suffix = "【蓝灯】" if history else ""
        duration_request = "" if ARGS.title_only else "; duration_s=7"
        instructions = "\n".join(f"- sht_eval_{i:03}: title=校正-{i:02}{duration_request}" for i in targets)
        memory = "标题还要遵守最早已经确认、至今有效的后缀约定，请从历史中找回，不要自创。" if suffix else ""
        if ARGS.repeat_constraint and suffix:
            memory += "为免遗漏，再明确一次：必须在新标题最后加【蓝灯】。"
        edited_fields = "标题" if ARGS.title_only else "标题和时长"
        title_only_rule = "本轮只修改 title，完全不要发送 duration_s 或其他未要求的字段。" if ARGS.title_only else ""
        message = f"精确执行以下修改清单，只修改清单列出的镜头，不要修改未列出的镜头：\n{instructions}\n{memory}{title_only_rule}请实际调用 revise_shot 保存，不要重建分镜表。所有镜头的ID和顺序不变；未列出的镜头所有字段保持原样（包括原来的5秒时长）；清单内镜头除{edited_fields}外的创作字段也不变。不要生成图像、音频或视频，不要仅用文字表示完成。"
    return project, history, message, {"create": create, "count": count, "targets": targets, "duration": 5 if ARGS.title_only else 7, "suffix": "【蓝灯】" if history else ""}


def assess(project, before, expected):
    after = [s.model_dump(mode="json") for s in list_shots(project.id)]
    checks = {"count": len(after) == expected["count"], "script_unchanged": load_project(project.id).script_text == project.script_text}
    if expected["create"]:
        checks["durations"] = all(s["duration_s"] == 5 for s in after)
        checks["beat_coverage"] = len(after) == expected["count"] and all(f"事件{i:02}" in s["script_beat"] for i, s in enumerate(after, 1))
        checks["complete_fields"] = all(all(s.get(k) for k in ("title", "script_beat", "shot_type", "camera_angle", "camera_motion", "composition")) for s in after)
        checks["no_dialogue"] = all(not s["dialogue"] for s in after)
    else:
        checks["identity_order"] = [s["id"] for s in before] == [s["id"] for s in after]
        checks["targets"] = True
        checks["untouched"] = True
        checks["target_other_fields"] = True
        original = {s["id"]: s for s in before}
        for i, shot in enumerate(after, 1):
            old = original.get(shot["id"], {})
            if i in expected["targets"]:
                checks["targets"] &= shot["title"] == f"校正-{i:02}" + expected["suffix"] and shot["duration_s"] == expected["duration"]
                for key in ("scene_id", "script_beat", "shot_type", "camera_angle", "camera_motion", "composition", "dialogue", "refs"):
                    checks["target_other_fields"] &= shot.get(key) == old.get(key)
            else:
                checks["untouched"] &= shot == old
    return checks, after


async def run_case(name, runtime, svc):
    global ACTIVE
    project, history, message, expected = fixture(name)
    path = RUN / f"{name}-{runtime}"
    path.mkdir()
    before = [s.model_dump(mode="json") for s in list_shots(project.id)]
    case = {"name": name, "runtime": runtime, "project_id": project.id, "path": path, "history_chars": sum(len(x["content"]) for x in history), "history_rows": len(history), "llm_calls": [], "tool_batches": [], "harness_tool_results": []}
    ACTIVE = case
    settings.director_agent_runtime = runtime
    write(path / "fixture.json", {"project": project.model_dump(mode="json"), "before": before, "history": history, "message": message, "expected": expected})
    for row in history:
        append_chat_message(project.id, **row)
    append_chat_message(project.id, role="user", content=message)
    async with httpx.AsyncClient(timeout=3) as client:
        queue = (await client.get("http://127.0.0.1:8188/queue")).json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("Comfy has active work; refusing to compete for GPU")
    log("case_start", case=name, runtime=runtime, project=project.id, history_rows=len(history), history_chars=case["history_chars"])
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(chat.handle_chat(project_id=project.id, message=message, svc=svc, history=history, chat_fn=await _make_chat_fn()), ARGS.timeout)
        case["reply"] = result.reply
        case["actions"] = result.actions
        append_chat_message(project.id, role="assistant", content=result.reply)
    except Exception as exc:
        case["error"] = f"{type(exc).__name__}: {exc}"
    case["seconds"] = round(time.perf_counter() - started, 3)
    case["checks"], after = assess(project, before, expected)
    case["passed"] = all(case["checks"].values()) and not case.get("error")
    case["provider_lease_released"] = get_orchestrator().owner is None
    write(path / "after.json", after)
    write(path / "result.json", case)
    log("case_end", case=name, runtime=runtime, passed=case["passed"], checks=case["checks"], seconds=case["seconds"], llm_calls=len(case["llm_calls"]), error=case.get("error"))
    return case


async def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_hex(32)
    settings.harness_base_url = f"http://127.0.0.1:{port}"
    settings.harness_internal_token = token
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
    env.update(DS_HARNESS_PORT=str(port), DS_HARNESS_INTERNAL_TOKEN=token)
    stdout = (RUN / "harness.out.log").open("w", encoding="utf-8")
    stderr = (RUN / "harness.err.log").open("w", encoding="utf-8")
    node = subprocess.Popen(["node", "--import", "tsx", "src/server.ts"], cwd=ROOT / "harness", env=env, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    results = []
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            for _ in range(50):
                try:
                    health = await client.get(settings.harness_base_url + "/health", headers={"Authorization": f"Bearer {token}"})
                    if health.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Owned sidecar did not start")
            models = (await client.get(settings.ollama_base_url + "/api/tags")).json()["models"]
            model = next(m for m in models if m["name"] == settings.director_plan_model)
            shown = (await client.post(settings.ollama_base_url + "/api/show", json={"model": settings.director_plan_model})).json()
            write(RUN / "environment.json", {"model": model, "model_configuration": {k: shown.get(k) for k in ("parameters", "capabilities", "details")}, "settings": {"num_ctx": ARGS.ctx, "num_predict": ARGS.predict, "seed": ARGS.seed, "temperature": "model default", "timeout": ARGS.timeout}, "args": vars(ARGS)})
        provider = get_llm_provider()
        svc = DirectorService(plan_provider=DirectorLLMPlanProvider(provider))
        log("run_start", path=str(RUN), model=settings.director_plan_model, num_ctx=ARGS.ctx, num_predict=ARGS.predict)
        for name in ARGS.cases.split(","):
            for runtime in ARGS.runtimes.split(","):
                results.append(await run_case(name, runtime, svc))
                write(RUN / "summary.json", results)
    finally:
        node.terminate()
        try:
            node.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node.kill()
            node.wait(timeout=5)
        stdout.close()
        stderr.close()
    log("run_end", path=str(RUN), passed=sum(r["passed"] for r in results), total=len(results))


if __name__ == "__main__":
    asyncio.run(main())
