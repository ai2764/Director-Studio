"""Static requirement extraction from DS built-in ComfyUI API workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from ..deps import catalog as deps_catalog

# loader class_type -> (input names, ComfyUI model folder)
LOADER_FOLDERS: dict[str, tuple[tuple[str, ...], str]] = {
    "UNETLoader": (("unet_name",), "diffusion_models"),
    "CLIPLoader": (("clip_name",), "text_encoders"),
    "DualCLIPLoader": (("clip_name1", "clip_name2"), "text_encoders"),
    "TripleCLIPLoader": (("clip_name1", "clip_name2", "clip_name3"), "text_encoders"),
    "VAELoader": (("vae_name",), "vae"),
    "LoraLoaderModelOnly": (("lora_name",), "loras"),
    "LoraLoader": (("lora_name",), "loras"),
    "CheckpointLoaderSimple": (("ckpt_name",), "checkpoints"),
    "CLIPVisionLoader": (("clip_name",), "clip_vision"),
    "UpscaleModelLoader": (("model_name",), "upscale_models"),
    "ControlNetLoader": (("control_net_name",), "controlnet"),
}

# Node packs, their hints and the workflow labels all come from the dependency catalog
# (app/core/deps/catalog.json), the single source of truth shared with the downloader.


def _pack_hint(pack: dict[str, Any]) -> tuple[str, str]:
    if not pack.get("installable"):
        return pack.get("note_zh") or pack["name"], pack.get("note_en") or pack["name"]
    lic_zh = "无许可证，DS 不内置，由你从作者处下载" if pack.get("unlicensed") else f"{pack.get('license')}"
    lic_en = "no licence, so DS does not bundle it; you download it from the author" if pack.get("unlicensed") \
        else f"{pack.get('license')}"
    repo = pack["repo"]
    return (f"在 设置 → 工作流依赖 中一键下载 {pack['name']}（{lic_zh}）；或在下方“用户节点目录”中执行 git clone {repo} ，然后重启 ComfyUI",
            f"Download {pack['name']} in Settings → Workflow dependencies ({lic_en}); or git clone {repo} inside the "
            "user node folder shown below, then restart ComfyUI")


# class_type -> (pack id, zh hint, en hint). Packs present in /object_info are
# reported from their python_module instead.
NODE_PACK_HINTS: dict[str, tuple[str, str, str]] = {
    cls: (pack["id"], *_pack_hint(pack)) for pack in deps_catalog.node_packs() for cls in pack["provides"]
}

# pack id -> where it comes from. "bundled": shipped with DS / the installer / ComfyUI itself.
NODE_PACKS: dict[str, dict[str, Any]] = {
    pack["id"]: {"repo": pack.get("repo") if pack.get("installable") else None, "license": pack.get("license"),
                 "bundled": bool(pack.get("bundled") or pack.get("ships_with") == "comfyui"), "installable": bool(pack.get("installable")),
                 **({"component": "h3-continuation"} if pack.get("ships_with") == "installer-optional" else {})}
    for pack in deps_catalog.node_packs()
}

FEATURES: dict[str, tuple[str, str]] = {wf["file"]: (wf["zh"], wf["en"]) for wf in deps_catalog.workflows()}


@dataclass
class WorkflowRequirements:
    name: str
    nodes: set[str] = field(default_factory=set)
    models: list[tuple[str, str, str, str]] = field(default_factory=list)  # (class, input, folder, file)


def extract(name: str, graph: dict[str, Any]) -> WorkflowRequirements:
    req = WorkflowRequirements(name=name)
    for node in graph.values():
        if not isinstance(node, dict) or "class_type" not in node:
            continue
        cls = str(node["class_type"])
        req.nodes.add(cls)
        loader = LOADER_FOLDERS.get(cls)
        if loader:
            inputs = node.get("inputs") or {}
            for input_name in loader[0]:
                value = inputs.get(input_name)
                if isinstance(value, str) and value:
                    req.models.append((cls, input_name, loader[1], value))
    return req


def load_builtin(workflows_dir: Path) -> list[WorkflowRequirements]:
    out: list[WorkflowRequirements] = []
    for path in sorted(workflows_dir.glob("*.api.json")):
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(graph, dict):
            out.append(extract(path.name, graph))
    return out


def combo_options(object_info: dict[str, Any], cls: str, input_name: str) -> list[str] | None:
    spec = (object_info.get(cls) or {}).get("input") or {}
    entry = (spec.get("required") or {}).get(input_name) or (spec.get("optional") or {}).get(input_name)
    if not entry:
        return None
    first = entry[0]
    if isinstance(first, list):
        return [str(v) for v in first]
    if first == "COMBO" and len(entry) > 1 and isinstance(entry[1], dict):
        return [str(v) for v in entry[1].get("options") or []]
    return None
