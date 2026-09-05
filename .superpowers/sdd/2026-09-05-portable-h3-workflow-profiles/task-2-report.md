# Task 2 Report: Generic H3 Runtime and Per-job Profile Snapshots

## Status

Implemented and verified.

## Delivered behavior

- Added the optional synchronous `prepare_job_submission(job)` pipeline hook and invoked it before job enrichment, queue reservation, or background task creation.
- Local H3 submissions (`local` and `mcp`) resolve and snapshot the active profile before entering the queue. Both the built-in official profile and installed custom profiles are captured.
- MiniMax cloud (`h3_api`) submissions skip the local workflow-profile snapshot.
- Each snapshot is stored under the job's `workflow_profile` directory as `workflow.api.json` and `profile.json`, using the profile store's atomic write implementation.
- Local H3 job params now record `h3_profile_id`, `h3_profile_sha256`, and `h3_contract_version`.
- Snapshot loading verifies profile metadata, workflow SHA-256, and pure Ref2AV semantics before returning a `ResolvedH3Profile`.
- `fill_profile_graph(profile, job_params)` now fills the graph through `H3BoundaryMapping`, including remapped node IDs, remapped input names, dynamic Picture/Audio socket patterns, seed, and output prefix.
- Workflow-owned model, sampler, scheduler, steps, denoise, decode, codec, and other internal values remain untouched.
- The compatibility `fill_ref2va_graph()` path delegates to the generic profile filler, preserving existing callers and official behavior.
- H3 `build_prompt()` and `map_history_outputs()` consume the job snapshot when profile identity is present, so active-profile changes cannot alter queued or running jobs.
- Output mapping honors the snapshotted saver node ID and ordered `output_fields` contract.
- H3 job responses expose the snapshotted profile identity, workflow hash, and contract version.

## TDD evidence

Red phase:

- Added profile equivalence, remapped-boundary, queued hot-switch, snapshot-consumer, MiniMax exclusion, response identity, output-field, and default-hook tests before implementation.
- Initial focused collection failed because `fill_profile_graph` and `load_job_profile_snapshot` did not exist.
- The built-in snapshot branch received an explicit mutation check: disabling built-in snapshots made its dedicated test fail with `ProfileStorageError`, then restoring the implementation made it pass.

Green phase:

- Focused command: `py -m pytest tests/test_h3_profile_runtime.py tests/test_h3_ref2va_graph.py tests/test_no_i2v_on_h3_pipeline.py tests/test_job_execution_adapters.py -q`
- Result: `43 passed in 0.54s`.
- Full backend command: `py -m pytest -q`
- Result: `710 passed, 2 warnings in 27.05s`.

## Self-review

- Confirmed the built-in graph's fully normalized output remains byte-for-byte behavior-equivalent via a captured canonical SHA-256 from the pre-profile filler.
- Confirmed custom node IDs and socket names are taken only from the mapping contract.
- Confirmed active profile selection is consulted only at local submission; later graph construction and history mapping use the job snapshot.
- Confirmed profile/workflow races during snapshot creation are detected by metadata and workflow hash rechecks.
- Confirmed `git diff --check` reports no whitespace errors (only the repository's Windows line-ending notices).
- No custom workflow data, input media, output media, secrets, or machine-specific paths were added.

## Known concerns

- The full suite emits two existing Pillow `Image.getdata` deprecation warnings from `test_tail_frame_extraction.py`; they are unrelated to Task 2.
- Legacy/direct local H3 callers without profile identity fields continue to resolve the current active profile for backward compatibility. All jobs submitted through `start_pipeline_job()` now receive a snapshot before queue execution.

## Fix round 1: Recovery preserves the original snapshot

Reviewer finding reproduced:

- A queued local H3 job was snapshotted with `first-profile`.
- After selecting `second-profile`, `recover_interrupted_jobs()` replayed through `start_pipeline_job()`.
- The original implementation unconditionally resolved the active profile and changed the recovered job to `second-profile`.
- A deliberately corrupted existing workflow snapshot was also silently replaced instead of failing.

Fix:

- `snapshot_for_job()` now detects either an existing `workflow_profile` directory or any persisted H3 snapshot identity field before resolving the active profile.
- Existing snapshots are loaded through the verifying snapshot loader, which checks metadata, workflow SHA-256, and pure Ref2AV shape.
- The loaded profile ID, hash, and contract version must match the job record exactly.
- A valid existing snapshot is returned without reading active selection and without rewriting snapshot files or job identity.
- Missing, partial, corrupt, or mismatched snapshots raise explicitly; only a true first submission resolves and captures the active profile.

TDD and verification evidence:

- The new recovery test failed with `second-profile` where `first-profile` was expected.
- The new corruption test failed because no `ProfileChangedError` was raised.
- Both targeted tests passed after the snapshot reuse guard was added.
- Focused H3/runtime suite: `45 passed in 0.66s`.
- Full backend suite: `712 passed, 2 warnings in 27.43s`.
- The response-schema test was also corrected to use an in-memory `JobRecord`; it no longer leaves queued jobs in the repository's ignored runtime data directory.
