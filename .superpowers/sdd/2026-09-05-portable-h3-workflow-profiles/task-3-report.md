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

## Fix round 1: Exact semantics, edge ownership, paired reachability, and depth containment

Reviewer findings reproduced with failing regression tests:

- Mappings could point application semantics at any existing input, allowing `clip`, `api_token`, `video`, or swapped dimensions to pass synthetic filling.
- A loader connected through a mapped dynamic Picture/Audio edge was subsequently rejected as unmapped, while excluding its whole node also hid additional fixed consumers.
- Seed and saver IDs were checked only against independent global candidate sets, so a seed selected from one branch could be paired with a saver on another branch.
- Excessive nesting raised `ValueError` or Python `RecursionError` out of the public inspector instead of producing the structured compatibility result expected by setup callers.

Corrections:

- Contract-v1 validation now enforces canonical H3, RandomNoise, SaveVideo, and dynamic reference input semantics before attempting a fill.
- Fixed dependency discovery now distinguishes mapped and workflow-owned consumer edges. A loader used only by a dynamic H3 reference is application-owned; the same loader is reported when any additional reachable consumer remains.
- Validation now requires the selected seed's reachable-output set to contain the selected saver.
- Nesting-limit and recursion failures are contained as `unsupported` / `invalid_structure`, and contract validation returns that fatal issue without misleading secondary mapping failures.

TDD and verification evidence:

- Corrected regression collection first produced 12 expected behavioral failures across the four reviewer findings.
- Focused inspector/validator suite: `33 passed in 0.39s`.
- Broader H3 profile/runtime suite: `82 passed in 0.81s`.
- Ruff: `All checks passed!`.
- Full backend suite: `745 passed, 2 warnings in 22.36s`.
