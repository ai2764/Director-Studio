"""GET /api/doctor implementation."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

import httpx

from ...config import settings
from ..comfy.runtime import comfy_runtime, models_dir, user_custom_nodes_dir
from .requirements import FEATURES, NODE_PACK_HINTS, NODE_PACKS, combo_options, load_builtin


def _check(cid: str, category: str, status: str, zh: str, en: str, *, detail: str = "",
           fix_zh: str = "", fix_en: str = "", **extra: Any) -> dict[str, Any]:
    item = {"id": cid, "category": category, "status": status, "title": {"zh": zh, "en": en},
            "detail": detail}
    if fix_zh or fix_en:
        item["fix"] = {"zh": fix_zh, "en": fix_en}
    item.update(extra)
    return item


def _version(cmd: str) -> str | None:
    try:
        out = subprocess.run([cmd, "-version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0][:120] if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def check_tools() -> list[dict[str, Any]]:
    items = []
    for tool in ("ffmpeg", "ffprobe"):
        from ..media.binaries import locate_tool

        path, source = locate_tool(tool)
        version = _version(path) if path else None
        if version:
            origin = {"bundled": "bundled", "configured": "DS_FFMPEG_DIR", "path": "PATH"}.get(source, source)
            items.append(_check(f"tool.{tool}", "system", "ok", f"{tool} 可用", f"{tool} available",
                                detail=f"[{origin}] {path} — {version}"))
        else:
            items.append(_check(f"tool.{tool}", "system", "fail", f"未找到 {tool}", f"{tool} not found",
                                fix_zh=f"安装包自带 FFmpeg；源码运行请安装 FFmpeg 并确保 {tool} 在 PATH 中（Debian: apt install ffmpeg），或设置 DS_FFMPEG_DIR",
                                fix_en=f"Install FFmpeg and put {tool} on PATH"))
    data = Path(settings.data_dir)
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / f".doctor-{os.getpid()}"
        probe.write_text("ok")
        probe.unlink()
        free_gb = shutil.disk_usage(data).free / 1e9
        status = "ok" if free_gb > 20 else "warn"
        items.append(_check("system.data_dir", "system", status, "数据目录可写", "Data folder writable",
                            detail=f"{data} — free {free_gb:.1f} GB",
                            fix_zh="磁盘剩余空间不足 20GB，模型和视频可能放不下" if status == "warn" else "",
                            fix_en="Less than 20 GB free" if status == "warn" else ""))
    except OSError as exc:
        items.append(_check("system.data_dir", "system", "fail", "数据目录不可写", "Data folder not writable",
                            detail=f"{data}: {exc}", fix_zh="检查目录权限或 DS_DATA_DIR",
                            fix_en="Check permissions or DS_DATA_DIR"))
    return items


def parse_nvidia_smi(output: str) -> list[dict[str, Any]]:
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                gpus.append({"name": parts[0], "vram_total_mb": int(float(parts[1])),
                             "vram_free_mb": int(float(parts[2])), "driver": parts[3]})
            except ValueError:
                continue
    return gpus


def detect_gpus() -> tuple[list[dict[str, Any]], str]:
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        try:
            driver = pynvml.nvmlSystemGetDriverVersion()
            driver = driver.decode() if isinstance(driver, bytes) else driver
            gpus = []
            for i in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpus.append({"index": i, "name": name.decode() if isinstance(name, bytes) else name,
                             "vram_total_mb": mem.total // 2**20, "vram_free_mb": mem.free // 2**20,
                             "driver": driver})
            return gpus, "pynvml"
        finally:
            pynvml.nvmlShutdown()
    except Exception:  # noqa: BLE001 - no NVML / no driver / not installed
        pass
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total,memory.free,driver_version",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                return parse_nvidia_smi(out.stdout), "nvidia-smi"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return [], "none"


def check_gpu() -> list[dict[str, Any]]:
    gpus, source = detect_gpus()
    remote = settings.comfy_mode == "remote"
    if gpus:
        detail = "; ".join(f"{g['name']} {g['vram_total_mb']/1024:.0f} GB (free {g['vram_free_mb']/1024:.1f} GB), driver {g['driver']}"
                           for g in gpus)
        return [_check("gpu.nvidia", "gpu", "ok", "检测到 NVIDIA 显卡", "NVIDIA GPU detected", detail=detail,
                       gpus=gpus, source=source)]
    status = "ok" if remote else "warn"
    return [_check("gpu.nvidia", "gpu", status, "未检测到本机 NVIDIA 显卡", "No local NVIDIA GPU detected",
                   detail="Remote ComfyUI mode does not need a local GPU" if remote else "neither pynvml nor nvidia-smi is available",
                   fix_zh="" if remote else "安装 NVIDIA 驱动；没有显卡时请使用远程 ComfyUI 或 MiniMax 云",
                   fix_en="" if remote else "Install the NVIDIA driver, or use remote ComfyUI / MiniMax cloud",
                   gpus=[], source=source)]


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


async def check_comfy() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    base = settings.comfy_base_url.rstrip("/")
    meta: dict[str, Any] = {"mode": settings.comfy_mode, "url": base, "reachable": False}
    runtime = comfy_runtime.status()
    managed = runtime.get("managed")
    if managed and managed.get("state") != "ready":
        items.append(_check("comfy.managed", "comfy", "fail" if managed.get("state") in {"crashed", "failed"} else "warn",
                            "内置 ComfyUI 未就绪", "Managed ComfyUI not ready",
                            detail=f"state={managed.get('state')} {managed.get('last_error') or ''}",
                            fix_zh="查看 设置→生成 中的 ComfyUI 日志；检查 DS_COMFY_DIR / Python",
                            fix_en="See the ComfyUI log in Settings → Generation; check DS_COMFY_DIR / Python"))
    object_info: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0), trust_env=False) as client:
            stats = await _get_json(client, f"{base}/system_stats")
            object_info = await _get_json(client, f"{base}/object_info")
        system = stats.get("system", {}) if isinstance(stats, dict) else {}
        meta.update(reachable=True, version=system.get("comfyui_version"),
                    devices=[{"name": d.get("name"), "type": d.get("type"), "vram_total": d.get("vram_total"),
                              "vram_free": d.get("vram_free")} for d in (stats.get("devices") or [])])
        items.append(_check("comfy.reachable", "comfy", "ok", "ComfyUI 可连接", "ComfyUI reachable",
                            detail=f"{settings.comfy_mode} · v{system.get('comfyui_version')} · "
                                   + ", ".join(str(d.get('name')) for d in meta['devices'])))
    except Exception as exc:  # noqa: BLE001
        items.append(_check("comfy.reachable", "comfy", "fail", "无法连接 ComfyUI", "ComfyUI unreachable",
                            detail=f"{base}: {type(exc).__name__}: {str(exc)[:200]}",
                            fix_zh="在 设置→生成 中检查 ComfyUI 模式与地址；内置模式请查看启动日志",
                            fix_en="Check the ComfyUI mode/URL in Settings → Generation"))
    return items, {**meta, "object_info": object_info}


def _optional(name: str) -> bool:
    if name == "h3_ref2va_turbo4_video_context.api.json":
        if settings.h3_provider != "minimax" and name == settings.h3_builtin_workflow:
            return False
        return not settings.video_context_enabled
    if name.startswith("h3_ref2va"):
        return settings.h3_provider == "minimax" or name != settings.h3_builtin_workflow
    if name == "qwen_image_21_layout.api.json":
        return False
    return False


async def check_workflows(object_info: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    workflows = load_builtin(Path(settings.workflows_dir))
    mdir = models_dir()
    remote = settings.comfy_mode == "remote"
    managed = settings.comfy_mode == "managed"
    seen_models: set[tuple[str, str]] = set()
    for wf in workflows:
        zh, en = FEATURES.get(wf.name, (wf.name, wf.name))
        optional = _optional(wf.name)
        bad = "warn" if optional else "fail"
        if not object_info:
            items.append(_check(f"workflow.{wf.name}", "workflow", "skip", f"{zh}：未检查", f"{en}: not checked",
                                detail="ComfyUI unavailable", feature=wf.name, optional=optional))
            continue
        missing_nodes = sorted(n for n in wf.nodes if n not in object_info)
        node_items = []
        for node in missing_nodes:
            pack, hzh, hen = NODE_PACK_HINTS.get(node, ("unknown", "查找提供该节点的自定义节点包", "Find the custom node pack providing it"))
            info = NODE_PACKS.get(pack, {})
            item = {"class_type": node, "pack": pack, "fix": {"zh": hzh, "en": hen},
                    "repo": info.get("repo"), "license": info.get("license"), "bundled": info.get("bundled")}
            if info.get("repo") and not remote:
                # managed / local ComfyUI: user packs go to the DS data dir (kept on upgrade/uninstall)
                item["install_dir"] = str(user_custom_nodes_dir()) if managed else "ComfyUI/custom_nodes"
            node_items.append(item)
        missing_models = []
        for cls, input_name, folder, filename in wf.models:
            options = combo_options(object_info, cls, input_name)
            if options is None or filename in options:
                continue
            target = (mdir / folder / filename) if managed else None
            missing_models.append({
                "file": filename, "folder": folder, "loader": cls,
                "expected_path": str(target) if target else (f"<remote server>/ComfyUI/models/{folder}/" if remote
                                                               else f"ComfyUI/models/{folder}/"),
            })
            seen_models.add((folder, filename))
        status = "ok" if not missing_nodes and not missing_models else bad
        fix_zh = fix_en = ""
        if missing_models:
            where = "远程服务器的 ComfyUI/models/<目录>" if remote else (str(mdir) + "/<目录>" if managed else "ComfyUI/models/<目录>")
            where_en = ("ComfyUI/models/<folder> on the remote server" if remote
                        else (str(mdir) + "/<folder>" if managed else "ComfyUI/models/<folder>"))
            fix_zh = f"把缺失模型放到 {where} 后点“重新检查”"
            fix_en = f"Place the missing model files under {where_en}, then re-run the check"
        if missing_nodes:
            fix_zh = (fix_zh + "；" if fix_zh else "") + "安装缺失节点（见下方节点包提示）"
            fix_en = (fix_en + "; " if fix_en else "") + "install the missing custom nodes (see pack hints)"
        items.append(_check(
            f"workflow.{wf.name}", "workflow", status, f"{zh}：{'就绪' if status == 'ok' else '缺少依赖'}",
            f"{en}: {'ready' if status == 'ok' else 'missing requirements'}",
            detail=f"{len(wf.nodes)} node types, {len(wf.models)} model references",
            fix_zh=fix_zh, fix_en=fix_en, feature=wf.name, optional=optional,
            missing_nodes=node_items, missing_models=missing_models,
        ))
    return items


async def check_llm() -> list[dict[str, Any]]:
    from ..llm import get_llm_provider

    provider = get_llm_provider()
    try:
        ok = await asyncio.wait_for(provider.client.health(), 10)
    except Exception:  # noqa: BLE001
        ok = False
    base = settings.ollama_base_url if provider.provider_id == "ollama" else (settings.llm_base_url or "(default)")
    if provider.provider_id == "ollama" and settings.ollama_mode == "bundled":
        from ..llm.ollama_runtime import ollama_runtime

        status = ollama_runtime.status()
        base = f"bundled Ollama {status.get('version') or ''} · {base} · models {status['models_dir']}"
        if not status["available"]:
            return [_check("llm.reachable", "llm", "fail", "未找到内置 Ollama", "Bundled Ollama missing",
                           detail=str(status.get("error") or ""),
                           fix_zh="安装包应包含 runtime/ollama；源码运行请设置 DS_OLLAMA_DIR，或改用本机 Ollama",
                           fix_en="The package ships runtime/ollama; from source set DS_OLLAMA_DIR or use your own Ollama")]
        if ok:
            try:
                models = await asyncio.wait_for(provider.client.list_models(), 10)
            except Exception:  # noqa: BLE001
                models = []
            if not models:
                return [_check("llm.reachable", "llm", "warn", "内置 Ollama 已运行，但还没有模型",
                               "Bundled Ollama is running but has no models", detail=base,
                               fix_zh="在 设置→导演大模型→模型管理 中下载一个模型（例如 qwen3:8b）",
                               fix_en="Pull a model in Settings → Director LLM → Models (e.g. qwen3:8b)")]
    if provider.provider_id == "llama-swap" and settings.llama_swap_mode == "bundled":
        from ..llm.llama_swap_runtime import llama_swap_runtime

        status = llama_swap_runtime.status()
        base = f"built-in llama.cpp · {status.get('effective_url') or '-'} · models {status['models_dir']}"
        if not status["available"]:
            return [_check("llm.reachable", "llm", "fail", "未找到内置 llama.cpp", "Built-in llama.cpp missing",
                           detail=str(status.get("error") or ""),
                           fix_zh="安装包应包含 runtime/llama-swap 与 runtime/llama.cpp；源码运行请设置 DS_LLAMA_SWAP_DIR / DS_LLAMA_CPP_DIR",
                           fix_en="The package ships runtime/llama-swap and runtime/llama.cpp; from source set DS_LLAMA_SWAP_DIR / DS_LLAMA_CPP_DIR")]
        if ok and not status["models"]:
            return [_check("llm.reachable", "llm", "warn", "内置 llama.cpp 已运行，但还没有 GGUF 模型",
                           "Built-in llama.cpp is running but has no GGUF model", detail=base,
                           fix_zh="在 设置→依赖下载 的“导演大模型”中下载 Qwen3.8-27B",
                           fix_en="Download one under Settings → Dependencies → Director LLM (Qwen3.8-27B)")]
    if ok:
        return [_check("llm.reachable", "llm", "ok", "导演大模型可连接", "Director LLM reachable",
                       detail=f"{provider.provider_id} · {base}")]
    return [_check("llm.reachable", "llm", "fail", "导演大模型不可连接", "Director LLM unreachable",
                   detail=f"{provider.provider_id} · {base}",
                   fix_zh="启动 Ollama/LM Studio，或在 设置→大模型 中修改地址/Key；远程隧道可在“额外请求头”中填写鉴权",
                   fix_en="Start Ollama/LM Studio or fix the URL/key in Settings → LLM; tunnels may need extra headers")]


async def check_harness() -> list[dict[str, Any]]:
    if settings.director_agent_runtime != "harness":
        return [_check("harness.health", "agent", "skip", "Harness 未启用（legacy 模式）", "Harness disabled (legacy)")]
    url = f"{settings.harness_base_url}/health"
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {settings.harness_internal_token}"})
        ok = response.status_code == 200 and response.json().get("ok") is True
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        return [_check("harness.health", "agent", "ok", "Harness 运行正常", "Harness healthy", detail=settings.harness_base_url)]
    return [_check("harness.health", "agent", "fail", "Harness 不可用", "Harness unavailable", detail=settings.harness_base_url,
                   fix_zh="重启 Director Studio，或在 高级 中把运行时改为 legacy",
                   fix_en="Restart DS, or switch the agent runtime to legacy")]


def check_mcp() -> list[dict[str, Any]]:
    from ...integrations.comfy_mcp import _resolve_command

    items = []
    for key, attr in (("comfy-mcp", "comfy_mcp_command"), ("comfy", "comfy_mcp_comfy_bin")):
        value = getattr(settings, attr)
        resolved = _resolve_command(value)
        ok = bool(shutil.which(resolved) or Path(resolved).is_file())
        items.append(_check(f"mcp.{key}", "comfy", "ok" if ok else "fail", f"{key} 命令{'可用' if ok else '未找到'}",
                            f"{key} command {'found' if ok else 'missing'}", detail=resolved,
                            fix_zh="" if ok else "pip install -r backend/requirements.txt，或在 高级 中设置路径",
                            fix_en="" if ok else "pip install -r backend/requirements.txt or set the path in Advanced"))
    return items


async def run_doctor() -> dict[str, Any]:
    started = time.monotonic()
    comfy_items, comfy_meta = await check_comfy()
    object_info = comfy_meta.pop("object_info", {})
    results = await asyncio.gather(
        asyncio.to_thread(check_tools), asyncio.to_thread(check_gpu), check_workflows(object_info),
        check_llm(), check_harness(), asyncio.to_thread(check_mcp),
    )
    checks = comfy_items + [item for group in results for item in group]
    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "warn", "fail", "skip")}
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "comfy": comfy_meta,
        "models_dir": str(models_dir()),
        "summary": summary,
        "checks": checks,
    }
