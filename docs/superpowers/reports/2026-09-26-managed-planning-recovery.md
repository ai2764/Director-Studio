# Managed planning conflict recovery — 2026-09-26

## Scope

An automatically chosen tail handoff had changed a reaction cut into a wide-to-close
push-in while retaining the close-up framing field. Replanning rejected the saved
result without a repair path. This fix does not impose fixed framing or keyword
rules, and does not manually edit any production project.

When the initial planner reports source-grounded conflicts, Python now requests
one bounded repair proposal and one separate review. The proposal may retract a
false claim without writing shots, reconsider automatic handoffs, or refine only
the four camera fields on implicated shots. The reviewer sees both original and
candidate shots, all original claims, script/directing requests, saved revision
history, and script-current confirmed project decisions. Real unresolved
requirements or rejected/malformed repairs still fail closed.

## Publication and invalidation

- Full shot/project snapshots and directing-request fingerprints are checked after
  inference and again under the project lock before publication.
- Camera edits invalidate derived prompts and current video bindings, preserve
  old job records/output files and user revision requests, and record a before/after
  receipt in the new draft's recovery history.
- Active/stopping managed runs and queued/uploading/running bound jobs prevent
  camera publication, including when the Shot's summary status is stale.
- Draft-write failure rolls back completed shot writes. Individual files are
  atomic, not a cross-file crash transaction or multi-process lock protocol.
- Director context writes now share the project lock and use atomic replacement.
- Terminal H3 callbacks reread under that lock and ignore superseded job IDs, so
  an old completion cannot reattach an obsolete clip after invalidation.
- Existing continuation removes stale automatically selected tails when a new
  execution step has no handoff; planning does not rewrite reference selections.

## Verification

Regression tests were observed failing before the corresponding changes: repair
and false-positive retraction, concurrent edits, actual-job admission, missing
revision evidence, context publication exclusion and superseded callback replay.
Additional coverage checks stopping runs, unauthorized fields, reviewer rejection,
confirmed-decision script versions and rollback on draft storage failure.

- Focused API/context/job-sync tests: **64 passed**.
- Full backend: **1660 passed, 13 skipped**, two existing Pillow deprecation
  warnings in tail-frame extraction tests; exit 0.
- Full frontend: **258 passed** across 33 files; exit 0.
- Independent read-only review: four important findings fixed and re-reviewed;
  no remaining Critical or Important findings.
- Development backend hot reload completed; API health reports Comfy and
  llama-swap reachable. No manual service termination was necessary.

No live-model planning retry or video generation was invoked for this fix. Fixture
tests verify recovery boundaries and persistence, not a real-model semantic
success rate. Production shots, chat and managed-run files were not manually edited.
