# Material-review intent and progress verification

## Scope

- Normal material review receives verified shot-authoring provenance, historical
  directing requests and the current prompt request, as does the prompt writer.
- Saved shot fields are the execution target. The screenplay provides background;
  reference review cannot replace the brief or reapprove the story.
- Reference identity/design and desired rendering style remain separate concepts.
- Existing exact-file, stale-input, dialogue and prompt-retry scope checks remain.
- Existing chat progress reports reference observation, suitability inference and
  normal prompt writing/repair starts and completion/failure durations.
- The separate tail-frame writer was not changed. Legacy chat keeps readable
  progress text but does not forward the extra structured phase metadata.

## Evidence

- Regression tests first failed for missing intent, unsolicited brief changes and
  missing phase forwarding, then passed after implementation.
- Material review, scoped retry and source recovery: **70 passed**.
- Native prompt/acceptance tests after updating callback-compatible test doubles:
  **4 passed**.
- Full backend run in an isolated checkout: **1697 passed, 6 failed, 13 skipped**,
  plus two existing Pillow deprecation warnings. Two failures were outdated test
  doubles; they were corrected and their tests passed.
- The other four failures belong to pre-existing, uncommitted VRAM changes:
  `test_unconfirmed_unload_blocks_gpu_handoff[http]`,
  `test_unconfirmed_unload_blocks_gpu_handoff[stuck]`,
  `test_unconfirmed_unload_blocks_gpu_handoff[malformed]`, and
  `test_comfy_does_not_start_when_local_lifecycle_release_fails`.
  Reverting only that pre-existing VRAM diff in the isolated test copy made all
  four pass. The workspace VRAM file was preserved. The entire workspace suite
  is therefore **not green**; no claim of a fully passing suite is made.
- Read-only live Qwen review of the actual kid shot with cached visual evidence:
  **83.5 seconds**, `blocking_question=null`, `brief=null`, `rewrite_prompt=true`.
  This tests review semantics, not end-to-end prompt latency or generated quality.
  No prompt, shot or video was saved by the probe.
- Independent code review found no critical or important issues.

## Deployment

The backend restart command was rejected by the execution policy before it ran.
The existing backend process remains in place and has not loaded this fix.
Restart the port-8790 backend to apply it. No automatic project retry was started.
