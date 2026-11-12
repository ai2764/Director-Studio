# Director Studio MCP v2

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
