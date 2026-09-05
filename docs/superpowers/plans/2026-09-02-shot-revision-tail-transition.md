# Shot Revision and Tail-Frame Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent one-Shot revisions from resetting an entire storyboard and prevent accepted tail-frame Layouts from being neutralized into style-only H3 prompts.

**Architecture:** Add a narrow single-Shot mutation contract to the existing Director service and tool executor. Extend the shared Layout prompt context and H3 validation layer with a clip-tail transition contract, then reuse those exact implementations from Harness through its existing FastAPI-owned tool bridge.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, pytest, existing Director native-tool executor, existing Harness state-version bridge.

**Spec:** `docs/superpowers/specs/2026-09-02-shot-revision-tail-transition-design.md`

## Global Constraints

- Pure H3 Ref2VA Pictures condition the whole clip; never claim a tail-frame Picture activates only at the opening.
- Do not promise pixel-identical first-frame continuity.
- Preserve all unrelated Shots and preserve the revised Shot's refs, voice refs, and Layout refs.
- Never delete historical Job files; only unlink a superseded `h3_job_id` from the revised Shot.
- Implement and verify Legacy first, then cherry-pick shared code into Harness and add Harness-only changes.

---

### Task 1: Clip-tail prompt context and deterministic validation

**Files:**
- Modify: `backend/app/core/projects/layouts.py:368-409`
- Modify: `backend/app/core/h3/prompt.py:1-240`
- Modify: `backend/app/agents/director/prompts.py:140-171`
- Modify: `backend/app/agents/director/service.py:1708-1795`
- Test: `backend/tests/test_h3_layout_pack.py`
- Test: `backend/tests/test_h3_prompt.py`

**Interfaces:**
- Produces: `selected_layout_prompt_context(shot) -> list[dict[str, Any]]` entries containing `origin_kind` and `visible_transition_required`.
- Produces: `validate_tail_frame_transition_prompt(sections: PromptSections, selected_layouts: Iterable[dict[str, Any]]) -> None`.
- Consumes: selected Layout context already built by `DirectorService.write_prompts_after_layout()`.

- [ ] **Step 1: Write failing context and validator tests**

Add tests that construct a selected `LayoutReference` with `ClipTailFrameOrigin` and assert:

```python
context = selected_layout_prompt_context(shot)
assert context[0]["origin_kind"] == "clip_tail_frame"
assert context[0]["visible_transition_required"] is True
```

Add parameterized prompt tests proving a first `0–0.8 seconds` paragraph with `continues ... dissolves ... revealing` passes, while `hard cut`, `palette only`, `style only`, `must not manifest`, a first interval beginning after zero, and a direct destination opening without a transition verb fail with `tail-frame transition` in the message.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
cd backend
pytest tests/test_h3_layout_pack.py tests/test_h3_prompt.py -q
```

Expected: failures because the context keys and validator do not exist.

- [ ] **Step 3: Implement the minimal shared contract**

In `layouts.py`, add origin fields to prompt context and include them in `layout_prompt_signature()`.

In `prompt.py`, add:

```python
def validate_tail_frame_transition_prompt(
    sections: PromptSections,
    selected_layouts: Iterable[dict[str, Any]],
) -> None:
    ...
```

Inspect only the first timed action interval in `sections.detailed_description`, require a zero start plus a transition verb, and reject the explicit negating phrases listed in the spec. Do not weaken `validate_no_time_addressable_pictures()`.

Update `H3_PROMPT_INSTRUCTIONS` to explain `origin_kind="clip_tail_frame"` and visible handoff. Call the new validator inside `parse_and_validate()` after the existing Picture-timing validator so the existing one-repair flow handles failures.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same two test files and expect all tests to pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/core/projects/layouts.py backend/app/core/h3/prompt.py backend/app/agents/director/prompts.py backend/app/agents/director/service.py backend/tests/test_h3_layout_pack.py backend/tests/test_h3_prompt.py
git commit -m "fix: enforce visible tail-frame transitions"
```

### Task 2: Native single-Shot revision service and Legacy tool

**Files:**
- Modify: `backend/app/agents/director/planner.py:87-140`
- Modify: `backend/app/agents/director/service.py:284-500`
- Modify: `backend/app/agents/director/tool_schema.py:240-278`
- Modify: `backend/app/agents/director/tool_handlers/project.py:8-133`
- Modify: `backend/app/agents/director/chat_orchestrator.py:321-367`
- Test: `backend/tests/test_director_agent.py`
- Test: `backend/tests/test_director_native_tools.py`
- Test: `backend/tests/test_director_chat_intent.py`

**Interfaces:**
- Produces: `ShotRevisionSubmission` with `shot_id` and at least one optional authored Shot field.
- Produces: `DirectorService.revise_shot(project_id: str, revision: ShotRevisionSubmission) -> list[Shot]`.
- Produces: native tool `revise_shot` using the shared service and returning the refreshed storyboard snapshot.

- [ ] **Step 1: Write failing model and service tests**

Test that empty and unknown-field revisions fail. Seed three Shots with Layouts, prompts, voices, and H3 IDs; revise only Shot 2's `script_beat`, `shot_type`, `camera_motion`, and `composition`; assert Shot 1 and Shot 3 model dumps are unchanged, Shot 2 retains refs/voices/layouts, Shot 2 prompt becomes empty, its old H3 ID is preserved in metadata, `h3_job_id` becomes `None`, and status becomes `needs_review`.

- [ ] **Step 2: Run service tests and verify RED**

Run:

```powershell
cd backend
pytest tests/test_director_agent.py -q -k "revise_shot"
```

Expected: collection or assertion failure because the model/service do not exist.

- [ ] **Step 3: Implement the revision model and service**

Define a Pydantic model with `extra="forbid"`, a required `shot_id`, optional authored fields, and a model validator requiring at least one supplied update. Implement `revise_shot()` with explicit `model_fields_set` updates, targeted invalidation, superseded Job audit metadata, one `save_shot()` call, refreshed agent context, and no call to `replace_project_shots()`.

- [ ] **Step 4: Run service tests and verify GREEN**

Run the focused service tests and expect them to pass.

- [ ] **Step 5: Write failing native-tool and guidance tests**

Assert `revise_shot` exposes only the partial authored shape; tool execution returns `actions == ["revise_shot"]`; all unrelated snapshots are unchanged; and `DIRECTOR_CHAT_SYSTEM` says exactly-one-Shot authored changes use `revise_shot`, followed by `write_prompt` when requested.

- [ ] **Step 6: Run native tests and verify RED**

Run:

```powershell
cd backend
pytest tests/test_director_native_tools.py tests/test_director_chat_intent.py -q -k "revise_shot or tool_surface"
```

Expected: failures because the schema, handler, and guidance are absent.

- [ ] **Step 7: Implement schema, handler, and Legacy routing guidance**

Add the function schema beside `patch_shot_refs`, route it through `handle_project_tool()`, return the normal storyboard snapshot, and update system guidance so full `save_storyboard` is reserved for multi-Shot/count/order changes.

- [ ] **Step 8: Run native tests and verify GREEN**

Run the focused native tests and expect them to pass.

- [ ] **Step 9: Run the Legacy regression suite**

```powershell
cd backend
pytest tests/test_h3_prompt.py tests/test_h3_layout_pack.py tests/test_tail_frame_acceptance.py tests/test_director_agent.py tests/test_director_native_tools.py tests/test_director_chat_intent.py -q
```

Expected: zero failures.

- [ ] **Step 10: Commit Task 2**

```powershell
git add backend/app/agents/director/planner.py backend/app/agents/director/service.py backend/app/agents/director/tool_schema.py backend/app/agents/director/tool_handlers/project.py backend/app/agents/director/chat_orchestrator.py backend/tests/test_director_agent.py backend/tests/test_director_native_tools.py backend/tests/test_director_chat_intent.py
git commit -m "feat: revise one shot without resetting storyboard"
```

### Task 3: Harness tool exposure and state-version coverage

**Files:**
- Modify in Harness worktree: `backend/app/agents/director/harness_tools.py:19-25`
- Modify in Harness worktree: `backend/tests/test_harness_tools.py`
- Modify in Harness worktree: `backend/tests/test_harness_vertical_slice.py`

**Interfaces:**
- Consumes: shared `revise_shot` and `write_prompt` native schemas/executor from Tasks 1–2.
- Produces: Harness allowlist entries for `revise_shot` and `write_prompt`, both included in `MUTATING_TOOL_NAMES`.

- [ ] **Step 1: Cherry-pick the two Legacy commits into Harness**

From the `deepseek-harness-phase1` worktree, cherry-pick the Task 1 and Task 2 commit hashes in order. Resolve no generated data or user files.

- [ ] **Step 2: Write failing Harness allowlist and mutation tests**

Assert an existing planned project offers both new tools, a valid `revise_shot` call changes only the target Shot and returns a new state version, and stale `revise_shot`/`write_prompt` calls raise `HarnessStateConflict` before execution.

- [ ] **Step 3: Run Harness tests and verify RED**

```powershell
cd backend
pytest tests/test_harness_tools.py tests/test_harness_vertical_slice.py -q
```

Expected: failures because the allowlist omits the new tools.

- [ ] **Step 4: Extend the Harness allowlist**

Add `revise_shot` and `write_prompt` to `PHASE1_TOOL_NAMES`; retain only `get_status` as read-only so both new tools receive state-version checks.

- [ ] **Step 5: Run Harness tests and verify GREEN**

Run the same Harness tests and expect zero failures.

- [ ] **Step 6: Run combined regression in Harness**

```powershell
cd backend
pytest tests/test_h3_prompt.py tests/test_h3_layout_pack.py tests/test_tail_frame_acceptance.py tests/test_director_agent.py tests/test_director_native_tools.py tests/test_director_chat_intent.py tests/test_harness_tools.py tests/test_harness_vertical_slice.py tests/test_project_harness_runtime.py -q
```

Expected: zero failures.

- [ ] **Step 7: Commit Harness-only integration**

```powershell
git add backend/app/agents/director/harness_tools.py backend/tests/test_harness_tools.py backend/tests/test_harness_vertical_slice.py
git commit -m "feat: expose safe shot revision through harness"
```

### Task 4: Final verification and handoff

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Consumes: Legacy and Harness commits from Tasks 1–3.
- Produces: verified branch hashes and a concise migration note.

- [ ] **Step 1: Verify both worktrees are clean except known user-owned untracked directories**

Run `git status --short` in both roots and inspect every entry.

- [ ] **Step 2: Run a focused behavior probe on Legacy**

Create an isolated test project through existing test fixtures or a one-off temporary test directory; prove `revise_shot` preserves neighboring Shot dumps and rejects a style-only tail transition prompt.

- [ ] **Step 3: Run a focused Harness behavior probe**

Exercise `get_harness_context()` and confirm `revise_shot`/`write_prompt` are offered with the current state version, then execute a stale mutation and confirm structured conflict behavior.

- [ ] **Step 4: Report exact commits and test counts**

Report Legacy commit hashes, Harness commit hashes, commands run, pass/fail counts, and any files deliberately left uncommitted. Do not merge branches unless the user explicitly requests it.

