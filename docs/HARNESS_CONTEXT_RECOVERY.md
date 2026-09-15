# Director Harness context recovery

The Python backend issues the complete Director skill/instructions and focused
project state once. Harness uses a native complete system section (literal values
through a prompt variable), not a fresh user-role snapshot every turn. Its
normalized tool presentation is also the provider's presentation. Python still
validates execution against its original schemas and the project version that
the model actually read.

Without a named Shot, the project payload contains Shot summaries. Naming a Shot
includes its details, not neighboring details. The existing get_status tool accepts
an optional exact shot_id to read full saved details before a cross-shot edit.
This does not change Layout permissions, workflow configuration or saved materials.

Native JSONL remains the durable source of history. On an old session's first use,
regenerable host-state snapshots are identified by plugin provenance and migrated
through native compactNow. Only the summarizer's input omits those snapshots;
the original log, user messages and UI transcript are retained. A small sourced
boundary permits native compaction to include the last old snapshot. Explicit
manual compaction reuses that migration's result rather than compacting twice.

Summaries use the native summarizer and checkpoint transaction, with a small
summary system prompt and no Director business tools, skill or current project
payload. Automatic pressure uses the native two-attempt limit. If successful
checkpoints still exceed the soft threshold but fit the reserved input budget,
the turn may continue. A failed summary is distinct and its underlying cause is
reported, including during provider-overflow recovery. No completed business
tool is replayed by this recovery.

The input budget reserves the configured output allowance and 2,048 tokens per
locally hydrated image. The image allowance and native text meter are estimates,
not exact tokenization or a guarantee that any image count will fit. Actual
provider counts remain the debug usage source after a response. A fixed current
request that cannot fit is stopped; compaction cannot make arbitrarily large
current input or unbounded model reasoning fit.

Both backend and sidecar must be updated/restarted. The backend requires the
context-envelope-v2 capability and will reject an older sidecar.

## Reproducible neutral real-model check

From the repository root:

    py -3 scripts/harness_context_probe.py --model "qwen3.8:27b"

Run only while Comfy and other model work are idle. The opt-in probe uses temporary
project and native-session directories, keeps real model inference, and permits
only get_status reads. It does not touch a live project.

2026-09-12 local results, final envelope implementation:

- Qwen 27.3B Q4_K_M, context 32,768, output allowance 4,096; reasoning enabled.
- 80 historical messages and 12 neutral Shots.
- Automatic summary: 26,403 input / 438 output tokens, completed.
- Three chat turns: completed, including a get_status tool call.
- Manual summary: 4,794 input / 657 output tokens, completed.
- Next fresh-runtime chat reused the checkpoint: 7,743 input / 53 output tokens,
  correctly retained the agreed BLUE color.
- Four chat turns, two summaries, one read tool, zero mutations. All finished
  normally. These are fixture observations, not a production success-rate estimate.
