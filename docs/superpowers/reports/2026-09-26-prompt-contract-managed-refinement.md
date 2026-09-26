# Prompt contract and managed refinement fixes

## Scope

Fix the Shot 7 protocol-repair failure and the Shot 2 managed-plan invalidation.
No multi-shot chat queue, production-media edits, or automatic run resumption.

## Changes

- New writers use source-backed `{{speech:line_id}}` references only. The backend
  emits exact dialogue, language labels and speaker bindings. Delivery, accent,
  emotion, staging and camera prose remain creative choices. Legacy finalized
  drafts remain readable and strictly validated.
- Readable six-section drafts expose independent dialogue/schema and Picture
  binding defects together to the existing bounded repair. No speculative tag
  replacement, extra retries or silent acceptance of incorrect speaker claims.
- A fresh placeholder draft no longer inherits obsolete rejected binding
  metadata through repair merging. Explicit newly supplied claims are validated.
- A coordinator-issued managed tail review may publish independently accepted
  camera refinements with its execution fingerprint and an audit receipt.
  Story/source/reference/duration changes require a decision before publication;
  rejected candidates are retained in existing diagnostics.
- Publication checks the current shot/event/run and project version under the
  existing lock. External changes and stopped runs cannot be absorbed. A failed
  run-record write rolls the shot back.
- Canonical video submission can also refresh a prompt. Job binding recognizes
  only that exact same-event refinement receipt and still checks actual project
  state. Unrelated changes cancel the unbound job and pause the run.
- Tail review now receives the original managed camera plan and execution
  authority. Editorial continuity is not automatically treated as a required
  continuous camera move. No keyword-based transition rules were added.

## Verification

Used systematic debugging, test-driven development, parallel scoped work and
independent code review. New regression tests were observed failing before the
corresponding fixes. Review found the canonical-submit refresh path; it was
reproduced, fixed and re-reviewed with no remaining Important/Critical findings.

- Final backend suite: **1640 passed, 13 skipped**, 2 existing Pillow deprecation
  warnings in tail-frame extraction tests.
- Frontend suite: **258 passed** across 33 files.
- Real canonical submit endpoint exercised with only model responses scripted
  and GPU execution stubbed; reference staging, storage and version checks real.
- `git diff --check`: clean (Windows line-ending notices only).
- Backend restarted after confirming no active managed runs, no active Kira chat,
  and empty Comfy queue. Backend/Comfy/LLM health and Harness readiness verified.
- Existing Kira run remains paused at Shot 2 with no current Job. No real video
  generation or creative choice was made on the user's behalf.

## Boundaries

Tests establish framework behavior, not real-model creative accuracy or spoken
accent quality. Explicit creative constraints still require semantic review.
Publication uses process-local locking and individually atomic files, not a
cross-process database transaction; a crash between file writes fails closed via
the fingerprint check. Existing paused plans are not retroactively re-authorized.
