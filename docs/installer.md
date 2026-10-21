# Director Studio 安装包 / Installer package (Linux)

[中文](#中文) · [English](#english)

## 中文

### 选择版本

| 变体 | 适用 | 说明 |
|---|---|---|
| `cu128` | NVIDIA RTX 20xx–50xx，驱动 ≥ 570 | 推荐 |
| `cu130` | NVIDIA，驱动 ≥ 580 | CUDA 13 |
| `cpu` | 无 NVIDIA 显卡 | 仅用于测试界面/流程，ComfyUI 以 `--cpu` 运行，无法实际生成视频 |

### 安装与运行

```bash
tar -xzf DirectorStudio-<版本>-linux-x86_64-<变体>.tar.gz -C ~/Apps
cd ~/Apps/DirectorStudio-<版本>-linux-x86_64-<变体>
sha256sum -c ../DirectorStudio-*.tar.gz.sha256   # 可选：校验下载
./director-studio                # 启动并打开浏览器（默认 http://127.0.0.1:8790）
./director-studio --no-open      # 不打开浏览器
./director-studio status | stop  # 查看状态 / 停止
./install-desktop-entry.sh       # 可选：加入应用菜单（--remove 删除）
```

首次运行会进入配置向导。安装包默认：**托管 ComfyUI**（`DS_COMFY_MODE=managed`，DS 自动启动内置 ComfyUI）、**内置 llama.cpp**（`DS_LLM_PROVIDER=llama-swap`、`DS_LLAMA_SWAP_MODE=bundled`）和 **Harness** 导演运行时。所有设置都可以在 设置 中修改，修改后需要重启的项目会出现“立即重启”按钮（由启动器重启 Harness + 后端 + ComfyUI/llama-swap）。

### 目录结构

程序目录（可整体替换升级）：

```
director-studio            启动脚本（python -m app.launcher）
ds-install.json            安装包标记 + 默认配置
backend/ frontend/dist/    Director Studio
harness/ runtime/node/     Harness 侧车 + Node 22
runtime/python/            后端 Python 3.13（python-build-standalone）
runtime/comfy-tools/ runtime/bin/   comfy-cli / comfy-mcp（独立进程）
runtime/ComfyUI/           ComfyUI（固定版本）+ ComfyUI-KJNodes + ds_nodes [+ 可选“H3 续写”: ComfyUI-H3-Motion-Context]
runtime/comfy-python/      ComfyUI 专用 Python + torch
runtime/llama-swap/        llama-swap（MIT，单一 LLM 服务）
runtime/llama.cpp/         llama-server（MIT；Linux CPU 版，Windows CUDA 版）
runtime/ffmpeg/            FFmpeg（LGPL 静态版）
THIRD_PARTY_NOTICES.txt SOURCE_OFFER.txt licenses/
```

用户数据（升级不受影响）：`$DS_HOME`，默认 `~/.local/share/DirectorStudio`：`.env`（配置）、`data/`（项目、素材库、日志 `data/logs/`、ComfyUI 输入/输出 `data/comfy/`、ComfyUI 模型 `data/models/`、导演大模型 GGUF `data/models/llm/`、自动生成的 `data/llama-swap/config.yaml`）。

**升级**：停止旧版本 → 解压新版本 → 运行新目录的 `./director-studio`。**卸载**：删除程序目录；如不再需要数据，再删除 `~/.local/share/DirectorStudio`。

### 导演大模型（内置 llama.cpp + llama-swap）

- DS 启动一个 llama-swap 进程（随机本机私有端口，不是 11435），它按需启动 llama-server；对用户来说只有一个服务。日志 `data/logs/llama-swap.log`。
- 模型：在 设置 → 依赖下载 → “导演大模型” 中下载推荐的导演模型 Qwen3.8-27B Q4_K_M（官方 Qwen/Qwen3.8-27B 的 GGUF，由 ggml-org 转换，Apache-2.0，约 19 GB，需约 24 GB 显卡），从 Hugging Face 下载并校验 sha256。小模型的工具调用不可靠，因此不推荐；也可以把任意 `.gguf` 放进 `data/models/llm/`。
- `data/llama-swap/config.yaml` 由 DS 自动生成（模型路径、`-c` = `DS_DIRECTOR_NUM_CTX`、GPU 层数 `DS_LLAMA_GPU_LAYERS`、模型带 MTP 层时自动加 `--spec-type draft-mtp`），设置或模型变化后自动重写并热加载，请勿手动编辑。
- 也可以改用 自己的 llama-swap（`DS_LLAMA_SWAP_MODE=remote`，上下文长度从服务端读取）、本机/远程 Ollama、LM Studio 或任意 OpenAI 兼容接口。

### ComfyUI

- **托管**（默认）：使用内置 ComfyUI，模型放在 `data/models/`，可在 设置 → 工作流依赖 一键下载，清单见 [models.md](models.md)。
- **本机已有**：填写地址（如 `http://127.0.0.1:8188`）。
- **远程**：填写远程地址和可选的鉴权（bearer / basic / 自定义请求头）；DS 通过本机代理转发，comfy-mcp 只连接本机代理。

### 工作流依赖下载（模型与节点包）

DS 不附带第三方模型，也不替你接受任何许可。打开 **设置 → 工作流依赖**：每个内置工作流一张卡片，列出所需的节点包和模型（状态、大小、许可）。可逐项下载，或点“一键补全此工作流”；下载前会提示“由用户从原作者处下载，适用原作者的许可”，对无许可证的节点包（如 H3-Optimizations）另有警告。

- 模型从 Hugging Face（固定版本）下载，失败时改用 ModelScope 同名仓库；支持断点续传，完成后校验 SHA-256 再移入 `data/models/<目录>/`。
- 国内网络可填写 Hugging Face 镜像（`DS_DEPS_HF_ENDPOINT`，如 `https://hf-mirror.com`）；需要同意条款的（gated）模型请先在 Hugging Face 页面同意，再填写 `DS_HF_TOKEN`（只发送给 Hugging Face / 镜像，不会回显或写入日志）。
- 节点包按固定提交 `git clone`（没有 git 时下载 GitHub zip）到下方“自己安装的节点”目录，并把它的依赖装进内置 ComfyUI 的 Python；装好后点“重启 ComfyUI 以加载新节点”。
- 体检和首次配置向导中的“缺少模型/节点？去下载”会直接打开这一页。远程或本机已有的 ComfyUI 只显示状态和链接，请在那台机器上自行安装。
- 清单（URL、大小、SHA-256、许可）统一维护在 `backend/app/core/deps/catalog.json`，[models.md](models.md) 由它生成。

### 自己安装的 ComfyUI 节点

托管 ComfyUI 还会加载 `<数据目录>/comfy/custom_nodes` 中的节点包（Linux：`~/.local/share/DirectorStudio/data/comfy/custom_nodes`；Windows：`%LOCALAPPDATA%\DirectorStudio\data\comfy\custom_nodes`）。自己安装的节点（例如 `git clone https://github.com/Zironic/H3-Optimizations`）请放在这里，升级和卸载都会保留。若节点包有额外 Python 依赖，需要装进内置解释器 `runtime/comfy-python`。

### 远程访问

Director Studio 本身**没有登录鉴权**，请保持 `DS_HOST=127.0.0.1`。需要从其他设备访问时，推荐：

- Tailscale：`tailscale serve --bg 8790`（仅你的 tailnet 可访问）；
- SSH 隧道：`ssh -L 8790:127.0.0.1:8790 user@主机`；
- 不要把 8790 直接暴露到公网。配置页（设置）只允许本机访问。

### Windows 安装包

`installer/build_windows.py` 生成 Windows x64 安装包（Inno Setup）：按用户安装到 `%LOCALAPPDATA%\Programs\DirectorStudio`，无需管理员权限；配置和数据在 `%LOCALAPPDATA%\DirectorStudio`，升级、卸载都会保留。开始菜单有“Director Studio”“Stop Director Studio”和带控制台的版本。启动器不显示控制台窗口，并把所有子进程（后端、Harness、ComfyUI、llama-swap/llama-server）放进 Job Object，启动器退出时它们一起结束。安装包超过 2 GB，会分成 `setup.exe` 和若干 `setup-N.bin`，请放在同一目录。

### 许可

Director Studio 计划以 Apache-2.0 发布（仓库尚未包含 LICENSE 文件）。ComfyUI、ComfyUI-KJNodes、ComfyUI-H3-Motion-Context、comfy-cli（GPL-3.0）、comfy-mcp（AGPL-3.0）和 FFmpeg（LGPL）作为**独立程序**随包分发、以独立进程运行，DS 代码不链接/不包含它们的代码。完整清单见 `THIRD_PARTY_NOTICES.txt`，源码获取方式见 `SOURCE_OFFER.txt`。GPU 版本还包含 NVIDIA CUDA/cuDNN 运行库（随 PyTorch 和 llama.cpp 分发，未修改，受 NVIDIA EULA 约束），清单见 `licenses/VENDOR-RUNTIMES.txt`。`H3MemoryOptimization`（Zironic/H3-Optimizations）没有许可证，因此不随包分发，体检会给出仓库链接和手动安装方法（见 docs/custom-h3-video-input.md）。

## English

### Pick a variant

| Variant | For | Notes |
|---|---|---|
| `cu128` | NVIDIA RTX 20xx–50xx, driver ≥ 570 | recommended |
| `cu130` | NVIDIA, driver ≥ 580 | CUDA 13 |
| `cpu` | no NVIDIA GPU | UI/flow testing only; ComfyUI runs with `--cpu` and cannot realistically generate video |

### Install and run

```bash
tar -xzf DirectorStudio-<version>-linux-x86_64-<variant>.tar.gz -C ~/Apps
cd ~/Apps/DirectorStudio-<version>-linux-x86_64-<variant>
./director-studio                # start and open the browser (default http://127.0.0.1:8790)
./director-studio --no-open
./director-studio status | stop
./install-desktop-entry.sh       # optional menu entry (--remove to delete)
```

The first run opens the setup wizard. Package defaults: **managed ComfyUI** (DS starts the bundled ComfyUI), **built-in llama.cpp** (llama-swap, `DS_LLAMA_SWAP_MODE=bundled`) and the **Harness** director runtime. Everything can be changed in Settings; settings that need a restart show a **Restart now** button (the launcher restarts Harness + backend + ComfyUI/llama-swap).

### Layout

The program folder (replace it as a whole to upgrade) contains `director-studio`, `ds-install.json`, `backend/`, `frontend/dist/`, `harness/` + `runtime/node/`, `runtime/python/` (backend Python 3.13, python-build-standalone), `runtime/comfy-tools/` + `runtime/bin/` (comfy-cli / comfy-mcp, separate processes), `runtime/ComfyUI/` (pinned ComfyUI + ComfyUI-KJNodes + ds_nodes, plus the optional *H3 continuation* component ComfyUI-H3-Motion-Context), `runtime/comfy-python/` (ComfyUI Python + torch), `runtime/llama-swap/` + `runtime/llama.cpp/` (llama-swap and llama-server, MIT), `runtime/ffmpeg/` (LGPL static FFmpeg), and the licence files.

User data lives in `$DS_HOME` (default `~/.local/share/DirectorStudio`): `.env` and `data/` (projects, library, logs in `data/logs/`, ComfyUI input/output in `data/comfy/`, ComfyUI models in `data/models/`, director GGUF models in `data/models/llm/`, generated `data/llama-swap/config.yaml`). It survives upgrades. **Upgrade**: stop, extract the new version, run its `./director-studio`. **Uninstall**: delete the program folder (and the data folder if you no longer need it).

### Director LLM (built-in llama.cpp + llama-swap)

- DS starts one llama-swap process on a random private loopback port (never 11435); llama-swap spawns llama-server on demand, so it is a single service. Log: `data/logs/llama-swap.log`.
- Models: Settings → Dependencies → "Director LLM" downloads the recommended director model, Qwen3.8-27B Q4_K_M (GGUF of the official Qwen/Qwen3.8-27B converted by ggml-org, Apache-2.0, ~19 GB, needs a ~24 GB GPU) with a sha256 check. Small models are not recommended: their tool calling is unreliable; any `.gguf` dropped into `data/models/llm/` is picked up too.
- `data/llama-swap/config.yaml` is generated by DS (model path, `-c` = `DS_DIRECTOR_NUM_CTX`, `-ngl` = `DS_LLAMA_GPU_LAYERS`, `--spec-type draft-mtp` when the GGUF carries MTP layers) and rewritten + hot-reloaded on every settings or model change. Do not edit it.
- Alternatively use your own llama-swap (`DS_LLAMA_SWAP_MODE=remote`; DS reads the context size from the server), your own/remote Ollama, LM Studio or any OpenAI-compatible endpoint.

### ComfyUI

Managed (default, models in `data/models/`, downloadable from Settings → Workflow dependencies, see [models.md](models.md)), an existing local ComfyUI, or a remote ComfyUI (optional bearer / basic / header auth; DS proxies it locally so comfy-mcp only talks to loopback).

If the Director LLM (llama-swap, Ollama, LM Studio) runs on the **same GPU** as a remote ComfyUI (for example both on one Windows PC over Tailscale), tick "The LLM and ComfyUI run on the same GPU" (`DS_VRAM_SHARED_GPU=true`). DS then unloads the LLM before each generation and frees ComfyUI before LLM turns; without it a 27B LLM plus a 20 GB image/video model can exhaust a 24 GB card. `auto` treats a local ComfyUI as shared and decides remote setups by host (two `127.0.0.1` forwards/tunnels count as shared).

### Workflow dependencies (models and node packs)

DS ships no third-party models and accepts no licence on your behalf. Open **Settings → Workflow dependencies**: one card per built-in workflow lists the node packs and models it needs (status, size, licence). Download item by item or click "Complete this workflow"; a confirmation reminds you that you download each file from its original publisher under that publisher's licence, with an extra warning for unlicensed packs (e.g. H3-Optimizations).

- Models come from Hugging Face at a pinned revision, falling back to the same-name ModelScope repository; downloads resume after interruption and are SHA-256 verified before being moved into `data/models/<folder>/`.
- Set a Hugging Face mirror (`DS_DEPS_HF_ENDPOINT`, e.g. `https://hf-mirror.com`) if needed. For gated models accept the terms on Hugging Face first, then set `DS_HF_TOKEN` (sent only to Hugging Face / the mirror, never shown or logged).
- Node packs are fetched at a pinned commit with `git` (GitHub zip when git is missing) into the user node folder below, and their requirements are installed into the bundled ComfyUI Python; then click "Restart ComfyUI to load new nodes".
- "Missing models/nodes? Download them" in the doctor and the first-run wizard opens this page. With a remote or your own ComfyUI the page is status-only with links; install on that machine.
- URLs, sizes, SHA-256 and licences live in `backend/app/core/deps/catalog.json`; [models.md](models.md) is generated from it.

### Your own ComfyUI node packs

The managed ComfyUI also loads node packs from `<data>/comfy/custom_nodes` (Linux `~/.local/share/DirectorStudio/data/comfy/custom_nodes`, Windows `%LOCALAPPDATA%\DirectorStudio\data\comfy\custom_nodes`), registered through the generated `extra_model_paths.yaml`. Put packs you install yourself there (e.g. `git clone https://github.com/Zironic/H3-Optimizations`): unlike `runtime/ComfyUI/custom_nodes` it is kept on upgrade and uninstall. Packs with extra Python requirements need them installed into the bundled interpreter (`runtime/comfy-python`).

### Remote access

Director Studio has **no login**; keep `DS_HOST=127.0.0.1`. To use it from another device prefer Tailscale (`tailscale serve --bg 8790`) or an SSH tunnel (`ssh -L 8790:127.0.0.1:8790 user@host`); never expose the port publicly. The Settings/config API only accepts loopback requests.

### Building the package (maintainers)

```bash
python -m venv build-venv && build-venv/bin/pip install -r installer/requirements-build.txt
build-venv/bin/python installer/build_linux.py lock --variant cpu --variant cu128 --variant cu130   # refresh uv hash locks
build-venv/bin/python installer/build_linux.py build --variant cu128 --out dist-installer --archive \
    --node-bin /path/to/node22/bin [--cache DIR] [--reuse-staging]
```

All downloads are pinned in `installer/manifest.json` and sha256-verified; Python packages install with `uv pip install --require-hashes`. Output: `<name>.tar.gz`, `.tar.gz.sha256`, `<name>.build.json` (sizes, components, licence summary).

### Windows (x64) installer

`installer/build_windows.py` builds the same pinned component set for `windows-x64` and an Inno Setup installer (`installer/windows/DirectorStudio.iss`):

- **Install**: per user, no admin, to `%LOCALAPPDATA%\Programs\DirectorStudio`; config and data in `%LOCALAPPDATA%\DirectorStudio` (kept on upgrade and uninstall). Start-menu entries *Director Studio*, *Stop Director Studio*, *Director Studio (console)*, optional desktop icon. The payload is > 2 GB, so the installer is `…-setup.exe` plus `…-setup-N.bin` slices that must stay in one folder.
- **Launcher**: `runtime\python\pythonw.exe director-studio.pyw` (no console window); the launcher puts itself into a kill-on-close **Job Object**, so the backend, Harness, ComfyUI and llama-swap/llama-server all end with it (even after "End task"). Children are started with `CREATE_NO_WINDOW`; the browser is started outside the job. `stop` uses a stop-request file because Windows has no SIGTERM. Logs: `%LOCALAPPDATA%\DirectorStudio\data\logs`.
- **ComfyUI**: same pinned source checkout + python-build-standalone + `uv --require-hashes` locks (`installer/locks/windows-*.txt`) as Linux, rather than the official ComfyUI_windows_portable 7z: one reproducible dependency set shared with Linux (same torch pins, custom-node deps in the lock), stageable on a Linux build host, variant choice (cu128/cu130/cpu), no embeddable-Python quirks.

```bash
build-venv/bin/python installer/build_windows.py lock --variant cu128 --variant cpu     # Windows hash locks
build-venv/bin/python installer/build_windows.py stage --variant cu128 --node-bin /path/to/node22/bin
build-venv/bin/python installer/build_windows.py check --root dist-installer-win/staging/<name>
python installer/build_windows.py build --variant cu128     # on Windows: stage + check + ISCC
```

`stage` works on Linux too (wheels are installed for `x86_64-pc-windows-msvc`, npm production deps with `--os=win32 --cpu=x64`); `check` validates the tree statically (required files, PE-only binaries, MAX_PATH budget, CRLF wrappers). CI: `.github/workflows/installer-windows.yml` (manual / tags).

### macOS (Apple Silicon / Intel)

`python installer/build_macos.py build --arch arm64|x86_64 --archive` on a Mac of that architecture
(CI: `.github/workflows/installer-macos.yml`, runners `macos-15` / `macos-15-intel`) produces
`DirectorStudio-<version>-macos-<arch>-<variant>.tar.gz`. It reuses the Linux staging steps and layout:
launcher config UI, Harness + Node 22, official llama.cpp macOS release build (Metal on arm64) + llama-swap,
workflow dependency downloader. Locks: `installer/locks/macos-*` (`build_macos.py lock`, uv cross-compiles
for `aarch64-apple-darwin` / `x86_64-apple-darwin`).

* arm64 (`mps`): bundled ComfyUI + ds_nodes, KJNodes, H3-Motion-Context, PyTorch from PyPI (MPS).
* x86_64 (`nocomfy`): PyTorch has no Intel-macOS wheels for Python 3.13 / current torch, so no local
  ComfyUI is bundled; the package defaults to `DS_COMFY_MODE=external-local` (connect your own or a remote ComfyUI).
* FFmpeg is not bundled (no upstream LGPL macOS build): `brew install ffmpeg` or `DS_FFMPEG_DIR`.
* Start with `Director Studio.command` (double-click) or `./director-studio`. Builds are unsigned and not
  notarized; the `.command` clears the download quarantine flag on first run.

### Licensing

Director Studio is planned to be released under Apache-2.0 (the repository has no LICENSE file yet, so the package does not claim one). ComfyUI, ComfyUI-KJNodes, ComfyUI-H3-Motion-Context, comfy-cli (GPL-3.0), comfy-mcp (AGPL-3.0) and FFmpeg (LGPL) are shipped as **separate programs** and only run as separate processes; no DS code links to or includes them. GPU builds also contain NVIDIA CUDA/cuDNN runtime libraries (inside PyTorch and llama.cpp), redistributed unmodified under NVIDIA's EULAs; they are listed in `licenses/VENDOR-RUNTIMES.txt`. See `THIRD_PARTY_NOTICES.txt` and `SOURCE_OFFER.txt`. `H3MemoryOptimization` (Zironic/H3-Optimizations) has no licence and is therefore not bundled; the health check links the repo with a manual install hint (see docs/custom-h3-video-input.md).
