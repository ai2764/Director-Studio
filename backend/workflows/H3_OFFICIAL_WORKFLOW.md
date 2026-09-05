# Official MiniMax H3 Ref2AV workflow

`h3_ref2va.api.json` is the API-format equivalent of the default full-quality
execution branch in Comfy-Org's official `video_minimax_h3_r2v.json` template.
The public application and portable package contain no alternate H3 workflow.

Upstream template:
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json

The executable graph intentionally omits UI-only notes, example assets,
duration controls, switches, and the disabled optional Lightning LoRA branch.
It retains the official model, encoders, VAEs, 20-step `simple` scheduler,
`res_multistep` sampler, AV decode, mux, and save path. Director Studio injects
only the prompt, dimensions, frame count, seed, references, and output prefix;
all sampling, model, decode, mux, and encoding settings remain owned by the
workflow.
