# Task 3 Report: Deterministic H3 Workflow Inspection and Validation

## Status

Implemented and verified.

## Delivered behavior

- Added bounded H3 API-workflow inspection for parsed mappings and raw JSON bytes/text.
- Enforced the 8 MiB file, 2,000 node, 10,000 edge, depth 32, and 64 KiB string limits before compatibility analysis.
- Built deterministic source-to-consumer graph reachability from Comfy `[node_id, output_index]` links.
- Required exactly one `MiniMaxH3ReferenceToVideo`, rejected I2V/`ref_frame`/`last_frame` semantics, and checked the exact H3 boundary inputs.
- Limited `RandomNoise` and `SaveVideo` candidates to nodes converging on H3-reachable final outputs and exposing their exact boundary inputs.
- Returned `auto_compatible`, `needs_confirmation`, or `unsupported` with structured candidates and issues.
- Emitted a stable compact manifest containing only node IDs, class types, sanitized titles, input names, output reachability, candidate roles, and scalar/redacted defaults.
- Redacted secret-like keys, absolute path-shaped strings, and file-loader values from the agent manifest.
- Reported reachable workflow-owned `LoadImage`/`LoadAudio` values as fixed dependencies while excluding media already connected through mapped dynamic H3 Picture/Audio sockets.
- Added exact contract validation that exercises `fill_profile_graph()` with one Picture, zero Audio, 864x480, 56 frames, and seed 42.
- Rechecked all synthetic graph links and every mapped node/input after filling.
- Preserved the store's minimal pure-Ref2AV defense. The only store change defers its job-store import until snapshot methods execute, removing a package initialization cycle exposed by direct inspector imports.

## TDD evidence

Red phases:

- Initial focused collection failed because `inspector.py` and `validator.py` did not exist.
- The mapped-dynamic-Picture test failed because the loader was incorrectly reported as a fixed dependency.
- Candidate-role and exact candidate-input tests failed because H3 had no manifest role and class-only candidates were accepted without `noise_seed`/`filename_prefix`.
- Broader H3 collection reproduced an eager import cycle through `h3.store -> core.jobs.__init__ -> h3 pipeline -> h3 package`.

Green phases:

- Focused inspector/validator suite: `22 passed in 0.38s`.
- Broader H3 profile/runtime suite after the import-cycle correction: `69 passed in 0.92s`.
- Ruff: `All checks passed!`.
- Full backend suite: `734 passed, 2 warnings in 23.60s`.

## Self-review

- Confirmed the bundled official graph is `auto_compatible`, produces the official mapping (`136`, `129`, `92`), and passes synthetic contract validation.
- Confirmed disconnected seed/saver decoys are not candidates and multiple reachable savers require confirmation.
- Confirmed manifest ordering is independent of input dictionary order and sensitive values do not survive serialization.
- Confirmed invalid mapped input names, disconnected mappings, dangling links, malformed reachable file loaders, and forbidden semantics return structured validation failures.
- Confirmed `git diff --check` has no whitespace errors apart from repository line-ending notices.

## Known concerns

- The full backend suite retains two existing Pillow `Image.getdata` deprecation warnings in `test_tail_frame_extraction.py`; they are unrelated to Task 3.
- Fixed workflow file dependencies are intentionally reported with their local relative value in the analysis record for explicit UI disclosure; their values are redacted from `agent_manifest`, which is the only payload intended for Ollama.
