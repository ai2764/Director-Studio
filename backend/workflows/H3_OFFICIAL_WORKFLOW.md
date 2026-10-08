# Official MiniMax H3 Ref2AV workflow

`h3_ref2va.api.json` is the API-format execution graph for the full-quality
branch of the [Comfy-Org H3 Ref2AV template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json).
Settings displays it as **Built-in Official H3**; its profile ID is
`builtin-official-h3`.

The graph uses the template's base model, text encoder, video/audio VAEs,
20-step `simple` scheduler, `res_multistep` sampler, decoding and video output.
It does not enable the template's optional Turbo LoRA branch or add third-party
memory/sparse-attention nodes. UI-only notes, switches, sample media and duration
controls are omitted from the execution graph.

Director Studio fills the prompt, dimensions, frame count, seed, references and
output prefix. Internal model and sampling parameters stay in the workflow.
Import a custom variation in Settings for Turbo sampling or video continuation;
see [custom H3 video input](../../docs/custom-h3-video-input.md) for API/visual
examples and dependencies.
