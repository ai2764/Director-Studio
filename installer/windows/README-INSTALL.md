# Director Studio for Windows (x64)

Installed per user (no administrator rights) to `%LOCALAPPDATA%\Programs\DirectorStudio`.
Settings, projects, models, logs and the Ollama model store live in
`%LOCALAPPDATA%\DirectorStudio` (or `DS_HOME`) and are kept on upgrade and uninstall.

* Start: Start menu / desktop shortcut **Director Studio** (no console window; the browser
  opens when ready). `DirectorStudio.cmd` starts the same launcher with a console showing logs.
* Stop: Start menu **Stop Director Studio**, or `DirectorStudio.cmd stop`. Closing the
  launcher in Task Manager also ends ComfyUI, Ollama and the Harness (Windows job object).
* Logs: `%LOCALAPPDATA%\DirectorStudio\data\logs` (launcher.log, backend.log, harness.log, comfyui.log, llama-swap.log).
* Models: put ComfyUI model files under `%LOCALAPPDATA%\DirectorStudio\data\models\<folder>`
  (see docs\models.md); the health check (Settings → System) lists anything missing.
* Your own ComfyUI node packs: `%LOCALAPPDATA%\DirectorStudio\data\comfy\custom_nodes`
  (kept on upgrade/uninstall, unlike the program folder).
* NVIDIA builds (cu128 / cu130) need a recent GeForce/Studio driver (cu128: 570+, cu130: 580+).
  The CPU build runs anywhere but is only usable for testing.

Third-party components and their licences: `THIRD_PARTY_NOTICES.txt`, `SOURCE_OFFER.txt`, `licenses\`.
GPL/AGPL components (ComfyUI, custom nodes, comfy-cli, comfy-mcp) run as separate programs.
