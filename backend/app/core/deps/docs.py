"""Generate docs/models.md from catalog.json:  python -m app.core.deps.docs [--check]."""

from __future__ import annotations

import sys
from pathlib import Path

from . import catalog

DOC_PATH = Path(__file__).resolve().parents[4] / "docs" / "models.md"

INTRO_ZH = """Director Studio 自带的 ComfyUI 工作流需要下面的模型文件和节点包。DS **不附带**第三方模型（共约 {total}，单个最大约 {largest}），也不替你接受任何许可：请在 **设置 → 工作流依赖** 里按工作流点击下载——文件由你从原作者处下载，适用原作者的许可。也可以按下表手动下载。

> 本文件由 `backend/app/core/deps/catalog.json` 生成（`cd backend && python -m app.core.deps.docs`），请勿手改。大小与 SHA-256 来自 Hugging Face API（核验日期见 catalog）。

**放在哪里**

- 安装包 / 托管 ComfyUI（`DS_COMFY_MODE=managed`）：`<数据目录>/data/models/<目录>/`，数据目录默认为 `~/.local/share/DirectorStudio`（可用 `DS_HOME` 修改），也可在 设置 → 生成 → 模型目录（`DS_COMFY_MODELS_DIR`）改为别处。自定义节点包放在 `<数据目录>/data/comfy/custom_nodes/`（卸载/升级不会删除）。
- 自己的 ComfyUI（本机或远程）：`ComfyUI/models/<目录>/`（或其 `extra_model_paths.yaml` 中配置的目录）。远程模式下 DS 只显示状态和链接，不会替远程机器安装。
- 放好后在 设置 → 总览 点 “体检”，缺失的文件会逐个列出（含应放入的完整路径）。

**下载来源**：优先官方仓库（Comfy-Org、lightx2v、fal）；标 ⚠ 的是社区模型。国内网络可在 设置 → 工作流依赖 填写 Hugging Face 镜像（如 `https://hf-mirror.com`），或使用 ModelScope 同名仓库。命令行示例：

```bash
# Hugging Face（可设 HF_ENDPOINT=https://hf-mirror.com）
huggingface-cli download Comfy-Org/Qwen-Image_ComfyUI split_files/vae/qwen_image_vae.safetensors --local-dir /tmp/dl
mv /tmp/dl/split_files/vae/qwen_image_vae.safetensors ~/.local/share/DirectorStudio/data/models/vae/
# ModelScope
modelscope download --model Comfy-Org/Qwen-Image_ComfyUI split_files/vae/qwen_image_vae.safetensors --local_dir /tmp/dl
```

**显存**：H3 视频工作流面向 24 GB 以上 NVIDIA 显卡；Qwen Image 系列 fp8 模型约需 16–24 GB。CPU 版安装包只适合测试界面和流程，无法实际生成。"""

INTRO_EN = """The ComfyUI workflows built into Director Studio need the model files and node packs below. DS does **not** ship third-party models (≈{total} in total, largest file ≈{largest}) and accepts no licence on your behalf: use **Settings → Workflow dependencies** to download per workflow - you download each file from its original publisher, under that publisher's licence. Manual links are listed below.

> Generated from `backend/app/core/deps/catalog.json` (`cd backend && python -m app.core.deps.docs`); do not edit by hand. Sizes and SHA-256 come from the Hugging Face API (verification date in the catalog).

**Where files go**

- Installed package / managed ComfyUI (`DS_COMFY_MODE=managed`): `<data dir>/data/models/<folder>/`; the data dir defaults to `~/.local/share/DirectorStudio` (override with `DS_HOME`), or point Settings → Generation → Models folder (`DS_COMFY_MODELS_DIR`) elsewhere. Custom node packs go to `<data dir>/data/comfy/custom_nodes/` (kept across uninstall/upgrade).
- Your own ComfyUI (local or remote): `ComfyUI/models/<folder>/` (or a folder from its `extra_model_paths.yaml`). In remote mode DS only shows status and links; it never installs on the remote machine.
- Then run Settings → Overview → Doctor; every missing file is listed with its expected path.

**Sources**: official repositories first (Comfy-Org, lightx2v, fal). Files marked ⚠ are community models. In mainland China set a Hugging Face mirror (e.g. `https://hf-mirror.com`) in Settings → Workflow dependencies, or use the same-name ModelScope repositories. CLI examples are in the Chinese section above.

**VRAM**: the H3 video workflows target NVIDIA GPUs with 24 GB+; Qwen Image fp8 models need roughly 16–24 GB. The CPU package is for UI/flow testing only and cannot generate in practice."""

HEAD = {"zh": ("文件", "放入目录", "大小", "许可", "Hugging Face", "ModelScope（国内镜像）"),
        "en": ("File", "Folder", "Size", "Licence", "Hugging Face", "ModelScope (CN mirror)")}
PACK_HEAD = {"zh": ("节点包", "来源（固定版本）", "许可", "提供的节点", "说明"),
             "en": ("Node pack", "Source (pinned)", "Licence", "Provides", "Notes")}


def _licence(entry: dict, lang: str) -> str:
    lic = entry.get("license")
    if entry.get("unlicensed") or not lic:
        return "⚠ 无许可证（保留所有权利）" if lang == "zh" else "⚠ no licence (all rights reserved)"
    url = entry.get("license_url")
    return f"[{lic}]({url})" if url else lic


def _model_row(m: dict, lang: str) -> str:
    links = []
    for kind in ("hf", "modelscope"):
        src = next((s for s in m["sources"] if s["kind"] == kind), None)
        if not src:
            links.append("-")
            continue
        url = (f"https://huggingface.co/{src['repo']}/resolve/main/{src['path']}" if kind == "hf"
               else f"https://modelscope.cn/models/{src['repo']}/resolve/master/{src['path']}")
        links.append(f"[{src['repo']}]({url})")
    warn = " ⚠" if m.get("community") else ""
    gated = (" 🔒" if m.get("gated") else "")
    return (f"| `{m['file']}`{warn}{gated} | `{m['folder']}/` | {catalog.human_size(m.get('size'))} | "
            f"{_licence(m, lang)} | {links[0]} | {links[1]} |")


def _pack_rows(lang: str) -> list[str]:
    head = PACK_HEAD[lang]
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for p in catalog.node_packs():
        src = (f"[{p['repo'].removeprefix('https://github.com/')}]({p['repo']}) @ `{(p.get('ref') or '')[:12]}`"
               if p.get("repo") and p.get("ref") else (f"[ComfyUI]({p['repo']})" if p.get("repo") else "DS"))
        note = p.get(f"note_{lang}") or ""
        if not note and p.get("installable"):
            note = "设置 → 工作流依赖 一键安装" if lang == "zh" else "one click in Settings → Workflow dependencies"
        provides = ", ".join(f"`{c}`" for c in p["provides"])
        rows.append(f"| {p['name']} | {src} | {_licence(p, lang)} | {provides} | {note} |")
    return rows


def render() -> str:
    all_models = catalog.models()
    total = sum(m.get("size") or 0 for m in all_models)
    largest = max((m.get("size") or 0) for m in all_models)
    out = ["# 模型与节点清单 / Models and node packs", "", "[中文](#中文) · [English](#english)", ""]
    for lang, title, intro in (("zh", "中文", INTRO_ZH), ("en", "English", INTRO_EN)):
        out += [f"## {title}", "", intro.format(total=catalog.human_size(total), largest=catalog.human_size(largest)), ""]
        out += ["### " + ("自定义节点包" if lang == "zh" else "Custom node packs"), ""] + _pack_rows(lang) + [""]
        for wf in catalog.workflows():
            out += [f"### {wf[f'doc_{lang}']}", "", f"`backend/workflows/{wf['file']}`", ""]
            packs = [p for p in wf["node_packs"] if p not in ("comfy-core",)]
            if packs:
                out += [("节点包：" if lang == "zh" else "Node packs: ") + ", ".join(packs), ""]
            head = HEAD[lang]
            out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            out += [_model_row(catalog.model(f), lang) for f in wf["models"]]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in argv:
        if DOC_PATH.read_text(encoding="utf-8") != text:
            print("docs/models.md is out of date: run python -m app.core.deps.docs", file=sys.stderr)
            return 1
        return 0
    DOC_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {DOC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
