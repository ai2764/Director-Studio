from __future__ import annotations

import json

import pytest

from app.config import settings
from app.core.doctor import checks
from app.core.doctor.requirements import combo_options, extract

WORKFLOW = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
    "3": {"class_type": "CR Prompt List", "inputs": {"multiline_text": ["4", 0]}},
    "4": {"class_type": "StringConstantMultiline", "inputs": {"string": "a"}},
    "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": ["9", 0]}},
}
OBJECT_INFO = {
    "UNETLoader": {"input": {"required": {"unet_name": [["other.safetensors"]]}}},
    "VAELoader": {"input": {"required": {"vae_name": ["COMBO", {"options": ["vae.safetensors"]}]}}},
    "StringConstantMultiline": {"input": {}},
    "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [[]]}}},
}


def test_extract_requirements():
    req = extract("wf", WORKFLOW)
    assert req.nodes == {"UNETLoader", "VAELoader", "CR Prompt List", "StringConstantMultiline", "LoraLoaderModelOnly"}
    assert ("UNETLoader", "unet_name", "diffusion_models", "model.safetensors") in req.models
    assert all(m[0] != "LoraLoaderModelOnly" for m in req.models)  # linked inputs are skipped


def test_combo_option_formats():
    assert combo_options(OBJECT_INFO, "UNETLoader", "unet_name") == ["other.safetensors"]
    assert combo_options(OBJECT_INFO, "VAELoader", "vae_name") == ["vae.safetensors"]
    assert combo_options(OBJECT_INFO, "Missing", "x") is None


@pytest.mark.asyncio
async def test_check_workflows_reports_missing_nodes_and_models(tmp_path, monkeypatch):
    (tmp_path / "qwen_prop_master.api.json").write_text(json.dumps(WORKFLOW))
    monkeypatch.setattr(settings, "workflows_dir", tmp_path)
    monkeypatch.setattr(settings, "comfy_mode", "managed")
    monkeypatch.setattr(settings, "comfy_models_dir", str(tmp_path / "models"))
    items = await checks.check_workflows(OBJECT_INFO)
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "fail"
    assert [n["class_type"] for n in item["missing_nodes"]] == ["CR Prompt List"]
    assert item["missing_nodes"][0]["pack"] == "ds_nodes"
    assert item["missing_models"] == [{
        "file": "model.safetensors", "folder": "diffusion_models", "loader": "UNETLoader",
        "expected_path": str(tmp_path / "models" / "diffusion_models" / "model.safetensors"),
    }]
    assert item["fix"]["zh"] and item["fix"]["en"]


@pytest.mark.asyncio
async def test_remote_mode_hint_and_optional_workflow(tmp_path, monkeypatch):
    (tmp_path / "h3_ref2va_fast4.api.json").write_text(json.dumps(WORKFLOW))
    monkeypatch.setattr(settings, "workflows_dir", tmp_path)
    monkeypatch.setattr(settings, "comfy_mode", "remote")
    monkeypatch.setattr(settings, "h3_builtin_workflow", "h3_ref2va.api.json")
    item = (await checks.check_workflows(OBJECT_INFO))[0]
    assert item["status"] == "warn" and item["optional"] is True
    assert "remote server" in item["missing_models"][0]["expected_path"]


def test_nvidia_smi_parsing_and_no_gpu(monkeypatch):
    gpus = checks.parse_nvidia_smi("NVIDIA GeForce RTX 4090, 24564, 23000, 560.94\n")
    assert gpus == [{"name": "NVIDIA GeForce RTX 4090", "vram_total_mb": 24564, "vram_free_mb": 23000, "driver": "560.94"}]
    monkeypatch.setattr(checks, "detect_gpus", lambda: ([], "none"))
    monkeypatch.setattr(settings, "comfy_mode", "managed")
    assert checks.check_gpu()[0]["status"] == "warn"
    monkeypatch.setattr(settings, "comfy_mode", "remote")
    assert checks.check_gpu()[0]["status"] == "ok"


def test_tools_missing(monkeypatch):
    from app.core.media import binaries

    monkeypatch.setattr(binaries, "locate_tool", lambda _name: (None, "missing"))
    items = {i["id"]: i for i in checks.check_tools()}
    assert items["tool.ffmpeg"]["status"] == "fail" and "ffmpeg" in items["tool.ffmpeg"]["fix"]["zh"]


@pytest.mark.asyncio
async def test_h3_node_packs_report_repo_and_licence(tmp_path, monkeypatch):
    graph = {"1": {"class_type": "H3MemoryOptimization", "inputs": {}},
             "2": {"class_type": "MiniMaxH3MotionContext", "inputs": {}},
             "3": {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {}}}
    (tmp_path / "h3_ref2va_turbo4_video_context.api.json").write_text(json.dumps(graph))
    monkeypatch.setattr(settings, "workflows_dir", tmp_path)
    monkeypatch.setattr(settings, "comfy_mode", "managed")
    monkeypatch.setattr(settings, "comfy_models_dir", str(tmp_path / "models"))
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    item = (await checks.check_workflows({"KSampler": {"input": {}}}))[0]
    nodes = {n["class_type"]: n for n in item["missing_nodes"]}
    mem = nodes["H3MemoryOptimization"]
    assert mem["pack"] == "H3-Optimizations" and mem["repo"] == "https://github.com/Zironic/H3-Optimizations"
    assert mem["license"] is None and mem["bundled"] is False and "git clone" in mem["fix"]["en"]
    assert mem["install_dir"] == str(tmp_path / "data" / "comfy" / "custom_nodes")
    ctx = nodes["MiniMaxH3MotionContext"]
    assert ctx["pack"] == "ComfyUI-H3-Motion-Context" and ctx["license"] == "GPL-3.0" and ctx["bundled"] is True
    assert nodes["MiniMaxH3MotionContextTrim"]["repo"] == ctx["repo"]


def test_manifest_h3_component_matches_doctor_mapping():
    from pathlib import Path

    from app.core.doctor.requirements import NODE_PACK_HINTS, NODE_PACKS

    manifest = json.loads((Path(__file__).resolve().parents[2] / "installer" / "manifest.json").read_text())
    bundled = {n["name"]: n for n in manifest["custom_nodes"]}
    h3 = bundled["ComfyUI-H3-Motion-Context"]
    assert h3["optional"] and h3["component"] == "h3-continuation" and len(h3["commit"]) == 40
    for cls in ("MiniMaxH3MotionContext", "MiniMaxH3MotionContextTrim"):
        assert cls in h3["provides"] and NODE_PACK_HINTS[cls][0] == h3["name"]
    assert all(NODE_PACKS[name]["bundled"] for name in bundled)
    for entry in manifest["unbundled_custom_nodes"]:
        assert entry["name"] not in bundled and NODE_PACKS[entry["name"]]["bundled"] is False
