# Autonomous Director: bounded execution and reusable evidence

Status: core design approved by the user; four-stage progress added on user request.
No product code changed yet.

## Intent

The user wants Director to remain highly autonomous: understand a brief, plan shots,
select or obtain references, write prompts, and advance production within the user's
existing authorization. They recall the earlier optimization remaining slow and
producing unsatisfactory results. Borrow AI Assistant's focused execution approach
without making the user manually perform Director's planning or reference selection.

The first implementation targets autonomous Film / Video Director authoring through
prompt readiness. Existing video execution and approval behavior remains the authority
for generation. Music Video and JSON Production are outside the first rollout.

## Verified starting point

- The local `大模型优化` branch ends at `bce94a8` (2026-10-21). It reduces automatic
  chat image attachments using validated observations and adds performance logging.
  Its recorded validation did not establish actual inference speed or quality gains.
- Director's writer still has material review, dialogue preparation, prompt generation
  and repair. Active motion continuation uses a separate tail writer/reviewer path.
- Task-scoped context already exists behind off/shadow/pilot configuration. Its
  writer packet still includes the full script; the ordinary fallback serializes
  Agent context. Merely enabling the existing pilot is not the proposed solution.
- AI Assistant on `origin/codex/shot-workspace` writes from one saved shot, selected
  Library descriptions, explicit dialogue and continuation settings, with one bounded
  format repair. It does not do Director's autonomous planning or visual verification.
- The current checkout contains unrelated uncommitted changes. Implementation must
  use an isolated checkout and must not move, stage or overwrite those changes.

## Chosen architecture

Keep the Director Agent as the autonomous planner and recovery decision maker.
Move already-decided work to focused backend operations. The Agent selects the work;
the backend supplies current inputs, performs the operation and returns a concise
receipt. Routine execution must not require a new user action or authoring mode.

Use the existing Director tool and managed-run entry points. Do not introduce a
second general Agent loop, replace Director with the AI Assistant UI, or merge the
AI Assistant branch wholesale. Reuse suitable contracts/helpers after inspection.

### 1. Compile current state into a bounded shot task

The writing packet includes the current shot and latest applicable amendment,
authoritative dialogue and voice bindings, selected reference descriptions and visual
evidence, camera/timing, explicit continuity dependencies and applicable project rules.
Previous or next shot summaries are supplied only for an actual narrative dependency.
An explicit nonadjacent source shot must be followed even if it is not a neighbor.
Chat transcripts, irrelevant shots and superseded amendments do not enter the writer.

Director retains access to the complete script and project while planning. A compact
project contract preserves global obligations, identities, story facts and explicit
constraints with source provenance. It is derived once per changed source version;
any model work used to derive it counts in the benchmark. Never silently truncate the
script or treat a lossy summary as complete evidence. If applicable requirements cannot
be resolved confidently, perform a bounded source lookup or use the existing complete
context path and record the reason. Long inputs must fit the actual provider budget.

Initial eligibility policy (design decision): reuse the source-linked plan and
constraints established during Director's full-context planning. The compact packet
must cover project-wide requirements, current-shot obligations and every explicit
dependency recorded by that plan. Record the exact script/brief/plan versions. A
missing plan, missing source link, unresolved dependency, changed global source or
request to reinterpret the story selects the full-context path. A generated summary
alone cannot enable the compact path. This is conservative task eligibility, not a
claim that a model can certify arbitrary screenplay summaries as semantically complete.

### 2. Separate visual evidence from creative suitability

Reuse visual observations when image content and the observation's actual source
dependencies remain valid. A new camera instruction does not by itself change what
is visible in a reference. Preserve role, Picture index, description and provenance.
Library descriptions and authored notes remain distinct from observed image facts.
Unknown facts cannot be promoted to verified observations.

Suitability depends on current shot intent, reference roles, global requirements and
continuation. Recompute only the affected decision when those change. Cache keys
include the true source dependencies and policy version. A new request for previously
unobserved detail causes targeted inspection, not blind reuse. Changed bytes or
conflicting evidence require fresh evidence before dependent publication.

Initial suitability policy (design decision): the one-call route requires an existing
current suitability decision and a change limited to a recognized camera-motion edit
with no new visibility, identity, action, staging, timing, speech, reference role or
continuation requirement. Ambiguous natural-language edits are not assumed eligible.
The focused writer receives the current amendment and must return either a candidate
or a structured evidence/conflict result within the same call; the backend still
validates all structural contracts. An action change, newly requested visual detail
or other semantic uncertainty takes the existing suitability review path. No separate
suitability LLM call is hidden in the one-call target. Broader fast-path eligibility
is a later change requiring its own quality evidence.

### 3. Make execution and recovery bounded

For a fully prepared ordinary shot, writing should use one narrow generation call,
followed by deterministic contract checks. An invalid candidate permits at most one
targeted repair within that operation. Validation covers required sections, exact
Picture/Audio bindings, explicit dialogue, timing, current source versions and existing
publication contracts. A previous valid prompt remains intact until a new candidate
passes the required checks.

Receipts distinguish complete, missing evidence, stale inputs, semantic conflict,
invalid output and transport failure. Include affected source keys, reusable completed
work and the next allowed action. Director automatically resolves fixable gaps within
its existing authority; missing IDs, retrievable evidence or ordinary stale reads must
not become questions to the user. Ask the user only for an essential creative choice
that available sources cannot resolve, or an existing authorization boundary.

Bound the whole shot operation, including Agent re-entry: one repair and one targeted
evidence recovery pass. Identical inputs plus the same unresolved failure cannot
restart generation/review. A transport failure does not trigger creative replanning.
On budget exhaustion, preserve progress and return the concrete unresolved cause.

### 4. Preserve observed continuation

Do not route motion-context or selected-tail shots through a description-only shortcut.
First reuse a current, source-job-specific ending observation when available; otherwise
obtain the required observation through the existing path. Preserve current handoff,
dialogue, freshness and publication checks. The first rollout keeps the existing tail
writer/reviewer as fallback until equivalent evidence and behavior are verified.
The autonomous end-to-end benchmark must still include continuation shots and count
their cost; optimizing ordinary shots alone is not a whole-workflow speed claim.

## Rollout and measurement

### Four-stage progress and mode-specific validation

User-requested presentation: follow AI Assistant's stage-oriented approach. Proposed
Director labels are Plan shots, Prepare materials, Write and validate, Generate and
check. These are presentation groups, not four equally timed backend operations.
The currently inspected AI Assistant branch has operation timings and prompt task
status; do not claim an exact existing four-stage component until identified.

Use authoritative backend events and persisted per-run/per-shot status. Show running,
completed, reused, not requested, failed, cancelled and waiting for a user decision
when applicable, plus elapsed time and a concise current activity. A prompt-only
request must not mark generation complete. Retry stays within the affected stage;
replacement of inputs invalidates affected completion. Reconnect/refresh must recover
the current task. Do not fabricate percentages, ETAs, elapsed duration or QC completion.
Actual video QA is shown only when that operation occurred; job success alone is not
creative-quality approval. For multi-shot production show current shot/total separately
from its current stage. Planned future shots must not read as completed work.

Both modes retain structural prompt checks, explicit dialogue and reference bindings,
input freshness, concurrency protection, and generation preflight. Director additionally
owns global story compliance, suitability of autonomously selected materials, source
evidence and observed tail continuity, and bounded recovery from its own decisions.
AI Assistant's use of saved authored descriptions must not silently replace Director's
required visual evidence. Borrow focused execution, not the other mode's authority.

Classify validations by dependency and failure consequence: deterministic correctness
checks remain on every publish; current semantic/visual certificates may be reused;
semantic conflicts require targeted evidence/review; stylistic advice is not a new
hard blocker. Keep hard semantic blockers (missing required character, incompatible
reference, unsupported continuation or source dialogue conflict). Failed checks must
state which current input or output violated the contract, and supply a bounded
recovery route instead of reopening unrelated planning.

### Refreshed repository state

Remote refs refreshed during the approved planning phase: `origin/main` is `3b689755`
and already includes AI Assistant plus Director commit `79de2ab` (vision routing and
visual facts caching). The older `bce94a8` checkout is an historical baseline, not
the latest Director implementation. Before implementing cache/context changes, compare
latest Director behavior with the older optimization and preserve the new routing.
Use latest main for the new isolated implementation; port only necessary earlier
telemetry after checking for equivalent existing metrics. Benchmark latest Director
as the primary baseline; report historical comparisons separately if useful.

Implementation begins by recording a repeatable baseline and verifying the actual
active Director path. Use an opt-in Director execution setting; retain the old path
for comparison and rollback. Resolve the implementation base after refreshing remote
state, keeping original optimization history and unrelated local edits intact.

Compare the existing optimized Director with the proposed path using the same model,
endpoint, project copy, request, output budget and warm/cold conditions. AI Assistant
is an optional execution reference for an identically prepared shot, not a like-for-like
autonomy baseline. Do not mix project state between runs. Run at least three equivalent
trials per primary condition; report samples and medians, with cold results separated.

Record end-to-end user request to prompt readiness, planning time, each model call,
input/output sizes, attached/reused images, cache miss reasons, source retrieval,
repair/fallback counts and model/GPU waiting where observable. Existing phase logs
overlap contained calls and must not be added together as total wall time.

Required scenarios:

1. Autonomous creation from a brief, including reference selection and first-time reads.
2. Rewrite only camera movement with unchanged references.
3. Change the action or dialogue; verify current request wins without unrelated drift.
4. Replace an image or its description; verify affected evidence is invalidated.
5. Continue a finished source shot, including a nonadjacent source and a changed source job.
6. A global story constraint and an instruction outside adjacent-shot context.
7. Missing visual detail, ambiguous reference and concurrent edits while writing.
8. Invalid model output and repeated failure without an unbounded recovery loop.

The autonomous creation case must complete routine planning/selection/execution without
additional manual preparation. On valid cached ordinary shots, the target is one writer
call plus local validation, no image observation call and no repeated Agent execution
loop. For the camera-only scenario, target at least 30% lower median request-to-ready
time, reported with actual call counts. This is an acceptance target, not a promised gain.
Do not activate by default if critical constraints regress or measured speed fails to
improve. If the target is missed, report the dominant measured remaining cost before
expanding implementation scope.

Quality checks require no identity/reference swaps, lost or invented dialogue,
violated global requirements, unsupported tail facts or stale publication. Assess
creative fidelity against the user request with readable before/after artifacts;
passing a JSON schema alone is not evidence of creative quality. A live endpoint
failure leaves performance and creative-quality validation explicitly incomplete.

## Implementation choices left to the builder

Choose minimal module extraction, cache persistence details and synthetic fixtures
consistent with existing repository contracts. Reuse current telemetry and receipts
where suitable. Do not change models, providers, generation settings, frontend flows
or existing user approval policy as a means of producing a faster benchmark.

## Next step

After the user reviews this design, produce an implementation plan and implement in
an isolated checkout, beginning with the baseline and targeted contract tests.
