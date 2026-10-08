# Director conversation runtime

The Harness runtime manages the Director's model/tool loop, native session
history and compaction. It uses pinned DeepSeek Harness packages, but **does not
require a DeepSeek model**. The configured Ollama, LM Studio, llama-swap or
OpenAI-compatible model still runs through Python's provider boundary.

For system responsibilities, see [architecture](ARCHITECTURE.md). For summary
failures and history recovery, see [context recovery](HARNESS_CONTEXT_RECOVERY.md).

## Windows portable

Extract the complete ZIP and run `DirectorStudio.exe`. The Windows x64 package
includes private Node.js, Python and Harness runtimes. ComfyUI and the chosen LLM
server remain external services.

On first launch, the application downloads checksum-pinned `comfy-mcp` and
`comfy-cli` packages into `data/tools/comfy`; later launches reuse this environment.
The first launch needs PyPI access. Bootstrap failures are logged to
`data/logs/comfy-bootstrap.log` and preserve the previous valid installation.
Explicit MCP command overrides in the process environment or portable `.env`
take precedence over managed bootstrap.

The launcher validates the authenticated sidecar before starting the backend.
Native history is stored in `data/harness-sessions`; the sidecar log is
`data/logs/harness-sidecar.log`. Closing the application stops its owned sidecar.
A failed Harness startup reports the failure instead of falling back to Legacy.

Set `DS_DIRECTOR_AGENT_RUNTIME=legacy` in the portable `.env` to use Legacy.
To use a separately managed sidecar, set `DS_HARNESS_MANAGED=false`,
`DS_HARNESS_BASE_URL` to its loopback URL, and `DS_HARNESS_INTERNAL_TOKEN` to the
matching token. Restart after changing runtime settings.

## Windows source launch

Install the [source prerequisites](../README.md#run-from-source) and Node.js 22
or later, then install the Harness and backend dependencies:

```powershell
npm ci --prefix harness
python -m pip install -r backend/requirements.txt
.\start.ps1
```

The launcher starts the sidecar, verifies the backend connection, and starts the
frontend. It stores a generated internal token in ignored `.run/harness.token`;
keep that file private. Node receives an allowlisted environment. Both processes
run as the current user; the sidecar is not an OS security sandbox.

Stop services from this checkout before switching runtimes:

```powershell
.\kill.ps1
.\start.ps1 -AgentRuntime legacy
```

The launcher's runtime precedence is `-AgentRuntime`, process
`DS_DIRECTOR_AGENT_RUNTIME`, `backend/.env`, then `harness`. A direct backend
launch uses the settings default (`legacy`) unless configured explicitly.
`-FrontendOnly` does not start Harness. Logs and owned process IDs live in `.run/`.
Concurrent checkouts need separate data directories, backend/frontend/Harness
ports, and a matching frontend backend URL.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `DS_HARNESS_MANAGED` | `true` | Start the bundled portable sidecar. |
| `DS_HARNESS_BASE_URL` | `http://127.0.0.1:8791` | Authenticated sidecar URL; literal loopback HTTP only. |
| `DS_HARNESS_INTERNAL_TOKEN` | Launcher-managed | Shared authentication token for Python and Node. |
| `DS_HARNESS_MAX_STEPS` | `12` | Model steps per turn; range 1–32. |
| `DS_HARNESS_MAX_TOOL_CALLS` | `64` | Tool admissions per turn; range 1–64. |
| `DS_HARNESS_TURN_TIMEOUT_SEC` | `1800` | Turn timeout in seconds; maximum 7200. |
| `DS_HARNESS_SESSION_ROOT` | Launch-dependent | Native session directory used by the sidecar. |

The source launcher sets the URL from `-HarnessPort` (8791 by default).
For a manual sidecar launch, give Python and Node the same token, then run
`node --import tsx src/server.ts` inside `harness/`.

One model step can request multiple tools. Rejected new calls can consume the
tool budget; context refresh does not reset it. Tools execute sequentially in
Python, which validates arguments, current availability and project state.
Stale calls require fresh inference, and duplicate executed mutations are
rejected within a turn.

## History, cancellation and recovery

Harness creates a short-lived runtime handle per request and resumes a stable
native JSONL session. Python's UI transcript is imported only when initializing
that session. Summary calls omit business tools and current project payload.

Cancellation, timeout, disconnect or lost acknowledgement stops the loop without
replaying mutations. Completed edits remain saved, and submitted jobs continue
under their own lifecycle. Inspect the project and job before retrying.

`POST /api/projects/{project_id}/chat/compact` runs native manual compaction under
the same admission guard as chat. It does not send a new message, execute tools
or retry an action. Both backend and sidecar must support `native-sessions-v1`
and `context-envelope-v2`; an incompatible sidecar is rejected.

Requests are limited to 8 MiB, initial history imports to 10,000 rows, and active
turns to eight. Context estimates reserve output and image capacity; they are
not exact provider tokenization. A large current request can still need narrowing.

## Experimental focused context

`DS_DIRECTOR_TASK_CONTEXT_MODE` accepts `off` (default), `shadow`, or `pilot`.
`DS_DIRECTOR_TASK_CONTEXT_PROJECTS` is a JSON array of allowed project IDs
(default `[]`). Both settings must allow a Director project; MV uses its existing
context path.

`shadow` records comparison metrics while keeping the existing model input.
`pilot` lets the same agent choose focused `overview` / `shot_prompt` views and
read versioned project evidence. Missing evidence returns `CONTEXT_REQUIRED`;
stale evidence blocks saving. This does not expand tool permissions or reset
budgets. Return the mode to `off` to restore ordinary context on subsequent turns.
