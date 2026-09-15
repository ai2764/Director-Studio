# Native Harness session recovery — approved design
User approved the staged proposal and implementation on 2026-09-12.
Scope: native durable sessions plus manual compaction; no automatic output-truncation continuation, no new summary store, no generic retry rewrite or image protocol overhaul.
Use pinned 0.1.1-rc.2 JSONL persistence and compactNow. Keep Python project authority, tool validation, generation intent and GPU leases.
Each project in a data directory has a stable session identity, independent of model choice. Import old chat text only when creating a session; subsequent calls resume its native event log. Store per-worktree sidecar logs under an explicit server-owned root; never accept disk paths from requests. Serial admission covers chat and compaction; dispose/flush before releasing.
Manual compact is a separate operation, not a user message; it never follows up, executes tools, or retries the failed user action. Show estimated before/after token counts and keep the draft. The user sends the next message explicitly.
Preserve maxTokens and actual usage in the host bridge. Length finish takes precedence over tool calls. Provider caps remain authoritative. Compaction results exclude private reasoning.
Tests: summary retained after dispose/resume; stale seed ignored; compact before retry does not execute tools; original history survives failed compression; same-session concurrent calls rejected; invalid session/operation rejected; real Python/Node bridge retains session and result; UI no automatic send.
