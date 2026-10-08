# Built-in MiniMax H3 Ref2AV workflow

The packaged `h3_ref2va.api.json` currently uses Turbo 8. Settings displays it as
**Built-in H3 Turbo 8 (temporary test)**. The internal profile ID remains
`builtin-official-h3` for compatibility with saved projects; it does not imply
that the graph is the unmodified upstream template.

The graph derives from the
[Comfy-Org MiniMax H3 Ref2AV template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json).
It retains the base model, encoders, VAEs and video/audio output path, and adds
Turbo LoRA, sigma shift, memory optimization and sparse attention. Sampling uses
eight steps, the `simple` scheduler and the `euler` sampler. Install the required
models and custom nodes in ComfyUI.

Director Studio fills the prompt, dimensions, frame count, seed, reference media
and output prefix. Model, sampling, decoding and encoding settings remain in the
workflow. Import an alternative API graph in Settings to use your own variation.
See [custom H3 video input](../../docs/custom-h3-video-input.md) for continuation
requirements and downloadable API/visual examples.
