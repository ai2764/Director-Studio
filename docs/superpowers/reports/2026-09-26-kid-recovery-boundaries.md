# Kid recovery boundaries

## Scope and evidence

The kid conversation's duration-only instruction (`15s`) was followed by a
rejected storyboard, then `set_script` deleting the requested entrance beat.
The altered screenplay and storyboard were actually saved. Separate reference
observation attempts failed on uncertain values and invalid source/conflict
quotations. These were model-output contract failures, not missing uploads.

Existing managed-planning work was committed first as `f3c2002`. The unrelated
`output/` directory was excluded. This report describes the subsequent changes.

## Changes

- A failed storyboard submission marks its existing, turn-local budget as being
  in recovery. Both conversation runtimes hide `set_script`; shared dispatch also
  rejects it. This does not set `Project.script_locked`. A new user turn restores
  normal authoring. Successful submissions alone do not restrict editing.
- Semantic review separates blocking `issues` from non-blocking `warnings`.
  Generation uncertainty is advisory, while actual requirement contradictions
  remain blocking. No action-count, character, language, or wardrobe keywords
  are used to classify requests or restrict creative choices.
- Advisory warnings survive publication and appear in tool results. Replacement
  previews retain and display warnings before the user confirms replacement.
- Visual reinspection and structural/source repair have separate bounded
  allowances: one reinspection, two structure repairs, four total model calls.
  Repeated identical validation errors stop early. Invalid facts or quotations
  are not silently accepted, rewritten, or promoted to user authority.

## Verification

- Before the first commit: 45 managed-planning/context-publication tests passed.
- New regression tests reproduced six failures before the initial implementation.
- Focused integration run: 102 passed.
- Review found advisory overflow blocking publication and missing preview
  warnings. Both were reproduced with failing tests, fixed, and independently
  rechecked. Focused replacement/reference tests: 37 passed.
- Frontend: 258 passed across 33 files.
- First backend suite: 1667 passed, 13 skipped, 2 failed. Both failures were old
  prompt-prose assertions requiring the previous hard rejection categories:
  - `test_save_storyboard_semantic_rejection_receives_complete_grounding_and_is_transactional`
  - `test_storyboard_validation_stage_guide_loads_with_semantic_contract`
  Transactional/grounding assertions were retained; the fixture now represents
  a genuine required-beat conflict. Guide loading is tested separately from
  policy prose. The affected two files then passed all 70 tests.
- Final backend full suite: **1671 passed, 13 skipped**, 391.93 seconds. Two
  existing Pillow `getdata()` deprecation warnings remain in tail-frame tests.
- Final independent review: both reported issues resolved, no new findings.
- Read-only service check confirmed kid remains `script_locked=false`.

## Limits and runtime impact

This is a bounded recovery fix, not a general natural-language authorization
classifier or the separately proposed final-response completion protocol.
Semantic risk classification still depends on the model. Mocked model responses
exercise real tool dispatch and persistence, but do not establish live-model
reliability or H3 visual quality. No new video was intentionally submitted.

No production screenplay, references, or video files were directly edited.
However, the development server watches test files too: a test-file edit caused
hot reload while kid job `job_interrupted_example` was running. The shutdown cancelled
that job. The user was informed; it was not automatically resubmitted. Earlier
successful video `job_completed_example` remained present (3,884,300 bytes). Further
watched-file edits stopped once this was identified. Future concurrent production
and development should use an isolated checkout or a non-reloading service.
