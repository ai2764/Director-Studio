# Autonomous Director Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Reduce repeated Director execution work while retaining autonomous planning, required validation and truthful four-stage progress.

**Architecture:** Keep existing Agent tools and publication gates. Add opt-in focused execution, dependency-aware suitability reuse, bounded telemetry and persisted progress at the existing chat boundary. Reuse current pixel observation caching and tail review.

**Tech Stack:** Python/FastAPI/Pydantic, React/TypeScript/Vitest, pytest.

**Spec:** docs/superpowers/specs/2026-11-03-autonomous-director-optimization-design.md

## Global Constraints

- Highly autonomous Director; no new manual planning or reference selection.
- Preserve global requirements, dialogue, source evidence, tail review and publication freshness.
- Latest main is baseline; preserve the original dirty checkout.
- New execution behavior is opt-in; four-stage progress reports observed events without percentages.
- Do not claim model latency/creative-quality improvements from stub tests.
- Native execution in this session follows the user's approval; do not add another implementation approval gate.

## Review Focus

- Scoped shot numbering must still resolve global instructions without losing unrelated global requirements.
- Continuation sources may be nonadjacent and must keep observed video evidence.
- Stale stream events must not overwrite a newer run after cancellation/restart.
- Cache reuse must not bypass semantic checks when action, identity or required visibility changes.
- Prompt-only completion must never imply video generation or creative QA completed.

### Task 1: Performance baseline and bounded telemetry

**Files:** backend/app/core/llm/performance.py; backend/app/agents/director/llm_plan_provider.py; backend/app/agents/director/progress.py; scripts/llm_performance_report.py; backend/tests/test_llm_performance.py.

**Interfaces:** measure_call(path, model, image_count, text_chars) async context manager; record_performance(metadata); phase_scope(name, on_progress).

- [ ] Write failure/success/cancel metadata-only logging and rotation tests; run them RED.
- [ ] Adapt earlier optimization telemetry to latest model routing. Attribute vision calls to the actual vision model. Exclude prompt/error contents. Reuse existing context usage for frontend Agent inference.
- [ ] Run telemetry and active-routing regressions GREEN.

### Task 2: Focused writer context and conservative review reuse

**Files:** backend/app/agents/director/execution_context.py; service.py; material_review.py; reference_facts.py; config.py; backend/tests/test_focused_execution.py.

**Interfaces:** focused_writer_context(context, project, shot) -> dict; review_execution_snapshot(shot) -> dict; reusable_execution_review(project, shot, records, review, revision_request) -> dict | None. Setting director_focused_execution defaults false.

- [ ] Test target/neighbor/nonadjacent source preservation, full global source retention and unknown-dependency fallback; run RED.
- [ ] Build a deterministic focused view of existing saved context. Keep full global script and directing sources until a source-linked plan proves scoped completeness; never create an unsupported semantic summary.
- [ ] Test reviewed-input snapshot reuse for an unchanged or strictly camera-speed-only edit; changed visibility/action/dialogue/references/global source and ambiguous free-text revisions must require existing review.
- [ ] Add an input snapshot to successful suitability results; reuse only when current visual/context signatures and conservative eligibility match. Existing certificates still run on publish.
- [ ] Integrate opt-in context and reuse into ordinary shot writer; keep tail path and legacy fallback intact.
- [ ] Run focused, material review, global-intent, task-context and publication regressions GREEN.

### Task 3: Durable four-stage execution state

**Files:** backend/app/agents/director/execution_progress.py; backend/app/core/projects/chat_sessions.py; backend/app/api/projects.py; backend/tests/test_director_execution_progress.py.

**Interfaces:** ExecutionProgress stores run_id, shot_id, current phase, per-stage status and timestamps; apply_event(event); finish(status). Stages plan/materials/prompt/generation.

- [ ] Test actual phase mapping, reuse, failure, cancellation, unknown events and stale-run protection RED.
- [ ] Project structured backend phase events into stage groups. Persist under the project using atomic writes and retain through chat-session lookup.
- [ ] Start at planning only for an active chat request; do not mark unvisited stages complete. Stream completion finalizes only observed work, not generation success or QA.
- [ ] Keep existing text SSE handlers compatible; add structured execution event and session field.
- [ ] Run chat lifecycle and progress tests GREEN.

### Task 4: Director four-stage panel

**Files:** frontend/src/features/director/ExecutionProgress.tsx; ExecutionProgress.test.tsx; executionProgress.css; api.ts; api.test.ts; DirectorPage.tsx; DirectorPage.test.tsx.

**Interfaces:** DirectorExecutionProgress type from the API; onExecutionProgress callback; reusable ExecutionProgress component consumes progress and clockNow.

- [ ] Test stage labels/statuses, elapsed time, reused result, prompt-only noncompletion and stream decoding RED.
- [ ] Display Plan shots / Prepare materials / Write and validate / Generate and check. Show current task and elapsed times, no percentages or speculative ETA.
- [ ] Recover progress from session polling and reject old-run updates. Keep Agent chat text/status and cancellation controls.
- [ ] Run component/API/DirectorPage tests and build GREEN.

### Task 5: End-to-end contracts and benchmark evidence

**Files:** backend/tests/test_director_execution_trajectories.py; scripts/director_execution_benchmark.py; docs/director-execution-optimization.md.

**Interfaces:** benchmark command records condition, request wall time, model calls, input size, cache/recovery events and quality verdict separately; repeated runs isolate copied project state.

- [ ] Test ordinary autonomous tool path with existing guards, cached rewrite, changed image/global requirement, nonadjacent continuation and stale publication.
- [ ] Run three deterministic architectural trials per condition with a recording provider. Label these call-count/context-size evidence, never live-model speed evidence.
- [ ] If a configured reachable model and suitable copied fixture exist, run a live before/after prompt-only benchmark; otherwise report that live latency/creative quality remain unverified. Never generate paid videos just to fill telemetry.
- [ ] Run relevant regression suites, frontend suite/build, diff checks and fresh whole-branch review. Fix Important/Critical findings and rerun affected tests.
- [ ] Commit reviewed changes; preserve worktree as the reviewable result. No merge or deployment without a user request.

## Preflight rulings

- The spec's compact project contract needs trustworthy source provenance not yet present in existing projects. Initial implementation keeps complete global source text and narrows only unrelated shot context; unsupported dependency cases fall back. Document that full-script compression remains conservative rather than promise a semantic completeness proof.
- Exact camera-speed changes may reuse a prior suitability decision; any semantic uncertainty retains review. New input snapshots are required, so legacy reviews never acquire reuse authority retroactively.
- One-call target applies only to eligible ordinary writes; tail review and unprepared autonomous planning remain explicit costs.
