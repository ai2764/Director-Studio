# Task-context pilot verification — 2026-09-26

## Scope and status

P0/P1A only: request-shape measurement, versioned task evidence, read-only lookup,
Legacy/Harness context presentation, and shared normal/tail writer inputs. One
agent retains creative decisions. No new character/wardrobe/word keyword rules,
database migration, managed-run state redesign, service restart or live film edits.

Branch: `feature/mv-mode`. Implementation base: `06190fc`.
Commits: `1faf1ad` metrics; `d46d02e` snapshot/builder; `ef7d7f3` read-only lookup;
`676d23e` runtimes; `ee40655` writers; `c6ab049` trajectories/report. Final review
additions, if any, follow it. Default remains `off`, project allowlist `[]`.

Real-model comparison: **NOT RUN**. No sample project/cost range was authorized for
this pilot. Passing fixture tests does not establish fewer semantic errors, better
filmmaking, tokenizer savings, or lower end-to-end latency. P1B is not started;
live pilot evaluation and review of its findings precede broader rollout.

## Verification evidence

Python: 3.12.7; backend commands run from `backend`, frontend from `frontend`.
All fixtures isolate project/Library/Job storage; model inference is scripted.

| Check | Result |
| --- | --- |
| Pre-change backend suite | 1539 passed, 13 skipped |
| Task 5 integrated writer/context regression | 148 passed, exit 0 |
| Task 5 named writer regression | 111 passed, exit 0 |
| Task 5 full `python -m pytest -q` | 1601 passed, 13 skipped, exit 0 |
| Pilot-specific suite | 74 passed, exit 0 |
| Full backend suite at `c6ab049` | 1613 passed, 13 skipped, exit 0 (372.40s) |
| Review fix, Legacy/Harness stale-read trajectories | 2 failed → 2 passed (22.07s) |
| Full backend suite after review fix | 1615 passed, 13 skipped, exit 0 (365.42s) |
| `npm test` | 33 files, 258 tests passed, exit 0 |
| `npm run build` | TypeScript/Vite build passed, exit 0 |
| `git diff --check` | Passed, exit 0 |

Backend suites retain two pre-existing Pillow `getdata` deprecation warnings in
`test_tail_frame_extraction`. Intermediate trajectory failures were fixture errors:
missing legacy context setup, compressed solid image rejected by the existing
2-KB placeholder rule, and a pure-builder size threshold incorrectly applied to
the runtime's authority schemas. Corrected tests compare growth from 2 to 502 shots
and use valid non-placeholder image files. No product rule was changed for them.
An overlapping full run had already collected the old image fixture and ended
with `test_cached_wardrobe_does_not_replace_current_reference` failing (1612
passed, 13 skipped); the fresh full run above includes the corrected fixture and
passes. Final pilot-suite repetition: 74 passed (101.54s).

Reproduce pilot suite:

```powershell
python -m pytest tests/test_task_context_metrics.py tests/test_task_context_builder.py tests/test_task_context_query.py tests/test_task_context_runtime.py tests/test_task_context_writers.py tests/test_task_context_trajectories.py -q
python -m pytest -q
```

## Multi-step matrix and evidence coverage

| Sequence | Observable assertion |
| --- | --- |
| Recover dialogue → camera edit → rewrite | Same user-message source and speakers; one extraction; no authored-field rewrite |
| Old wardrobe summary + current ref | Old cached description absent; current metadata and actual selected-file hash present |
| 500 irrelevant shots + long history | Target packet does not acquire unrelated beat details; history still counted in final request |
| Read ref → UI replaces bytes → writer | Old packet/save check and new writer preflight reject stale read; new bytes preserved |
| Wrong shot focus → read → correct focus | Original objective and all authored files preserved; 1 read and 1 switch |
| Prompt-only tool scope → overview → shot → overview | Exact authorized shot schema retained; no revise/start authority; 3 switches |
| Two camera/performance variants | Both pass existing structural publication contracts; no golden wording match |
| Off → shadow → pilot outside allowlist → pilot inside | One model call each; first three requests identical; no business writes |
| Cancel Legacy/Harness chat | Scope resets; already submitted independent Job unchanged |
| Foreign Job linked from local Shot | Project-scoped lookup rejects the foreign Job |
| Missing ref → repeated write → restore → read → write | No model/candidate before evidence; restore alone insufficient; 1 successful generation after 1 source read; saved replay adds no call |
| Scoped retry → context-capacity gap | Original retry receipt stays pending; no replacement generation-failure receipt |
| Read → UI replaces ref → write gap → refresh → write (both runtimes) | Nonterminal preflight; no candidate/retry-failure receipt before refresh; one generation after refresh; saved replay adds no call |

Required fields tested: full script ending and global directing requirements;
target authored beat/camera/dialogue; source-bound speaker IDs/languages; existing
prompt and revision history; adjacent summaries explicitly not dependency edges;
selected Picture numbering and actual byte hashes; every selected image still
visually inspected (9 in the normal fixture, 1 in tail); Voice speaker/Audio index
and file hash; source-audio hash; tail source Shot, exact Job and generation;
authorized tool schemas and unchanged global tool budget.

Both runtime switch/read/status/final trajectories use 4 model calls, 1 switch,
1 source read and 1 status read. Context tools add no separate inference loop.
Creative-variant tests script semantic audit approval: they prove the framework
does not enforce a single phrasing, not that a live model will always judge well.

## Actual final-envelope character measurements

`test_long_history_final_envelope_is_measured_honestly` captures actual provider
requests through the Harness backend and API adapter. Fixture: 502 shots, 20
history messages plus the current user message, no images; same inputs/config.
Numbers are characters, **not tokens**. Dynamic IDs have the same length.

| Mode | System excluding state | Task/state | Final system including separator | History + current user | Tool schemas | Images |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| off | 19164 | 81486 | 100667 | 20029 | 33630 | 0 |
| shadow | 19164 | 81486 | 100667 | 20029 | 33630 | 0 |
| pilot, outside allowlist | 19164 | 81486 | 100667 | 20029 | 33630 | 0 |
| pilot, allowed shot_prompt | 19164 | 28720 | 47901 | 20029 | 2905 | 0 |

The runtime packet still duplicates original authority schemas; normal/tail writer
templates also retain duplicate fields. These are observed costs, not hidden
savings. This large fixture is not representative of all projects. Metrics tests
also exercise normal, image and bounded writer provider boundaries, verify loaded
guides are counted and image counts are 0/1/0, and prohibit raw content logging.

## Engineering decisions and limits

1. Rebase only the writer's own revision-request bookkeeping after its authorized
   save; reject changes to every other pinned field. Existing tail behavior writes
   that request before drafting. Risk if wrong: accepting stale input.
2. Keep writer preflight a conservative character estimate with template and guide
   reserves. Visual-review prose is not known before inspection. Risk if wrong:
   over/under-reservation; live capacity validation remains required.
3. Accept the review's live-quality/capacity/latency exclusion because no live
   evaluation was authorized: retain default off and make no production-quality
   claim. Risk if wrong: rollout before evaluation could expose unmeasured errors.
4. Accept the review's crash-atomic persistence/operation-identity exclusion because
   it belongs to P1B+; retain existing save semantics without claiming transaction
   guarantees. Risk if wrong: existing crash/race windows remain possible.

Current snapshots repeatedly read all project sources and decode selected images
for consistency. No I/O/latency improvement is claimed. Full-history compaction,
durable operation IDs, crash-atomic saves, state-machine unification, and broad
database/storage refactors remain outside this pilot. The existing save boundary
is retained with extra source checks, not replaced by a transactional database.

## Rollout / rollback

Only after user-approved project and cost scope: same model/sampling, same minimal
input per mode, at least 3 repeats each; record successes, unjustified refusals,
omissions, recovery, calls and actual usage. Three repeats are exploratory, not
statistical proof. Any critical omission or stale submission blocks expansion.
Use `shadow` for observation first, then allowlisted `pilot`. Set mode `off` to
return subsequent turns to legacy context; no data migration must be reversed.

## Final independent review

Fresh read-only reviewer inspected `06190fc..c6ab049`. No Critical findings; one
Important finding and one Minor. Its pre-fix verdict was **With fixes**.

Important fixed: a stale queried source detected before any writer candidate had
been misclassified as terminal generation failure. The preflight now carries an
explicit `CONTEXT_SOURCE_CHANGED` gap inside `CONTEXT_REQUIRED`, with old/current
versions and a first-page refresh handle. Only refresh can unlock one attempt;
an already-started writer cannot be downgraded into a retryable preflight. Changes
discovered during generation still fail the existing save guard. Both runtime
trajectories reproduced the failure before the fix and passed afterward.

Deferred minor: `context_metrics.request_shape` counts string message content and
tool schemas, but not assistant native tool-call arguments. Its digest also omits
roles/tool calls. This may under-report request growth in multi-tool trajectories.
The final-envelope table above has no tool-call arguments and remains accurate for
that fixture. Do not interpret these metrics as an exhaustive transport-size or
token measurement. Address argument accounting in a later focused metrics change.

Reviewer explicitly declined live semantic/capacity/latency outcomes and
crash-atomic/durable-operation redesign; engineering decisions 3–4 record the
disposition and residual risks. One TDD fix pass, no second reviewer pass. Final
suite evidence above is the executor's verification of the fix, not a new reviewer
approval or a live-model quality claim.
