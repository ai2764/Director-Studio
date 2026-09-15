# Slim Harness runtime

This runtime replaces the Director's conversational loop with
the pinned DeepSeek Harness agent loop and stock context compaction. It does
**not** select a DeepSeek model. The same configured Ollama, LM Studio, or
OpenAI-compatible model still runs through Python's provider/VRAM boundary.
This branch launches Harness by default. Windows x64 portable bundles Harness;
Linux portable integration remains separate.

## Windows x64 portable

Extract the complete `Director-Studio-Windows-x64.zip` and run
`DirectorStudio.exe`. The archive includes a private Node.js runtime, compiled
Harness sidecar, production dependencies, the Koffi Windows x64 native module,
and a private Python runtime with a checksum-pinned `pip` bootstrap. On first
launch, it downloads the locked `comfy-mcp` and `comfy-cli` wheels into
`data/tools/comfy`; subsequent launches reuse that private environment. Do not
install Node.js, Python, npm packages, or MCP tools yourself. ComfyUI and the
selected LLM server remain external.

The backend starts the managed MCP process on demand with
`runtime/python/python.exe -m comfy_mcp.server` and supplies the adjacent
`runtime/python/comfy.exe` to it. The first launch needs PyPI access; downloads
are hash-verified and staged before atomically replacing any previous private
runtime. Failures are logged to `data/logs/comfy-bootstrap.log` and preserve the
last valid copy. Director Studio does not probe system installations.
`Install-Tools.cmd` is not included. Explicit process environment values and
uncommented portable `.env` MCP command settings skip bootstrap and take
precedence.

The executable starts the loopback sidecar, validates its authenticated
identity and capabilities, and only then starts the backend. Managed sessions
are stored in `data/harness-sessions`; the current launch log is
`data/logs/harness-sidecar.log`. Closing the application stops its owned
sidecar, and the sidecar also exits if its parent process disappears. Harness
startup failures identify the failed stage and log path; they never silently
fall back to Legacy.

Set `DS_DIRECTOR_AGENT_RUNTIME=legacy` in the portable `.env` for explicit
Legacy mode. To connect to a separately managed sidecar, set
`DS_HARNESS_MANAGED=false`, `DS_HARNESS_BASE_URL` to its loopback URL, and
`DS_HARNESS_INTERNAL_TOKEN` to the matching token. The bundled sidecar is not
started in either mode.

## Run on Windows from this checkout

Install the normal source prerequisites, Python backend requirements, frontend
dependencies, and Node.js 22 or later. Then:

```powershell
npm ci --prefix harness
python -m pip install -r backend/requirements.txt
.\start.ps1
```

The launcher starts the loopback-only sidecar automatically, verifies its
authenticated identity and the backend's connection to it, and then starts the
frontend. It stores a generated internal token in ignored `.run/harness.token`.
Do not share that file. Node receives only an allowlisted environment, not
provider keys or backend URLs. This is architectural separation, not an OS
security sandbox: both processes run as the current user.

To switch back, stop the services launched from this checkout, then restart:

```powershell
.\kill.ps1
.\start.ps1 -AgentRuntime legacy
```

Runtime precedence is explicit `-AgentRuntime`, process
`DS_DIRECTOR_AGENT_RUNTIME`, `backend/.env`, then `harness`. Existing backends
must be stopped before switching. An unavailable sidecar or incompatible
native-tool provider fails explicitly; it never silently uses legacy.
`-FrontendOnly` needs no Harness. Logs and owned process IDs are in `.run/`.
For simultaneous checkouts use distinct backend, frontend and Harness ports
and separate project data; configure the frontend's backend URL accordingly.

Backend options: `DS_HARNESS_MAX_STEPS` (12 model steps by default, 1–32),
`DS_HARNESS_MAX_TOOL_CALLS` (64 tool admissions per turn by default, 1–64),
`DS_HARNESS_TURN_TIMEOUT_SEC` (1800 by default, at most 7200), and
`DS_HARNESS_BASE_URL` (HTTP literal 127.0.0.1 only). `DS_HARNESS_MANAGED`
controls the bundled portable sidecar and defaults to true. The source Windows launcher sets
the URL from `-HarnessPort` (8791 by default). Manual launches must give Python
and Node the same `DS_HARNESS_INTERNAL_TOKEN` and run
`node --import tsx src/server.ts` inside `harness/`.

One model step can request multiple tools; tool admissions no longer share the
model-step limit. Tool admission counts include argument, availability and
stale-state rejections after a new well-formed call ID is admitted. Duplicate
call IDs and exact executed arguments remain rejected before admission;
equivalent normalized arguments can consume an admission before rejection.
Neither context refresh nor fresh
inference replenishes the tool budget. The cap permits bounded batches, not
unlimited retries; existing replay, state, transport and timeout guards remain.

## Ownership and failure semantics

- Python owns projects, shot IDs, assets, workflows/MCP, provider routing,
  generation jobs, file writes, model lifecycle/VRAM, and durable chat history.
  Every model call gets current Python context and authoritative tool schemas.
- Harness holds only an ephemeral turn: model/tool sequencing, stock compaction,
  and at most two retries of a transient inference failure. Summarization calls
  use the same Python inference boundary and cannot execute tools.
- Tools execute sequentially in Python through existing handlers. Python
  validates arguments, current tool availability, and the project snapshot.
  Stale calls need fresh inference. Duplicate executed mutation fingerprints
  are rejected within the turn; known pre-execution rejections can be repaired.
- For `revise_shot`, Python first normalizes arguments with the existing
  `ShotRevisionSubmission` business model, preserving omitted fields, then
  applies the tool schema and state checks. Numeric strings such as `"7"` and
  `"7.0"` become `7.0`; equivalent encodings share an executed-call fingerprint.
  Boolean, non-finite, null and non-positive durations and unknown fields remain
  rejected. Other tools retain their existing argument-validation behavior.
- Cancellation, sidecar disconnect, timeout, or lost acknowledgement stops the
  loop without reconnecting or replaying a mutation. Completed changes remain
  in Python; inspect the project before explicitly asking for another attempt.
  Already submitted generation jobs retain their existing job lifecycle:
  cancelling chat does not roll back or automatically cancel those jobs.
- There is no durable Node checkpoint, receipt database, resume daemon, judge
  model, or automatic cross-turn mutation deduplication. Compacted history is
  ephemeral and rebuilt from Python history on the next turn. Chat reservation
  is process-local, intended for the existing single-backend deployment; the
  snapshot check is not a cross-process database transaction.

Transport is bounded to 8 MiB request bodies, 10,000 history rows and eight
active turns. These are explicit safety limits, not silent history truncation.
Very large histories can still hit a transport limit; persistent compacted
history is deliberately deferred.

New turns seed the known host route and initial project/tool envelope before
the first pressure check, so stock compaction can summarize seeded history
before the first business inference. The normal loop replaces that seed with
its effective request header. Failed, truncated, or non-shrinking automatic
summaries stop further inference with `COMPACTION_FAILED`; they do not silently
continue on the original oversized history. Already completed writes remain.

This still uses the stock heuristic (80% context pressure, approximately four
characters per token), not exact provider tokenization or a universal input/
output budget guarantee. A very large current message, image inputs, extra
Python skill context, or a summary input that itself exceeds the window can
still require a narrower request. Original Python chat history remains intact;
only this disposable turn's replay surface is compacted.

## Verification and useful A/B test

`revise_shot` returns `{ok: true, shot: ...}` containing only the saved target's
storyboard fields, in both runtimes. It no longer repeats the full storyboard
after every single-shot edit. Persistence, project reads, final chat state, and
the existing prompt/H3 invalidation behavior are unchanged. Other storyboard
tool result contracts are unchanged.

For a shot marked `material_review_pending`, `write_prompt` now runs a Python-owned
review of all 1–9 current Picture references, one thumbnail per vision request.
It then decides whether to preserve or revise the existing Creative brief
(`script_beat`) and prompt. Missing/unreadable images, unresolved conflicts,
concurrent edits or changed file contents prevent publishing a completed review.
Failure is returned as `ok: false`, not recorded as a successful tool action.
This is shared by legacy and Harness; Node still owns no images or durable review
state. See [single-shot reference review](evaluations/2026-09-12-single-shot-reference-review.md)
for the exact scope and live-test limitations.

Real Qwen findings and the isolated slim-result retest are recorded in
[the live evaluation](evaluations/2026-09-12-harness-qwen38-live.md).

```powershell
npm run typecheck --prefix harness
npm test --prefix harness
cd backend
python -m pytest tests/test_harness_runtime.py tests/test_harness_integration.py tests/test_harness_launcher.py -q
```

Integration tests start a real Node process and the actual stock Harness loop,
while model responses and VRAM orchestration are controlled fixtures. They
exercise a real isolated shot edit, argument repair, neighboring-shot preservation,
history, provider lease exit on cancellation, and authenticated backend readiness.
Kernel tests additionally force overflow through the real stock compactor.
Tests never run a live model, GPU generation, or modify user projects.

For a live A/B, use two copies of one small fixture project, the same model and
the same prompt (for example, revise only shot 2 while preserving neighboring
shots), once per runtime. Record verified requested changes, unintended changes,
model/tool call counts, elapsed time, and how a deliberate invalid argument is
handled. Add long-history and interruption cases separately. Passing these
engineering tests does not establish improved task convergence; that remains
the outcome the live comparison must measure.
## Append-only shot authoring

`append_shot` is available in both native and Harness tool loops. It accepts
`expected_script_hash`, required `expected_last_shot_id` (null on an empty board),
and exactly one `shot` containing authored fields plus optional Picture/voice
matches. The backend allocates the new ID and appends it to the project index.
Existing Shot JSON files are never written or removed by this tool: refs, prompts,
Layouts and video/job links remain intact. It returns only the saved new Shot.

Stale script/tail, replayed tail, invalid bindings, supplied existing IDs, unknown
production fields and invalid durations are rejected before persistence. Numeric
duration strings are normalized through the same append model in both runtimes.
Appending does not certify old shots against a changed script. It suppresses
automatic full replanning for its own batch; status-only reads also never replan.
Agent instructions direct end-additions to this tool rather than `save_storyboard`.
Other tools still retain their existing powers; this is not a global prohibition
on storyboard replacement or a distributed/crash-atomic transaction protocol.

Tests: `backend/tests/test_director_append_shot.py` covers unchanged old file
bytes/mtimes, rejected writes, replay and ID collision, native execution and a
real Node sidecar roundtrip with a scripted model. Both status-before-append and
status-after-append are covered. This verifies tool execution, not Qwen's
natural-language tool selection rate; no new live-model estimate is claimed.
