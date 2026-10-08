# Director history and context recovery

The Python backend supplies current Director instructions, focused project state
and tool schemas. Harness owns native JSONL history and compaction checkpoints.
Python still validates tool calls against the original schemas and the project
version the model read.

## What compaction preserves

Compaction summarizes the replay history; it does not delete the original native
log, user messages or UI transcript. Old host-state snapshots can be migrated
through native compaction on first use. Manual compaction reuses that migration's
result rather than summarizing twice.

Summaries use a minimal system envelope without Director business tools or
current project payload. They cannot perform project edits. Completed business
actions are not replayed when context recovery runs.

Automatic compaction has a bounded two-attempt policy. A successful checkpoint
may remain above the soft pressure threshold if it fits the reserved input
budget. A failed summary is reported with its underlying cause and leaves the
original history available.

## When a turn fails

1. Read the complete error to distinguish summary failure from provider context
   overflow or a failed business tool.
2. Check saved shot state and generation jobs before retrying an action; earlier
   writes or submissions may already have completed.
3. Use the Director's manual compaction control to reduce replay history.
4. Focus the next request on a named shot and relevant references. Compaction
   cannot shrink an oversized current project/material payload.
5. If the sidecar is incompatible, update and restart both backend and Harness.
   The backend requires `context-envelope-v2` and `native-sessions-v1`.

Without a named shot, the context includes shot summaries. Naming a shot includes
its details; the agent can use `get_status` with `shot_id` for full saved state.
This does not change references, Layout permissions or workflow settings.

## Limits and backups

Input budgeting reserves the configured output allowance and an estimated 2,048
tokens per locally hydrated image. Text and image estimates are not exact
provider token counts or a guarantee that any request will fit.

Back up native sessions together with project data. Source launches use
`.run/harness-sessions`; Windows portable uses `data/harness-sessions`.
Do not share one session root between concurrent sidecars. Runtime settings and
log locations are documented in [Harness](HARNESS.md).
