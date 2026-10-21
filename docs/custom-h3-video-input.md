# Custom H3 video input

In Settings → Custom H3 Workflows, import the ComfyUI API JSON and choose the final video output. Director Studio searches upstream for `LoadVideo.file` and `VHS_LoadVideo.video`, including empty file slots. A single candidate is proposed automatically; multiple candidates require a choice. Confirm the input nodes to save the mapping.

For a workflow test, choose a Picture and upload a sample source video using **Video for test**. Production replaces this sample with the source selected on the shot. An imported Motion Context graph without a video mapping or a real source is blocked instead of using the filename left in the workflow.

Supported continuation paths:

- `LoadVideo → GetVideoComponents → MiniMaxH3MotionContext.context_frames`; optional context audio comes from the same components node.
- `VHS_LoadVideo.IMAGE → MiniMaxH3MotionContext.context_frames`; optional context audio comes from the same loader's AUDIO output, index 2.

Both paths must feed the conditioned sampler, video/audio decoders and overlap trim. The selected final output must be `SaveVideo` connected to `CreateVideo`, whose images and audio come from that trim directly or through the supported delivery crops (`ImageFromBatch` / `TrimAudioDuration`). Other output sinks, including `VHS_VideoCombine`, are not certified for continuation. Continuation remains restricted to the supported Ref2AV + Motion Context graph at 24 fps. Other custom video or VideoExtend nodes are not certified by this change.

Disconnect `Motion Context.context_latent` when using the mapped video: the node prefers that latent over video pixels, so Director Studio rejects a graph with both connected. A connected `context_audio` inherits sound even when `audio_context_length=0`; zero means follow the video context span. To disable inherited audio in your custom variation, disconnect `context_audio`. Director Studio reports these uploaded settings rather than overriding them.

VHS must read the full source as decoded images: `frame_load_cap=0`, `skip_first_frames=0`, `select_every_nth=1`, and `force_rate=0` or `24`, without optional VAE or meta-batch connections. Incompatible values are reported, not overwritten. Model, LoRA, sampler, steps and context-window settings remain owned by the uploaded workflow; existing duration budgeting and delivery trimming still apply.

Previously saved profiles keep their mappings. Reimport a profile that lacks the video mapping, confirm its input, then validate and test it before activation.

## Downloadable Turbo 4-step continuation example

- [ComfyUI visual workflow](../backend/workflows/h3_ref2va_turbo4_video_context.visual.json): drag into ComfyUI to view and edit the node graph.
- [Director Studio API workflow](../backend/workflows/h3_ref2va_turbo4_video_context.api.json): import in Settings → Custom H3 Workflows.

Both files have an empty prompt and empty Picture, Audio and source-video file slots. Supply your own prompt and media before a direct ComfyUI run. No demo dialogue, character descriptions, source job identifiers or private media are included.

### Import and input mapping

1. Install the required models and custom nodes listed below.
2. Import the API file, choose **Save Video (92)** as the final output, and confirm **MiniMax H3 Reference To Video (136)** as the Ref2AV input.
3. Confirm **Source video (142), `LoadVideo.file`** as the independent video input. This does not use a Picture or Audio reference slot.
4. Validate the mapping, supply a Picture and **Video for test**, then run the workflow test before activation. Reference audio is optional when submitting through Director Studio; a direct ComfyUI run must populate or disconnect the connected `LoadAudio` node.
5. For a continuation shot, select a completed source take or upload an external clip. Director Studio replaces the empty media slots and prompt with that shot's submitted inputs.

### Nodes and settings

| Node | Role |
| --- | --- |
| 142 → 143 | Load the finished source video and decode its container into image frames. |
| 144 | Re-encode the selected tail frames through the video VAE into the receiving H3 context. No `context_latent` or `context_audio` is connected in this example. |
| 126 → 125 | Apply that conditioning to the receiving sampler. |
| 122 / 121 | Decode the newly generated video / audio latents. |
| 145 | Remove the overlapping context frames and corresponding audio. |
| 146 / 147 → 130 → 92 | Limit delivery length, create the video at 24 fps, and save it. |

The example preserves the demo's Singularity INT8 model, Turbo 4-step LoRA, `euler` sampler, `beta` scheduler with four steps, 1376×768 dimensions and a **5-frame** video context window. The generated length is 277 frames; delivery is limited to 260 frames (about 10.83 seconds). Set compatible lengths when running directly in ComfyUI. Director Studio calculates submission and delivery lengths for the shot while preserving the uploaded model, sampler and context-window choices. A context window of five is an example setting, not a required value for other variations.

### Requirements and credits

This graph is a Director Studio adaptation of the [Comfy-Org MiniMax H3 Ref2AV template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json), with continuation and optimization nodes:

- [NikoDemon80 / ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context): `MiniMaxH3MotionContext` and `MiniMaxH3MotionContextTrim`.
- [Zironic / H3-Optimizations](https://github.com/Zironic/H3-Optimizations): `H3MemoryOptimization`.

Packaging status (checked 2026-10-08):

| Pack | Licence | Director Studio package |
| --- | --- | --- |
| ComfyUI-H3-Motion-Context v0.6.2 (`5335715abe54`) | GPL-3.0 (LICENSE file) | Bundled as the optional **H3 continuation** component (`installer/manifest.json`, leave out with `build_linux.py build --without h3-continuation`). No Python dependencies; runs inside the ComfyUI process only. |
| H3-Optimizations 0.2.45 (`862774944a33`) | none published (no LICENSE file, no licence in pyproject/README, Comfy registry licence empty) | **Not bundled** — without a licence it cannot be redistributed. Install it yourself into the **user node folder** — with the Director Studio package (managed ComfyUI) that is `<DS data>/comfy/custom_nodes` (Linux `~/.local/share/DirectorStudio/data/comfy/custom_nodes`, Windows `%LOCALAPPDATA%\DirectorStudio\data\comfy\custom_nodes`; the health check shows the exact path), which survives upgrades and uninstall: `cd <that folder> && git clone https://github.com/Zironic/H3-Optimizations`, then restart ComfyUI. With your own ComfyUI use its `custom_nodes` folder. The health check links the repository. Alternatively use the built-in `h3_ref2va.api.json` or remove the `H3MemoryOptimization` node from your variation. |

Both `h3_ref2va_fast4.api.json` and the video-context workflow use `H3MemoryOptimization`.

## Default built-in H3 workflow

`h3_ref2va_turbo4_video_context.api.json` is the default built-in H3 workflow (`DS_H3_BUILTIN_WORKFLOW`, Settings → Generation → advanced). It is used for every shot: a shot with a video context (previous shot or external upload, `DS_VIDEO_CONTEXT_ENABLED=true`) feeds the source video into node 142 and Motion Context; a shot without one runs the same Turbo 4-step graph with the context nodes removed. The health check therefore requires its node packs (ComfyUI-H3-Motion-Context, H3-Optimizations). `h3_ref2va_fast4.api.json` (Turbo 4, no context) and `h3_ref2va.api.json` (official 20-step) remain selectable.
- A compatible ComfyUI version providing the H3 Ref2AV, Sigma Shift, video/audio loading and decoding, audio trimming, and `ModelAttentionBackend` nodes. The example selects `comfy kitchen attention`; install its requirements or change the attention backend to one supported by your deployment.

Install these exact model filenames, or select compatible alternatives in the visual graph and export a new API variation:

- Diffusion model: `Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors`.
- Text encoder: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.
- Video VAE: `minimax_h3_video_vae_int8_convrot.safetensors`.
- Audio VAE: `minimax_h3_audio_vae_fp32.safetensors`.
- LoRA: `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors`.

Models and custom-node code are separate downloads governed by their upstream licenses. These example files are imported variations; adding them does not select or replace Director Studio's built-in profile.
