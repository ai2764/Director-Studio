# 模型与节点清单 / Models and node packs

[中文](#中文) · [English](#english)

## 中文

Director Studio 自带的 ComfyUI 工作流需要下面的模型文件和节点包。DS **不附带**第三方模型（共约 148.9 GB，单个最大约 21.0 GB），也不替你接受任何许可：请在 **设置 → 工作流依赖** 里按工作流点击下载——文件由你从原作者处下载，适用原作者的许可。也可以按下表手动下载。

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

**显存**：H3 视频工作流面向 24 GB 以上 NVIDIA 显卡；Qwen Image 系列 fp8 模型约需 16–24 GB。CPU 版安装包只适合测试界面和流程，无法实际生成。

### 自定义节点包

| 节点包 | 来源（固定版本） | 许可 | 提供的节点 | 说明 |
|---|---|---|---|---|
| Director Studio nodes (ds_nodes) | DS | Apache-2.0 | `DS Prompt List`, `CR Prompt List` | DS 自带（Apache-2.0），随程序安装，无需下载 |
| ComfyUI-KJNodes | [kijai/ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) @ `d3cfe21625e5` | GPL-3.0 | `SimpleCalculatorKJ`, `ImageConcatMulti`, `StringConstantMultiline` | 安装包已预装（GPL-3.0，固定提交） |
| ComfyUI-H3-Motion-Context | [NikoDemon80/ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context) @ `v0.6.2` | GPL-3.0 | `MiniMaxH3MotionContext`, `MiniMaxH3MotionContextTrim`, `MiniMaxH3MotionContextSaveLatent`, `MiniMaxH3MotionContextLoadLatent`, `MiniMaxH3MotionContextChain`, `MiniMaxH3MotionContextSeamProbe` | 安装包已预装（可选组件“H3 续写”）；未勾选该组件时可在此下载 |
| H3 Optimizations | [Zironic/H3-Optimizations](https://github.com/Zironic/H3-Optimizations) @ `862774944a33` | ⚠ 无许可证（保留所有权利） | `H3MemoryOptimization` | 无许可证：DS 不内置，只能由你在此从作者处下载 |
| ComfyUI core | [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 | `MiniMaxH3ReferenceToVideo`, `MiniMaxH3SigmaShift`, `ModelAttentionBackend`, `QwenImage21Cache`, `TextEncodeQwenImage21` | ComfyUI 内置节点：请升级 ComfyUI 到支持 MiniMax H3 / Qwen-Image 2.1 的版本 |

### 场景多角度参考（Qwen Image Edit 2511）

`backend/workflows/QwenEdit2511_MultiAngle_SceneRef.api.json`

节点包：ComfyUI-KJNodes, ds_nodes

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `qwen-image-edit-2511-multiple-angles-lora.safetensors` | `loras/` | 295 MB | [apache-2.0](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/main/qwen-image-edit-2511-multiple-angles-lora.safetensors) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://modelscope.cn/models/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/master/qwen-image-edit-2511-multiple-angles-lora.safetensors) |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/master/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) |

### H3 视频（官方标准，8 步）

`backend/workflows/h3_ref2va.api.json`

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` | 2.8 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` | 605 MB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `diffusion_models/` | 21.0 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` | 15.7 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |

### H3 视频 fast-4（社区剪枝模型 + Turbo 4 步）

`backend/workflows/h3_ref2va_fast4.api.json`

节点包：H3-Optimizations

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` | 2.8 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` | 605 MB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors` ⚠ | `diffusion_models/` | 21.0 GB | [apache-2.0](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity) | [WarmBloodAban/Minimax-h3_Singularity](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) | [WarmBloodAban/Minimax-h3_Singularity](https://modelscope.cn/models/WarmBloodAban/Minimax-h3_Singularity/resolve/master/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` | 15.7 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | `loras/` | 2.0 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) |

### H3 视频续接 video-context（Turbo 4 步）

`backend/workflows/h3_ref2va_turbo4_video_context.api.json`

节点包：ComfyUI-H3-Motion-Context, H3-Optimizations

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` | 2.8 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` | 605 MB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors` ⚠ | `diffusion_models/` | 21.0 GB | [apache-2.0](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity) | [WarmBloodAban/Minimax-h3_Singularity](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) | [WarmBloodAban/Minimax-h3_Singularity](https://modelscope.cn/models/WarmBloodAban/Minimax-h3_Singularity/resolve/master/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` | 15.7 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | `loras/` | 2.0 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) |

### 演员资产工作台（Qwen Image 2512 + Edit 2511）

`backend/workflows/qwen_actor_asset_workbench.api.json`

节点包：ComfyUI-KJNodes

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `qwen_image_2512_fp8_e4m3fn.safetensors` | `diffusion_models/` | 20.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors) |
| `Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors` | `loras/` | 1.7 GB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning) | [lightx2v/Qwen-Image-2512-Lightning](https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors) | [lightx2v/Qwen-Image-2512-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-2512-Lightning/resolve/master/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/master/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |

### Layout 构图（Qwen Image 2.1）

`backend/workflows/qwen_image_21_layout.api.json`

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `qwen_image_2.1_bf16.safetensors` | `diffusion_models/` | 14.2 GB | [qwen-research](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_bf16.safetensors) | [Comfy-Org/Qwen-Image-2.1](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/diffusion_models/qwen_image_2.1_bf16.safetensors) |
| `qwen3vl_8b_bf16.safetensors` | `text_encoders/` | 17.5 GB | [qwen-research](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_bf16.safetensors) | [Comfy-Org/Qwen-Image-2.1](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/text_encoders/qwen3vl_8b_bf16.safetensors) |
| `qwen_image_2.1_vae_bf16.safetensors` | `vae/` | 676 MB | [qwen-research](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors) | [Comfy-Org/Qwen-Image-2.1](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/vae/qwen_image_2.1_vae_bf16.safetensors) |

### 道具（Qwen Image Edit 2511 多角度）

`backend/workflows/qwen_prop_master.api.json`

节点包：ComfyUI-KJNodes

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |
| `qwen-image-edit-2511-multiple-angles-lora.safetensors` | `loras/` | 295 MB | [apache-2.0](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/main/qwen-image-edit-2511-multiple-angles-lora.safetensors) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://modelscope.cn/models/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/master/qwen-image-edit-2511-multiple-angles-lora.safetensors) |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/master/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) |

### 参考帧构图（Qwen Image Edit 2511）

`backend/workflows/ref_frame_layout.api.json`

| 文件 | 放入目录 | 大小 | 许可 | Hugging Face | ModelScope（国内镜像） |
|---|---|---|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |

## English

The ComfyUI workflows built into Director Studio need the model files and node packs below. DS does **not** ship third-party models (≈148.9 GB in total, largest file ≈21.0 GB) and accepts no licence on your behalf: use **Settings → Workflow dependencies** to download per workflow - you download each file from its original publisher, under that publisher's licence. Manual links are listed below.

> Generated from `backend/app/core/deps/catalog.json` (`cd backend && python -m app.core.deps.docs`); do not edit by hand. Sizes and SHA-256 come from the Hugging Face API (verification date in the catalog).

**Where files go**

- Installed package / managed ComfyUI (`DS_COMFY_MODE=managed`): `<data dir>/data/models/<folder>/`; the data dir defaults to `~/.local/share/DirectorStudio` (override with `DS_HOME`), or point Settings → Generation → Models folder (`DS_COMFY_MODELS_DIR`) elsewhere. Custom node packs go to `<data dir>/data/comfy/custom_nodes/` (kept across uninstall/upgrade).
- Your own ComfyUI (local or remote): `ComfyUI/models/<folder>/` (or a folder from its `extra_model_paths.yaml`). In remote mode DS only shows status and links; it never installs on the remote machine.
- Then run Settings → Overview → Doctor; every missing file is listed with its expected path.

**Sources**: official repositories first (Comfy-Org, lightx2v, fal). Files marked ⚠ are community models. In mainland China set a Hugging Face mirror (e.g. `https://hf-mirror.com`) in Settings → Workflow dependencies, or use the same-name ModelScope repositories. CLI examples are in the Chinese section above.

**VRAM**: the H3 video workflows target NVIDIA GPUs with 24 GB+; Qwen Image fp8 models need roughly 16–24 GB. The CPU package is for UI/flow testing only and cannot generate in practice.

### Custom node packs

| Node pack | Source (pinned) | Licence | Provides | Notes |
|---|---|---|---|---|
| Director Studio nodes (ds_nodes) | DS | Apache-2.0 | `DS Prompt List`, `CR Prompt List` | Part of DS (Apache-2.0); installed with the app, nothing to download |
| ComfyUI-KJNodes | [kijai/ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) @ `d3cfe21625e5` | GPL-3.0 | `SimpleCalculatorKJ`, `ImageConcatMulti`, `StringConstantMultiline` | Preinstalled by the installer (GPL-3.0, pinned commit) |
| ComfyUI-H3-Motion-Context | [NikoDemon80/ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context) @ `v0.6.2` | GPL-3.0 | `MiniMaxH3MotionContext`, `MiniMaxH3MotionContextTrim`, `MiniMaxH3MotionContextSaveLatent`, `MiniMaxH3MotionContextLoadLatent`, `MiniMaxH3MotionContextChain`, `MiniMaxH3MotionContextSeamProbe` | Preinstalled by the installer (optional 'H3 continuation' component); download here if it was not selected |
| H3 Optimizations | [Zironic/H3-Optimizations](https://github.com/Zironic/H3-Optimizations) @ `862774944a33` | ⚠ no licence (all rights reserved) | `H3MemoryOptimization` | No licence: not bundled; download it yourself from the author here |
| ComfyUI core | [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 | `MiniMaxH3ReferenceToVideo`, `MiniMaxH3SigmaShift`, `ModelAttentionBackend`, `QwenImage21Cache`, `TextEncodeQwenImage21` | Built into ComfyUI: update ComfyUI to a version with MiniMax H3 / Qwen-Image 2.1 nodes |

### Scene multi-angle reference (Qwen Image Edit 2511)

`backend/workflows/QwenEdit2511_MultiAngle_SceneRef.api.json`

Node packs: ComfyUI-KJNodes, ds_nodes

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `qwen-image-edit-2511-multiple-angles-lora.safetensors` | `loras/` | 295 MB | [apache-2.0](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/main/qwen-image-edit-2511-multiple-angles-lora.safetensors) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://modelscope.cn/models/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/master/qwen-image-edit-2511-multiple-angles-lora.safetensors) |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/master/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) |

### H3 video (official standard, 8 steps)

`backend/workflows/h3_ref2va.api.json`

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` | 2.8 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` | 605 MB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `diffusion_models/` | 21.0 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` | 15.7 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |

### H3 video fast-4 (community pruned model + Turbo 4-step)

`backend/workflows/h3_ref2va_fast4.api.json`

Node packs: H3-Optimizations

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` | 2.8 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` | 605 MB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors` ⚠ | `diffusion_models/` | 21.0 GB | [apache-2.0](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity) | [WarmBloodAban/Minimax-h3_Singularity](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) | [WarmBloodAban/Minimax-h3_Singularity](https://modelscope.cn/models/WarmBloodAban/Minimax-h3_Singularity/resolve/master/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` | 15.7 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | `loras/` | 2.0 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) |

### H3 video continuation / video-context (Turbo 4-step)

`backend/workflows/h3_ref2va_turbo4_video_context.api.json`

Node packs: ComfyUI-H3-Motion-Context, H3-Optimizations

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` | 2.8 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` | 605 MB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors` ⚠ | `diffusion_models/` | 21.0 GB | [apache-2.0](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity) | [WarmBloodAban/Minimax-h3_Singularity](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) | [WarmBloodAban/Minimax-h3_Singularity](https://modelscope.cn/models/WarmBloodAban/Minimax-h3_Singularity/resolve/master/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` | 15.7 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | `loras/` | 2.0 GB | [minimax-h3-community-license-agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) | [Comfy-Org/MiniMax-H3](https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) |

### Actor asset workbench (Qwen Image 2512 + Edit 2511)

`backend/workflows/qwen_actor_asset_workbench.api.json`

Node packs: ComfyUI-KJNodes

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `qwen_image_2512_fp8_e4m3fn.safetensors` | `diffusion_models/` | 20.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors) |
| `Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors` | `loras/` | 1.7 GB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning) | [lightx2v/Qwen-Image-2512-Lightning](https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors) | [lightx2v/Qwen-Image-2512-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-2512-Lightning/resolve/master/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/master/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |

### Layout (Qwen Image 2.1)

`backend/workflows/qwen_image_21_layout.api.json`

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `qwen_image_2.1_bf16.safetensors` | `diffusion_models/` | 14.2 GB | [qwen-research](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_bf16.safetensors) | [Comfy-Org/Qwen-Image-2.1](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/diffusion_models/qwen_image_2.1_bf16.safetensors) |
| `qwen3vl_8b_bf16.safetensors` | `text_encoders/` | 17.5 GB | [qwen-research](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_bf16.safetensors) | [Comfy-Org/Qwen-Image-2.1](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/text_encoders/qwen3vl_8b_bf16.safetensors) |
| `qwen_image_2.1_vae_bf16.safetensors` | `vae/` | 676 MB | [qwen-research](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors) | [Comfy-Org/Qwen-Image-2.1](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/vae/qwen_image_2.1_vae_bf16.safetensors) |

### Prop master (Qwen Image Edit 2511 multi-angle)

`backend/workflows/qwen_prop_master.api.json`

Node packs: ComfyUI-KJNodes

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |
| `qwen-image-edit-2511-multiple-angles-lora.safetensors` | `loras/` | 295 MB | [apache-2.0](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/main/qwen-image-edit-2511-multiple-angles-lora.safetensors) | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://modelscope.cn/models/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/master/qwen-image-edit-2511-multiple-angles-lora.safetensors) |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | [apache-2.0](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://modelscope.cn/models/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/master/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors) |

### Reference-frame layout (Qwen Image Edit 2511)

`backend/workflows/ref_frame_layout.api.json`

| File | Folder | Size | Licence | Hugging Face | ModelScope (CN mirror) |
|---|---|---|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.4 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_edit_2511_fp8mixed.safetensors` | `diffusion_models/` | 20.5 GB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/master/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors) |
| `qwen_image_vae.safetensors` | `vae/` | 254 MB | [apache-2.0](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) | [Comfy-Org/Qwen-Image_ComfyUI](https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/split_files/vae/qwen_image_vae.safetensors) |
