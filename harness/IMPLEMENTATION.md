# Slim Harness sidecar

Uses pinned DeepSeek Harness packages, including `dsh-agent-loop`, stock
`dsh-compaction-basic`, and `dsh-session-persistence-jsonl` (0.1.1-rc.2).
Each HTTP request creates/disposes a short-lived runtime but resumes a stable
native session identified by the Python data directory and project ID. Model
changes retain that identity. Python UI history is requested only for the first
import; subsequent requests do not resend it. Python remains the project/shot
authority. Successful responses explicitly await native session flush.

`POST /api/projects/{project_id}/chat/compact` runs native `compactNow` under the
same admission guard as chat. It does not add a user message, execute tools or
retry the prior action. The UI displays estimated before/after token counts and
preserves the draft. No-op and failed compaction leave history available.
Old sidecars without the `native-sessions-v1` health capability are rejected
before any model/tool request is sent.

The adapter delegates every inference to the Python host, with `purpose: turn`
or `purpose: compaction`. Stock summary instructions remain in the user message
sent by the compactor. Current host context is refreshed before each inference
(including retries and summaries), and current tool availability is checked before
every sequential tool execution. Python owns authoritative tools and validation;
the schema adapter only translates Pydantic references/unions for Harness.

The launcher entry is `node --import tsx src/server.ts` (or `npm start`).
`DS_HARNESS_PORT` defaults to 8791; `DS_HARNESS_INTERNAL_TOKEN` is required.
`DS_HARNESS_SESSION_ROOT` optionally sets the server-owned persistence directory;
the default is this worktree's `.run/harness-sessions`. Never share this root
between concurrently running sidecars. Files use native JSONL with physical
compression disabled for inspectability and Node 22 compatibility. Back up this
directory if moving a worktree; normal start/stop does not remove it.
Binding is literal `127.0.0.1`; all routes require Bearer authentication.
The documented UUID/NDJSON host-request protocol is unchanged.

Bounds: 8 MiB incoming bodies, 8 active turns, 1 outstanding host request per
turn, 1–100 model steps, 2 hour + 10 second orphan deadline (Python owns the
shorter configurable turn deadline, default 30 minutes, maximum 2 hours),
30 second HTTP body timeout, and 10 second header timeout. Disconnect, DELETE,
timeout and shutdown reject pending host requests and dispose the runtime.
Transient model failures have at most two retries; tool requests are never
transport-retried or reconnected. Tool failures remain model-visible for repair.

Verification before native recovery (2026-09-12):

- `npm run typecheck`: passed.
- `npm ci --ignore-scripts`: passed from the checked-in lockfile (77 packages).
- `npm test`: 13 tests passed, exercising real Harness final/history, tool failure
  repair, finite steps, cancellation, two-retry bound, and forced context overflow
  through the stock compactor with context refresh afterwards; HTTP tests cover
  authentication, health identity, request/response streaming, DELETE, disconnect,
  invalid input and unmatched responses; schema test covers Pydantic references.
  OpenAI and Ollama length-truncated results are explicitly incomplete.
- Source formatted with pinned Prettier 3.6.2.

Install note: npm initially encountered its `edgesOut` peer resolver error. A
bootstrap `npm install --ignore-scripts --legacy-peer-deps` followed by ordinary
`npm install --ignore-scripts` resolved the peers and produced package-lock.json.
No dependency or source from the old branch was copied wholesale.

Native recovery verification (2026-09-12): 28 Harness tests including fresh Node
process restore, native write-failure propagation, manual compaction, one-time
history import, and truncated-tool suppression; 66 targeted Python tests including
a real Python/Node manual-summary round trip; 44 frontend tests plus build.

Limitations: tests use fixture host/model responses, not a live model or real
generation work. Token pricing uses stock Harness estimates. History is text
user/assistant history as specified by the Python protocol. Python's validation
retains schema constraints outside Harness's supported presentation subset.
`maxTokens` is forwarded to Python and capped by the configured Director output
budget; actual provider usage is forwarded to native session events. Python still
enriches the final request (skills/images), so native estimates are approximate,
not a complete tokenizer count. Automatic output-truncation continuation and
generic retry replacement remain out of scope. Initial legacy import still has
the transport/history bounds; a resumed native session no longer depends on the
size of the UI transcript.
