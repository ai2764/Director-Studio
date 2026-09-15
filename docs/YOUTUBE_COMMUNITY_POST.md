# YouTube Community Post

Director Studio's new Windows update is almost here.

The Director now uses the Harness agent runtime by default. Long conversations
have a visible context meter, clear overflow/truncation status, and a manual
Compact Context action that saves the compacted summary for later turns.

Shot editing is safer and more precise. Ask the Agent to add one shot at the
end and it preserves the existing storyboard instead of rewriting every shot.
When you change a shot's Picture references, you can add a message before
saving; the Agent reviews every current Picture for that shot and decides
whether its creative brief and H3 prompt need to change.

The Windows portable setup is simpler too: extract the ZIP and run
DirectorStudio.exe. Harness, Node.js, private Python, and the installer bootstrap
are managed by the app. `Install-Tools.cmd` is no longer included or required.
The first launch needs internet access once to download and verify the locked
Comfy MCP tools; later launches reuse the private copy in the Director Studio
data folder. Ollama/LM Studio or another configured LLM server, plus ComfyUI,
still run as external services.

This update also keeps generated Layout images and extracted tail frames visible
in the Library, where they can be reviewed or deleted like other generated
assets.

I'll share the new Windows package after the final portable verification run.
