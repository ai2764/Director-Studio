# Verified image reuse and phase measurement

Automatic chat attachments now reuse backend-validated visual observations when image bytes, asset metadata, source evidence and policy still match. The numbered descriptions and visual facts remain in the model context. Explicit uploads always retain their images. Reinspection requests, changed inputs and unresolved uncertainty retain fresh images; missing details must be checked through `inspect_asset`.

Both Harness and legacy chat preserve the Visual QC guide when observations replace images. Prompt writing continues to receive its existing full reference evidence and publication checks. This change does not remove the actual Picture inputs submitted to H3.

Existing observations remain eligible after upgrade when their policy and sources validate. New observations also carry the script signature. A cache hit certifies reusable model evidence, not human approval or completeness of every possible visual detail.

## What is measured

The backend writes bounded metadata-only logs to `<data_dir>/logs/llm-performance.jsonl`, rotating to `llm-performance.previous.jsonl` at 5 MiB. They contain no prompts, image bodies, model answers, or provider error bodies.

- `image_context`: preparation time, attached images, reused observations and payload character counts.
- `llm_call`: call duration, model, phase, image count, text characters, output size and available provider token usage. Chat, compaction, visual observation, suitability review, prompt writing and bounded review calls pass through this measurement.
- `phase`: existing pipeline phase durations, including source-frame processing, visual observation, review, writing and repair.

`writer.generate` and `writer.vision` preserve the existing string-returning provider APIs, so their token usage remains unknown. The measured request time includes adapter preflight and network wait; it is not isolated GPU prefill/decode time. Model loading/GPU queue time outside the adapter is not attributed to a model call. Phase totals and contained model calls overlap: do not add them together as turn wall time.

## Controlled before/after test

1. Use one project with verified observations. Complete a normal reference review first if needed. Use the same model, shot, user request, history and output budget for both runs. Preserve identical warm/cold model state; restore a project/session copy between runs if the request changes state.
2. For the baseline, start the backend with `DS_DIRECTOR_REUSE_VERIFIED_IMAGES=false`. This leaves the new telemetry enabled while restoring image attachment behavior. Restart is required after changing this environment setting.
3. Copy the log to `before.jsonl` before running, note its initial length, then isolate the new records into a baseline log after the request. Do not compare accumulated records from earlier unrelated work.
4. Restart with `DS_DIRECTOR_REUSE_VERIFIED_IMAGES=true` (the default), restore the same test state and repeat. Isolate the resulting records into `after.jsonl`.
5. Run at least three equivalent trials per condition, separating cold and warm results. Confirm prompt quality: Picture numbering, identity, outfit, framing, positions, uncertainty handling, and all six H3 prompt sections.

From the repository root:

```powershell
python scripts/llm_performance_report.py before.jsonl after.jsonl
```

For a single run:

```powershell
python scripts/llm_performance_report.py data/logs/llm-performance.jsonl
```

The report groups by model, phase and request path, showing sample count, total/mean time, attached/reused images, known token counts and failures. Missing phase records are reported as unknown rather than invented zero-duration savings.

## Validation on 2026-10-21

Regression tests demonstrate a nine-reference case changing automatic attachments from the previous four-image limit to zero, while all nine numbered observations reach both chat runtimes. This is an attachment reduction, not a measured inference speedup. The configured remote model endpoint timed out, so no hardware latency or creative-quality improvement has been established.

Final focused checks passed: 53 image/telemetry/chat-usage/context tests and 92 reference-contract/Harness tests (145 total). Independent review findings about uncertain facts and lost Visual QC guidance were fixed and covered by failing-then-passing regressions.

The first full run started before the final configuration field was added and retained an old in-memory Settings instance: 2336 passed, 24 failed, 16 skipped. Its failure list was rerun against final code: 119 passed, 11 failed, 2 skipped. No task-specific image reuse/vision tests remained failed. Remaining failures, outside the files changed by this task, are recorded in `data/llm-optimization-recheck.log`:

- `test_comfy_launch_options.py::test_auto_preset_is_minimal`
- `test_h3_workflow_profiles_api.py::test_profile_listing_starts_on_builtin_template`
- `test_installer_fetch.py::test_extract_strips_and_filters`
- `test_llama_swap_runtime.py::test_quoting_paths_with_spaces`
- `test_managed_process.py::test_start_crash_restart_stop`
- `test_managed_process.py::test_orphan_cleanup`
- `test_media_binaries.py::test_configured_dir_wins_over_path`
- `test_media_binaries.py::test_bundled_before_path`
- `test_ollama_bundled_runtime.py::test_bundled_ollama_starts_on_private_port_with_ds_models_and_ctx`
- `test_ollama_bundled_runtime.py::test_user_ollama_env_cannot_redirect_private_instance`
- `test_windows_packaging.py::test_bytecode_written_by_host_python_is_loadable`

These failures were not repaired as part of the image optimization. The original worktree already contained unrelated API, VRAM, frontend-test and packaging edits; those were preserved.
