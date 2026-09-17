# Harness Reliability and Context Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the observed Director turn/state regressions and replace the always-expanded context diagnostics with a compact, responsive dialog.

**Architecture:** Keep creative decisions in the model, but make state mutation boundaries and tool outcomes unambiguous. Reuse durable project review notes as bounded decision context for per-shot visual review. Keep token telemetry unchanged; move its presentation and manual compaction control behind one accessible dialog.

**Tech Stack:** Python 3.13, FastAPI/Pydantic, pytest, React 19, TypeScript, Vitest, CSS.

**Spec:** `.run/agent-context-boundary-evaluation.md` (local evidence and accepted requirements)

## Global Constraints

- Do not hard-code creative content such as time of day, wardrobe, or scene choices.
- A sparse premise may be expanded creatively, but model-authored expansion is a draft until the user confirms it.
- A successful `write_prompt` may mutate a Shot at most once per Harness turn.
- Layout remains optional; ordinary missing Picture bindings must never be reported as missing Layouts.
- Existing telemetry payloads and manual-compaction API remain backward compatible.
- The compact Context trigger must not cover or resize the chat composer on 320–430px mobile widths.
- Do not push this branch.

---

### Task 1: Protect script provenance in Director guidance

**Files:**
- Modify: `backend/app/agents/director/chat_orchestrator.py`
- Test: `backend/tests/test_director_chat_intent.py`

**Interfaces:**
- Consumes: `DIRECTOR_CHAT_SYSTEM` guidance supplied to every native Harness turn.
- Produces: explicit distinction between a user-supplied script/change and a model-authored draft.

- [ ] Add a test asserting the system contract says a premise/one-line brief must be proposed as a draft and must not call `set_script` until the user explicitly adopts it.
- [ ] Run the focused test and confirm it fails because the contract is absent.
- [ ] Add the minimal guidance beside the existing `set_script` pipeline rule; preserve model freedom to draft content in natural language.
- [ ] Run the focused test and the Director intent suite.

### Task 2: Make prompt completion terminal within one Harness turn

**Files:**
- Modify: `backend/app/agents/director/harness_runtime.py`
- Modify: `backend/app/agents/director/tool_handlers/layout.py`
- Test: `backend/tests/test_harness_runtime.py`
- Test: `backend/tests/test_director_native_tools.py`

**Interfaces:**
- Produces: turn-local `successful_prompt_shot_ids: set[str]` and an idempotent success result for duplicate `write_prompt` calls.

- [ ] Add a regression test whose model calls `write_prompt` twice across separate Harness steps after the first succeeds; assert the service runs once and the second result says the prompt is already saved.
- [ ] Confirm the test fails because the project version currently permits the second mutation.
- [ ] Record the shot id after a successful prompt write and short-circuit later same-turn calls before the service closes the review gate again.
- [ ] Change successful tool notes from future-tense “must be updated” language to completed-state copy while retaining the review reason in structured data.
- [ ] Run both focused suites.

### Task 3: Distinguish required Pictures from selected Layouts

**Files:**
- Modify: `backend/app/core/h3/prompt.py`
- Modify: `backend/app/agents/director/service.py`
- Test: `backend/tests/test_h3_prompt.py`
- Test: `backend/tests/test_director_agent.py`

**Interfaces:**
- `validate_required_picture_bindings(..., binding_label: str = "required Picture")` reports the caller-provided binding type.

- [ ] Add one test for a missing ordinary Picture and one for a missing selected Layout, asserting different error text.
- [ ] Confirm the ordinary-Picture test fails with the current false Layout wording.
- [ ] Add the label parameter and pass `selected Layout` only from the Layout-specific validation call.
- [ ] Run prompt and Director prompt-writing tests.

### Task 4: Carry durable user decisions into material review

**Files:**
- Modify: `backend/app/agents/director/material_review.py`
- Test: `backend/tests/test_director_material_review.py`

**Interfaces:**
- Consumes: `Project.asset_coverage_review`, Shot feedback, current material delta, script, brief and visual observations.
- Produces: a bounded `confirmed_project_review` field in the review-model request.

- [ ] Add a test that records “references are authoritative / use the bright coastal scene” in the current coverage review and captures the material-review request.
- [ ] Confirm the request currently omits the durable decision.
- [ ] Include only the current script-hash coverage review status, notes and resolved recommendations; instruct the reviewer not to reopen recorded choices unless the newly changed Picture creates a new concrete conflict.
- [ ] Run the material-review suite.

### Task 5: Replace inline telemetry with a responsive Context dialog

**Files:**
- Modify: `frontend/src/features/director/ContextUsage.tsx`
- Modify: `frontend/src/features/director/ContextCompaction.tsx`
- Modify: `frontend/src/features/director/contextUsage.css`
- Modify: `frontend/src/features/director/DirectorPage.tsx`
- Test: `frontend/src/features/director/ContextUsage.test.tsx`
- Test: `frontend/src/features/director/ContextCompaction.test.tsx`
- Test: `frontend/src/features/director/DirectorPage.test.tsx`

**Interfaces:**
- `ContextUsagePanel({ calls, children })` renders a one-line trigger and a modal dialog containing telemetry plus compaction actions.
- `ContextCompaction` remains the sole owner of runtime availability and POST `/chat/compact` state.

- [ ] Add tests asserting details and `Compact context` are absent while closed, appear after opening, close with Escape, and keep compaction results inside the dialog.
- [ ] Confirm the tests fail against the current `<details>` layout.
- [ ] Implement the trigger/dialog behavior with `role="dialog"`, labelled title, close button, Escape handling, backdrop click, initial close-button focus and focus return.
- [ ] Move `ContextCompaction` into the dialog action area; use clear idle/running/success/error copy and prevent repeated execution while busy.
- [ ] Add responsive CSS: fixed overlay, desktop max width/height, mobile bottom sheet at `max-width: 640px`, safe-area padding, internal scrolling, single-line summary truncation and no composer overlap.
- [ ] Run focused frontend tests and production build.

### Task 6: Integrated verification

**Files:**
- Verify only.

- [ ] Run focused backend suites for Harness runtime, native tools, material review and H3 prompt validation.
- [ ] Run the complete backend test suite with `DS_DIRECTOR_NUM_CTX=32768` so local 131K runtime configuration does not alter default-contract assertions.
- [ ] Run the complete frontend Vitest suite and `npm run build`.
- [ ] Inspect the dialog at desktop and mobile width against the accepted screenshot requirements.
- [ ] Run `git diff --check`, review `git status`, and confirm no project data, model configuration or secrets are staged.
