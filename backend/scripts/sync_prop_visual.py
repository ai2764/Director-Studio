"""Regenerate the runnable Prop visual workflow from its API graph."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"
API_PATH = WORKFLOWS / "qwen_prop_master.api.json"
VISUAL_PATH = WORKFLOWS / "DS_qwen_prop_master_visual.json"
SCENE_VISUAL = WORKFLOWS / "DS_qwen_scene_multiangle_visual.json"
ACTOR_VISUAL = WORKFLOWS / "DS_qwen_actor_workbench_visual.json"


POSITIONS = {
    "7": (0, 360),
    "3": (420, 360), "17": (420, 500), "18": (420, 650), "5": (420, 800),
    "2": (420, 940), "4": (420, 1100),
    "10": (900, 300), "20": (900, 650), "21": (900, 1000), "14": (900, 1350),
    "6": (1300, 120),
    "11": (1300, 300), "23": (1300, 650), "24": (1300, 1000),
    "12": (1660, 300), "25": (1660, 650), "26": (1660, 1000),
    "27": (1940, 300), "28": (1940, 650), "29": (1940, 1000),
    "30": (2280, 760), "31": (2620, 520), "13": (2980, 520),
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def template_catalog() -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for path in (VISUAL_PATH, SCENE_VISUAL, ACTOR_VISUAL):
        for node in load(path)["nodes"]:
            catalog.setdefault(node["type"], node)
    # The API node is supported by ComfyUI under this name, while the existing
    # Director visual graphs use the SD3-labelled node as the UI template.
    if "EmptyLatentImage" not in catalog and "EmptySD3LatentImage" in catalog:
        catalog["EmptyLatentImage"] = copy.deepcopy(catalog["EmptySD3LatentImage"])
    return catalog


def widget_values(class_type: str, inputs: dict, template: dict) -> list:
    if class_type == "CLIPLoader":
        return [inputs["clip_name"], inputs["type"], inputs.get("device", "default")]
    if class_type == "UNETLoader":
        return [inputs["unet_name"], inputs.get("weight_dtype", "default")]
    if class_type == "VAELoader":
        return [inputs["vae_name"]]
    if class_type == "LoraLoaderModelOnly":
        return [inputs["lora_name"], inputs["strength_model"]]
    if class_type == "ModelSamplingAuraFlow":
        return [inputs["shift"]]
    if class_type in {"EmptyLatentImage", "EmptySD3LatentImage"}:
        return [inputs["width"], inputs["height"], inputs["batch_size"]]
    if class_type == "LoadImage":
        return [inputs["image"], "image"]
    if class_type == "TextEncodeQwenImageEditPlus":
        return [inputs["prompt"]]
    if class_type == "KSampler":
        return [
            inputs["seed"], "fixed", inputs["steps"], inputs["cfg"],
            inputs["sampler_name"], inputs["scheduler"], inputs["denoise"],
        ]
    if class_type == "VAEDecode":
        return []
    if class_type == "ImageScale":
        return [inputs["upscale_method"], inputs["width"], inputs["height"], inputs["crop"]]
    if class_type == "ImageConcatMulti":
        return [inputs["inputcount"], inputs["direction"], inputs["match_image_size"]]
    if class_type == "SaveImage":
        return [inputs["filename_prefix"]]
    return list(template.get("widgets_values") or [])


def main() -> None:
    api = load(API_PATH)
    templates = template_catalog()
    nodes: list[dict] = []
    by_id: dict[str, dict] = {}

    note = copy.deepcopy(templates["MarkdownNote"])
    note.update(id=1000, pos=[0, -80], size=[780, 360], order=0)
    note["title"] = "Director Studio · Prop Reference Sheet"
    note["widgets_values"] = [
        "# Runnable graph generated from qwen_prop_master.api.json\n\n"
        "One uploaded prop runs through three independent single-view branches: "
        "Hero 3/4, Front, and Side/Rear. Each branch forbids sheets, labels, and multiple "
        "views. The final 1536×1536 master is composed deterministically: Hero 1024×1536 "
        "on the left; Front and Side/Rear 512×768 stacked on the right.\n\n"
        "Qwen Edit 2511 → Multi-angle LoRA 1.0 → Lightning 4step 1.0 → shift 3.1 → "
        "4 steps / CFG 1.0 / Euler simple."
    ]
    for output in note.get("outputs", []):
        output["links"] = []
    nodes.append(note)

    for order, (api_id, spec) in enumerate(api.items(), start=1):
        class_type = spec["class_type"]
        if class_type not in templates:
            raise KeyError(f"No visual template for {class_type}")
        node = copy.deepcopy(templates[class_type])
        node.update(
            id=int(api_id),
            type=class_type,
            pos=list(POSITIONS[api_id]),
            order=order,
            mode=0,
            title=(spec.get("_meta") or {}).get("title") or class_type,
        )
        node["flags"] = {}
        for inp in node.get("inputs", []):
            inp["link"] = None
        for out in node.get("outputs", []):
            out["links"] = []
        node["widgets_values"] = widget_values(class_type, spec["inputs"], node)
        nodes.append(node)
        by_id[api_id] = node

    links: list[list] = []
    link_id = 1
    for target_id, spec in api.items():
        target = by_id[target_id]
        input_slots = {item["name"]: i for i, item in enumerate(target.get("inputs", []))}
        for input_name, value in spec["inputs"].items():
            if not (isinstance(value, list) and len(value) == 2):
                continue
            source_id, source_slot = str(value[0]), int(value[1])
            if input_name not in input_slots:
                raise KeyError(f"{target_id}:{target['type']} missing input {input_name}")
            source = by_id[source_id]
            target_slot = input_slots[input_name]
            source_output = source["outputs"][source_slot]
            link_type = target["inputs"][target_slot].get("type") or source_output.get("type") or "*"
            target["inputs"][target_slot]["link"] = link_id
            source_output.setdefault("links", []).append(link_id)
            links.append([link_id, int(source_id), source_slot, int(target_id), target_slot, link_type])
            link_id += 1

    visual = {
        "last_node_id": 1000,
        "last_link_id": link_id - 1,
        "nodes": nodes,
        "links": links,
        "groups": [
            {"id": 1, "title": "1 · Input + shared models", "bounding": [-40, 280, 840, 980], "color": "#3f789e", "font_size": 22, "flags": {}},
            {"id": 2, "title": "2 · Three independent clean-background views", "bounding": [860, 80, 1320, 1570], "color": "#3f8058", "font_size": 22, "flags": {}},
            {"id": 3, "title": "3 · Deterministic 2:1 composite + final output", "bounding": [2240, 440, 1080, 620], "color": "#8a6d3b", "font_size": 22, "flags": {}},
        ],
        "config": {},
        "extra": {
            "ds": {"scale": 0.55, "offset": [40, 20]},
            "frontendVersion": "1.47.11",
            "info": {"name": "DS Qwen Prop Reference Sheet", "source": "qwen_prop_master.api.json"},
        },
        "version": 0.4,
    }
    VISUAL_PATH.write_text(json.dumps(visual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
