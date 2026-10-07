# Custom H3 video input

In Settings → Custom H3 Workflows, import the ComfyUI API JSON and choose the final video output. Director Studio searches upstream for `LoadVideo.file` and `VHS_LoadVideo.video`, including empty file slots. A single candidate is proposed automatically; multiple candidates require a choice. Confirm the input nodes to save the mapping.

For a workflow test, choose a Picture and upload a sample source video using **Video for test**. Production replaces this sample with the source selected on the shot. An imported Motion Context graph without a video mapping or a real source is blocked instead of using the filename left in the workflow.

Supported continuation paths:

- `LoadVideo → GetVideoComponents → MiniMaxH3MotionContext.context_frames`; optional context audio comes from the same components node.
- `VHS_LoadVideo.IMAGE → MiniMaxH3MotionContext.context_frames`; optional context audio comes from the same loader's AUDIO output, index 2.

Both paths must feed the conditioned sampler, video/audio decoders and overlap trim. Continuation remains restricted to the supported Ref2AV + Motion Context graph at 24 fps. Other custom video or VideoExtend nodes are not certified by this change.

Disconnect `Motion Context.context_latent` when using the mapped video: the node prefers that latent over video pixels, so Director Studio rejects a graph with both connected. A connected `context_audio` inherits sound even when `audio_context_length=0`; zero means follow the video context span. To disable inherited audio in your custom variation, disconnect `context_audio`. Director Studio reports these uploaded settings rather than overriding them.

VHS must read the full source as decoded images: `frame_load_cap=0`, `skip_first_frames=0`, `select_every_nth=1`, and `force_rate=0` or `24`, without optional VAE or meta-batch connections. Incompatible values are reported, not overwritten. Model, LoRA, sampler, steps and context-window settings remain owned by the uploaded workflow; existing duration budgeting and delivery trimming still apply.

Previously saved profiles keep their mappings. Reimport a profile that lacks the video mapping, confirm its input, then validate and test it before activation.
