# Director focused execution pilot

Director still plans shots, selects materials and advances production through its
existing tools. This pilot changes ordinary prompt execution and progress reporting;
it does not switch the project to AI Assistant or change generation authorization.

## Enable and compare

Set `DS_DIRECTOR_FOCUSED_EXECUTION=true` in the backend environment and restart that
backend. The default is false. Set it back to false to compare with the existing
Director path. Keep the model, provider, project snapshot and output limit unchanged.
New four-stage progress does not require the focused execution setting.

The ordinary tool-free writer receives its full H3 task contract, applicable writing
guide and custom creative guidance, without the generic Director operating/casting
instructions. Agent planning, reference suitability review, attributed dialogue,
publication freshness and generation preflight retain their existing authority.
Tail/motion-context writing retains the existing observed-ending/reviewer path.

## Reuse and context boundaries

New suitability reviews save an execution-input snapshot. A subsequent structured,
saved camera-speed-only change can reuse suitability if reference/context signatures
are unchanged and there is no free-form revision request. Actions, staging, camera
path/visibility, dialogue, voice, duration, global sources or references changing keep
the existing review. Legacy reviews without snapshots never gain reuse retroactively.

**Ordinary chat passes the user's full request to the writer.** Any nonempty free-form
request conservatively retains suitability review. Therefore the one-call prepared-
shot result below does not establish a one-call autonomous chat camera-edit path.
It remains a future extension requiring validated structured edit provenance.

Global script and directing sources are retained. Unrelated shot summaries are narrowed
only when an existing complete dependency-scope certificate matches the exact saved
context and target. Existing planning does not yet produce that certificate, so
legacy/ordinary projects keep complete summaries, including nonadjacent authored state.
This pilot does not claim to solve semantic full-script compression.

## Four-stage progress

The Director panel groups observed execution into:

1. Plan shots
2. Prepare materials
3. Write and validate
4. Generate and check

The panel reports running, completed, reused, not requested, submitted, ready for
review, failed, cancelled, interrupted or unconfirmed work with elapsed timestamps.
Nested writer phases cannot finish the outer validation operation. Queueing a reference
or video is submission, not completion. Actual job terminal status updates persisted
progress on polling; successful generation does not claim creative-quality approval.
Prompt-only requests leave generation as not requested.

Progress is persisted per project/run, with shot identity, run sequence and revision.
Refresh reads the current receipt; old-run writes and older polling/SSE revisions
cannot overwrite newer state. This is a stage display, not an ETA or percentage.
Batch tools without an explicit successful completion receipt may display needs
checking instead of claiming all work completed.

## Evidence and limits

Run the isolated architectural comparison from the repository root:

```powershell
py -3 scripts/director_execution_benchmark.py --trials 3 --output benchmark.json
```

It uses real storage, evidence caching, writer routing and publication contracts with
simulated inference and temporary synthetic projects. It never calls a live model or
generates video. Three trials per condition on a prepared 24-shot fixture produced:

| Condition | First preparation text / vision calls | Cached structured speed-edit text / vision calls | Speed-edit total input characters |
|---|---|---|---|
| Existing Director | 2 / 1 | 2 / 0 | 52,925 |
| Focused pilot | 2 / 1 | 1 / 0 | 28,604 |

The prepared structured edit reduces input characters by about 46%. First-preparation
input characters fall from 63,936 to 58,275. Character counts include the writing guide
and configured custom guidance, so other installations can differ. These figures are
not tokens, live latency gains, autonomous-planning measurements or creative-quality
scores. Free-form chat keeps the extra suitability call described above.

## Validation record

The complete frontend suite passed (335 tests), production build passed, and the new
panel was inspected at desktop and 375px mobile widths without page errors or horizontal
overflow. Targeted backend execution/routing/contracts/tool suites passed, including
an initial 265-test cross-workflow regression run.

The full backend run reported 2,328 passed, 27 skipped and two failures. The new SSE
terminal-order regression was fixed: execution progress now precedes the terminal
result/error while preserving the retry stream's first status event. The affected
retry/progress/chat-lifecycle suite then passed all 42 tests.

The other full-run failure,
`test_llama_swap_launcher.py::test_launcher_validates_then_starts_healthy_proxy`,
also fails when running an isolated copy of the unchanged `origin/main` launcher
and test. HTTP health succeeds, but Windows `Get-NetTCPConnection` returns no binding.
This baseline failure was not changed as part of Director execution optimization;
the full backend suite is not claimed green.

No configured live model was available in the isolated checkout, and the prior
configured endpoint was unreachable during this session. The 30% live request-time
acceptance target and creative-fidelity comparison remain unverified; focused execution
is intentionally opt-in.

Metadata-only inference and phase records are written to
`<data_dir>/logs/llm-performance.jsonl`, rotating at 5 MiB. They contain durations,
phase/model attribution, input/output sizes and available token usage, without prompts,
image bodies or provider error text. Use:

```powershell
py -3 scripts/llm_performance_report.py before.jsonl after.jsonl
```

Phase totals contain model calls; do not add both as wall time. For real validation,
use isolated project copies, separate cold/warm trials and compare at least three
equivalent requests per condition. Inspect identity, action, exact speech, global
requirements and observed video handoff in the resulting prompts, alongside timing.
