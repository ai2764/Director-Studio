# Temporary MiniMax H3 Turbo 8 Ref2AV workflow

`h3_ref2va.api.json` is temporarily overlaid with Turbo 8 for local testing.
This commit can be reverted to restore the default full-quality branch of
Comfy-Org's official `video_minimax_h3_r2v.json` template. The profile ID
remains `builtin-official-h3` for compatibility with existing project data.

Upstream template:
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json

The executable graph intentionally omits UI-only notes, example assets,
duration controls, and switches. It retains the official base model, encoders,
VAEs, AV decode, mux, and save path, but adds the Turbo LoRA, sigma shift,
memory optimization, sparse attention, 8-step `simple` scheduler, and `euler`
sampler. These nodes and the Turbo LoRA must be installed in ComfyUI. Director Studio injects
only the prompt, dimensions, frame count, seed, references, and output prefix;
all sampling, model, decode, mux, and encoding settings remain owned by the
workflow.
