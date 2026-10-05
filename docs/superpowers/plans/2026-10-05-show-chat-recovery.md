# Show chat recovery implementation plan

> **For agentic workers:** Execute inline with regression tests before each fix.

**Goal:** Fix the three failures observed in show's October 5 conversation.

**Architecture:** Ground reply Job IDs in structured project state actually supplied during this turn and in native tool receipts. Give the independent storyboard reviewer the saved board with its original indices. Retry malformed reviewer output once inside the same non-mutating review, using a bounded structured response.

**Tech stack:** Python, Pydantic, pytest, the existing local Director model adapter.

**Spec:** The user's approved scope: fix Job false positives, missing original-board evidence, and malformed semantic verdict recovery. No keyword intent rules or changes to the show project.

## Constraints and review focus

- Work in `codex/h3-video-context`, serving 5174; preserve the primary checkout.
- Keep nonexistent, other-project and chat-only Job IDs rejected.
- Free text mentioning a Job is not authoritative state evidence.
- Match numbered current requests against the original board, not candidate indices.
- Retry only malformed verdicts, never valid content rejection, transport errors or context overflow.
- Exhausted format recovery must leave the board unchanged and report a review-system failure.

## Tasks

### 1. Reply grounding

- [x] Add and run regressions for a real previous-shot source, free-text IDs and cross-project IDs.
- [x] Update `harness_runtime.py` to record IDs from structured Job fields in the delivered context.
- [x] Re-run grounding and Harness tests.

### 2. Original storyboard evidence

- [x] Add and run a four-to-three-shot merge regression that requires original index/ID/beat evidence.
- [x] Add a compact saved storyboard snapshot to the review payload in `service.py` and explain the two numbering maps in `prompts.py` and the validation guide.
- [x] Verify actual persisted merge behavior with the independent reviewer boundary isolated.

### 3. Malformed review recovery

- [x] Add and run regressions for malformed-then-valid, exhausted malformed, valid rejection and transport failure.
- [x] Implement bounded schema-based review and one format retry; return a distinct review error after exhaustion.
- [x] Preserve transactional state checks and stop Harness from treating that error as a creative repair request.
- [x] Run the backend suite, replay the original merge against the local reviewer without saving, restart the idle 5174 backend and check health.

## Result

Completed inline. Final suite: 1,983 passed, 13 skipped; original candidate accepted by the local reviewer without saving. Original blocked reply replay passed. Independent review clean after the truncation fix. Experiment service 5174 is healthy. Details: `docs/superpowers/reports/2026-10-05-show-chat-recovery.md`.
