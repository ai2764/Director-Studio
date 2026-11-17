# Director Studio MCP v2

## Compact catalog (default)

The default catalog keeps native Director tool names and their common
`project_id`, `request_id`, `expected_revision`, `objective`, `arguments` envelope.
Detailed business argument schemas and instructions are loaded when needed:

1. Read `get_project_context(project_id, objective)` for state, revision and the
   available tool index.
2. Call `get_tool_contract(tool_name="append_shots")` (or the chosen tool name).
   Its `inputSchema` is the complete call schema, including definitions. Do not
   guess fields inside `arguments` from the compact catalog.
3. Call the named Director tool with that schema. Follow its complete instructions
   and inspect its asynchronous receipt before proceeding.
4. Refresh project context after writes. Reuse a previously fetched contract only
   within the same server version; fetch it again on a schema error or reconnect.

H3 setup is available through ONE entry, `h3_workflow(tool_name, arguments)`.
Its `tool_name` enum lists supported H3 operations. First fetch that operation's
contract with `get_tool_contract`, then pass its **entire** argument object inside
the gateway's `arguments`. Example:

```json
{"name":"get_tool_contract","arguments":{"tool_name":"import_h3_workflow"}}
```

```json
{"name":"h3_workflow","arguments":{"tool_name":"import_h3_workflow","arguments":{"request_id":"import-one","workflow":{},"mapping":{},"display_name":"My H3 workflow"}}}
```

The empty graph/mapping above are placeholders, not a valid import. For the full
mapping schema and setup instructions, use
`h3_workflow("get_h3_workflow_contract", {})` only when connecting workflows.
The H3 operation names in the rest of this document refer to this gateway in
compact mode, and to individual MCP tools in full mode.

Execution still validates the full canonical schema and the backend still
enforces revision, deduplication, generation and activation requirements. Missing
fields produce an error pointing to `get_tool_contract`; failed writes require
state inspection before retrying. No contract-read state is shared between
clients. This reduces metadata, not the underlying checks. Model task reliability
and total tokens (including contract reads/retries) still need client testing.

For older clients or comparison testing, set this before starting the bridge:

```powershell
$env:DIRECTOR_MCP_CATALOG_MODE = "full"
python .\mcp\server.py
```

Use `compact` (or unset the variable) to restore the default. Reconnect the client
after changing modes so it refreshes its catalog. Full mode advertises the original
individual H3 and complete Director schemas. Cached legacy tool calls remain
supported in compact mode. `get_project_context(..., include_tool_schemas=True)`
also explicitly requests the original full tool definitions; it defaults to true
in full mode and false in compact mode. Backend/internal Director APIs are unchanged.

Reproduce offline metadata estimates using the project Python environment with
`tiktoken` installed: `python mcp/measure_catalog.py`. This makes no network/model
requests and measures serialized compact JSON rather than actual host billing.
On the 2026-11-16 implementation, `o200k_base` estimates were 16,683 tokens for
48 full-mode tools and 5,528 tokens for 39 compact-mode tools (about 67% less).
Reading the full `append_shots` contract adds about 2,077 tokens when needed;
the H3 mapping contract adds about 1,837. These are metadata measurements, not
proof of task success rates or net savings over a complete agent session.

The adapter exposes the backend's native Director tool schemas and executor. The backend owns validation and project state. Run one backend worker so calls share the UI's locks and task registries.

## Native operations

1. Read `get_project_context(project_id, objective)` for current revision, evidence, instructions and available schemas.
2. Call the native tool by name, such as `append_shots`, `review_asset_coverage` or `save_storyboard`, with `project_id`, `request_id`, `expected_revision`, `objective`, and `arguments`.
3. Inspect `get_agent_task(task_id)`. Acceptance is separate from completion. Status is queued, running, succeeded, failed or interrupted. Results include actual actions and bounded native progress events.
4. Refresh context after mutations. Retry an uncertain submission with identical input and request ID. Different input under the same ID is rejected. Inspect project state after interruption before deciding on another operation.

Authoring mode is preserved. Native business preconditions, schema validation, stale-state checks, busy-state checks and confirmation gates still apply. Tools may perform internal model inference, but the external agent chooses the tools and supplies arguments. Storyboard replacement can first save a proposal: inspect results before claiming replacement. UI upload classification and temporary context navigation are not business operations exported here.

The temporary workspace tools and `ask_director` were removed. Project creation now requires a stable `request_id`, saves the supplied script verbatim, and returns a durable operation result containing the actual created project. Identical retries return the same record.

## Recovery and deployment

Receipts live under the backend data directory in `agent-tasks`. Native operations save pre-operation project and shot snapshots as recovery evidence. They are not restored automatically. Unfinished operations from a previous backend process are reported interrupted. Binary assets are not backed up by this feature. Progress uses existing execution events; cancellation is not yet exported.

Install `mcp/requirements.txt`, then run `python mcp/server.py`. The default backend is `http://127.0.0.1:8796`; override with `DIRECTOR_STUDIO_URL`. MCP listens on loopback port 8798, configurable with `DIRECTOR_MCP_PORT`.

To publish through Tailscale Serve or another trusted reverse proxy, set `DIRECTOR_MCP_ALLOWED_HOSTS` to the comma-separated public Host headers, including the public port (for example `studio.example.test:8798`), before starting the MCP process. Localhost remains allowed. Configure the proxy separately to forward to `http://127.0.0.1:8798`; clients connect to its `/mcp` path. The Host allowlist protects transport routing; it does not authenticate callers. This deployment relies on private-network access controls and has no per-caller or per-project authorization. Keep machine names, tailnet addresses, tokens and local configuration out of source control.

Internal Comfy MCP job status reads tolerate up to two minutes of consecutive polling errors, bounded by the overall job deadline. Retries use the same prompt ID and never resubmit generation. Confirmed execution failures still fail immediately. When monitoring remains unavailable, the error identifies the prompt and states that the remote result is unknown; inspect that existing task before requesting another generation.

## Mailbox

Use `send_message`, `list_messages`, and `acknowledge_message` with codex and muse. Save `next_after_id` as the read cursor. Storage defaults to `.run/mcp-mailbox.sqlite3`, configurable with `DIRECTOR_MCP_MAILBOX`. Delivery does not wake agents. Sender names are supplied by callers and are not authenticated.

## External image import

`import_asset(request_id, project_id, kind, name, image_base64, filename, notes='', file_key='master')` accepts raw Base64 bytes, not a local path or URL. Supported kinds are actors, costumes, scenes, props and layouts. Supported image formats are PNG, JPEG, WebP and GIF, up to 40 MiB decoded. The MCP request body limit is 60 MiB to accommodate Base64 expansion. Clients should programmatically construct large payloads.

The bridge sends a multipart upload to the existing backend. Each image creates one LibraryAsset; multiple images are not merged into an existing asset. Identical retries with the same request ID return the original receipt. Check `status` and `result.asset` for the actual ID, project and file keys. SHA256 and byte count identify the unchanged source bytes. Import does not generate, redraw or bind a shot. Use `fullbody_threeview` or `bust_threeview` for actor sheets, `master` for ordinary references, and `layout` for layouts; use exact returned keys when subsequently binding with `patch_shot_refs`. Put identity and use constraints in `notes`.


## Agent-authored H3 / H3N workflow connection (contract v4)

This entry point does **not** use the human setup wizard, official-node discovery,
or an assumed `MiniMaxH3ReferenceToVideo` node. Read `get_h3_workflow_contract()`
for the complete JSON Schema, then submit the API graph and explicit boundary:

```json
{
  "inputs": {
    "kind": "direct",
    "prompt": [{"node_id": "text", "input_name": "text"}],
    "width": [{"node_id": "size", "input_name": "width"}],
    "height": [{"node_id": "size", "input_name": "height"}],
    "frames": [{"node_id": "sampler", "input_name": "frames", "offset": 22}],
    "seed": [{"node_id": "noise", "input_name": "seed"}],
    "pictures": [{"node_id": "image1", "input_name": "image"}],
    "audios": [{"node_id": "voice1", "input_name": "audio"}]
  },
  "context_video": {"node_id": "video1", "input_name": "file"},
  "output": {"node_id": "save"}
}
```

IDs and port names above are illustrative: use the actual uploaded graph. Omit
`context_video` and use `audios: []` for a graph without those inputs. Numeric
bindings support `value * scale + offset` (defaults 1 and 0), and may fan out to
several literal ports. For example `scale: 0.041666666666666664` maps 24-fps frame
count to a FLOAT duration; `offset: 22` can budget overlap only when that is what
the actual graph needs. No expression evaluation or arbitrary code is supported.

Bind Picture/Audio slots to existing loader **file-name string inputs**, not to
IMAGE/AUDIO tensor connections. Slots are ordered and fixed in number: each
submission must supply exactly the mapped Picture and Audio counts. Missing slots
fail instead of silently reusing example files. To change arity, import and test
a new mapping. The workflow owns internal topology, loaders, conditioning, sampling,
context windows, audio inheritance, fps and overlap/output cropping; DS does not
insert official nodes or automatically repair those internals in v4. Existing
shot source-media preparation still uses the application's 24-fps video-context
conventions. Legacy profiles keep their existing official-node handling.

### Tool sequence for Muse

1. `get_h3_workflow_contract()` and `list_h3_workflows()`.
2. `import_h3_workflow(request_id, workflow, mapping, display_name)`.
   `workflow` and `mapping` are JSON objects, not local paths. Read `status` and
   `result.import_id`. Invalid bindings return `status: failed` with structured
   issues. No video is generated or active profile changed by import.
3. `validate_h3_workflow(request_id, import_id)` checks the filled graph using
   Comfy MCP, without enqueueing a video. Inspect the returned receipt.
4. `test_h3_workflow(request_id, import_id, picture_asset_ids, audio_asset_ids,
   context_project_id, context_upload_id, native_source_job_id)` starts an isolated real local test.
   Use actual Library IDs from `list_assets`, in binding order. Context IDs must
   refer to a previously uploaded source video in that project. This performs
   GPU inference and requires the user's generation instruction.
5. Poll existing `get_video_job(result.job_id)`. Setup receipt `status: succeeded`
   means submitted, **not** that the video succeeded. Use
   `inspect_h3_workflow(import_id)` to read the exact saved graph, mapping,
   lifecycle, and `test_evidence.candidates` containing artifact indices and URLs.
   If multiple candidates require selection, call
   `select_h3_test_output(request_id, import_id, artifact_index)`.
6. Refresh `list_h3_workflows()` and pass its current `active.profile_id` and
   `active.workflow_sha256` as `expected_active_profile_id` and
   `expected_active_workflow_sha256` to `activate_h3_workflow`.
   Validation and successful downloaded test output for the SAME workflow and
   mapping are required. Activation is global for subsequent LOCAL H3 jobs;
   it does not change the MiniMax official API provider or existing snapshots.
7. `select_h3_workflow` accepts an installed profile ID (including
   `builtin-official-h3`) and the same expected-active fields for rollback.

Native AV latent context uses optional `mapping.native_latent_context`.
A cache consumer maps paired `load_path`/`load_index` ports on
`MiniMaxH3MotionContextLoadLatent`. A graph that exports a new cache also maps
ALL of `save_prefix`, `save_index`, and `save_output_node_id` for its terminal
`MiniMaxH3MotionContextSaveLatent` node. An input-only graph may omit that entire
save group; it consumes a verified source but cannot become a native cache source
for a following job. Bootstrap maps the save group and omits both load ports. This mapping requires direct inputs and excludes
`context_video`. Both SaveVideo and cache output roots remain in the submitted
graph. DS replaces demonstration cache values with
`director-studio/<jobid>/native/clip`, index 1, and verifies the new
`clip_00001.safetensors` using HTTP HEAD before marking success. No latent is
downloaded. Native continuation takes `native_source_job_id` from a succeeded
local DS H3 job with a verified cache on the same Comfy host and with identical
dimensions. Arbitrary caller cache paths and video uploads are rejected.
Previous-shot production continuation reuses saved job provenance and skips
MP4 upload and reencoding. Frame budgets, overlap trimming, and audio behavior
remain properties of the imported graph and its explicit direct numeric mappings.

Both activate/select tools accept optional `standalone_profile_id` for a tested
profile that requires no video/native source. The active pointer persists this
fallback and `list_h3_workflows().active.standalone_profile_id` exposes it.
It takes precedence over `DS_H3_STANDALONE_PROFILE_ID`; omitted tool values clear
the persisted override. A continuation workflow without a source requires this
explicit fallback and never silently executes frozen demonstration cache paths.

Each mutation requires a stable, unique `request_id`. Identical retries return
the saved receipt, including the same test job ID. Changed inputs under the same
ID return HTTP 409. Receipts survive MCP restarts in backend `agent-tasks`;
interrupted operations are never automatically re-executed. Inspect state before
choosing a new ID. Failed receipts are terminal for that ID.

An active context-video profile requires a source video on continuation shots.
First shots without a source need the existing explicit standalone route
(`DS_H3_STANDALONE_PROFILE_ID`); v4 does not silently choose another graph.

HTTP equivalents live under `/api/agent/h3-workflows`: `GET /contract`,
`GET` (list), `GET /imports/{import_id}`, and `POST /operations` with
`{request_id, action, arguments}`. Actions are `import`, `validate`, `test`,
`select_test_output`, `activate`, `select`. These reuse the same profile store,
validation/test evidence and immutable job snapshots as DS Studio.


## Update an existing import (contract revision 4.2-native-input-only)

Refresh `get_h3_workflow_contract`, which returns `contract_revision`, full
`mapping_schema`, and `native_context_examples`. A graph with LoadLatent203 but no
SaveLatent205 can add this TOP-LEVEL mapping field without deleting203 or inserting205:

```json
"native_latent_context": {
  "load_path": {"node_id": "203", "input_name": "latent_path"},
  "load_index": {"node_id": "203", "input_name": "clip_index"}
}
```

1. Read `inspect_h3_workflow(import_id)`. Copy its COMPLETE `mapping` and add or
   replace that top-level field; preserve inputs/output and the graph.
2. Call `configure_h3_workflow(request_id, import_id, mapping,
   expected_workflow_sha256, expected_mapping_sha256)`, using hashes from
   `lifecycle.workflow_sha256`/`mapping_sha256` (null if not yet mapped).
3. Check the receipt; success preserves `import_id` and graph bytes, clears old
   validation/test evidence, and returns current mapping/lifecycle. Replay with
   the identical request ID is safe; stale hashes fail. Invalid mappings do not
   invalidate existing evidence.
4. Run `validate_h3_workflow` with a NEW request ID. Then test with a verified
   `native_source_job_id`. Do not supply an arbitrary cache-folder string or
   assume an old validation receipt describes the new mapping.

Input-only graph success proves video delivery, not cache export. To chain
another native continuation from that result, the workflow must actually have
a mapped cache-save output. The service never rewrites graph topology for you.


## Discover an existing native source (revision4.3-native-source-discovery)

Before creating another bootstrap, call `list_h3_native_sources()` with no
project_id. This includes ALL projects and unassigned setup-test jobs, checks
canonical source eligibility and a fresh nonempty cache-file HEAD, then returns
`job_id`, dimensions, `cache_available`, cache size, and native metadata. A removed
installed Profile does not delete a succeeded job's independent cache.

`get_video_job` and `list_video_jobs` now return sanitized `native_latent_context`
metadata when present (verified, source_job_id, save_prefix/index, width/height).
This records generation-time verification; use the discovery tool for live file
availability. `unavailable_sources` distinguishes recorded cache candidates whose
files disappeared or whose host cannot be reached.

Use one returned job_id as `native_source_job_id` in `test_h3_workflow`. Do not
use an ordinary video-only task merely because it succeeded. A source does not
need its original Profile to remain installed, and it may be an unassigned setup
test rather than a production-project task. No generation or activation is
performed by discovery. If no valid source is returned, create a bootstrap graph
with an actual mapped SaveLatent output and test it first.
