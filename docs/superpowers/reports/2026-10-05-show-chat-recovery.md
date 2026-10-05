# Show Director chat recovery

## Observed failures

- Revising a continued shot and saving its prompt both succeeded, but the final reply was replaced by a Job grounding error. The previous shot's real source Job was provided in structured project context rather than that turn's tool receipt.
- A four-shot board became three shots by combining the pose and turn. The independent semantic reviewer had no original board and confused the newly numbered third candidate with the original third shot.
- Malformed semantic verdict JSON was exposed as a storyboard content rejection. The Agent's next whole-board submission then encountered duplicate-mutation protection.

## Changes

- Harness records Job IDs in structured Job fields of the context provided in this turn. Script/chat prose is not proof. Final reply checks still require an existing Job owned by the current project.
- The storyboard reviewer receives original indices, stable IDs, action, duration, camera and dialogue evidence from the saved board. Review instructions distinguish original numbering from candidate order.
- Production review uses the existing bounded model adapter with the semantic verdict schema and a 2,048-token output budget. Malformed or completed-but-truncated output gets one format retry with identical candidate/evidence. Real content rejection, context overflow and transport failures are not format retries.
- Exhausted format recovery returns `STORYBOARD_REVIEW_INVALID`, no fabricated content issues, and `retryable=false`. Harness stops mutations for that turn and reports the review-system failure. Transactional checks still prevent overwriting a concurrent edit.

## Verification

- Before implementation: four regression failures reproduced the observed boundaries; five negative/control cases passed.
- Grounding/Harness/storyboard recovery group: 78 passed.
- Native tools/replacement/module boundaries group: 108 passed.
- A review found the real model adapter's output truncation bypassed format recovery. Two real-adapter regressions failed before the typed truncation fix; show/Director tests then passed (90 tests).
- Independent review of the final fix reported no remaining concrete findings.
- Read-only local Qwen replay of the original four-to-three-shot candidate returned `valid=true`, no issues, two advisory warnings. The source show's project/shot/chat files were unchanged during the probe. A tokenizer preflight timed out; the completed structured model verdict was still obtained.
- Read-only replay of the originally blocked assistant reply preserved the complete reply and grounded its genuine source Job.
- First full backend run: 1,980 passed, 13 skipped; one old test client lacked the newly used structured `chat_response` interface. Its fixture was updated and its original persisted-state/guide assertions retained; the test passed in the 90-test run.
- Final full backend run: **1,983 passed, 13 skipped**, exit 0 in 438.76 seconds. Two existing Pillow `getdata` deprecation warnings remain in tail-frame tests.
- Changed-file secret scan passed. No generated media or user project edits are part of this fix.
- The idle experiment backend was restarted; backend reports instance `video-context` with authenticated Harness on 8793 (`sidecar_ready=true`), and LAN frontend 5174 returned HTTP 200.

## Scope

Changes are on `codex/h3-video-context`. No keyword-based merge or continuation intent checks were added. Existing nonexistent Job, cross-project Job, unexposed Job and destructive storyboard replacement protection remain covered.
