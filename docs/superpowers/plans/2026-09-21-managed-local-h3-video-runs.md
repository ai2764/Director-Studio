# Managed Local H3 Video Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user approve one local-H3 resolution and one Shot-brief-grounded plan, then toggle Agent-managed sequential video generation on and off.

**Architecture:** A persisted run record owns the chosen resolution, ordered Shot steps, current Job, and event cursor. One new Agent tool starts a local H3 Job through the existing submission service; a terminal Job event schedules a fresh, compact Agent turn instead of blocking or polling within a Harness turn. The Production UI owns plan review and Start/Stop, while the backend gates every mutation against active run state.

**Tech Stack:** FastAPI, Pydantic, JSON project storage, pytest, React 19, TypeScript, Vitest, local ComfyUI H3.

**Spec:** `docs/superpowers/specs/2026-09-21-managed-local-h3-video-runs-design.md`

## Global Constraints

- Local ComfyUI only; MiniMax API remains manual.
- Agent gets one new callable tool: `start_h3_video(shot_id)`; no poll, callback, plan-save, or stop tool.
- Managed runs require one explicit landscape/portrait 480, 720, 768, or 1080 preset. Local 768 is 1376×768; local 1080 is 1920×1088; portrait swaps dimensions.
- No video visual QC, automatic retry, resolution fallback, unplanned Layout generation, or silent plan revision.
- A planned tail frame may be selected directly, but provenance must identify managed-run preauthorization rather than human visual review.
- Stopping must prevent a late LLM result or duplicate terminal event from submitting another Job.
- Preserve the six pre-existing uncommitted Agent-loop files; stage only feature hunks when committing.

---

### Task 1: Shared local resolution contract

**Files:**
- Create: `backend/app/pipelines/h3_ref2va/resolutions.py`
- Modify: `backend/app/pipelines/h3_ref2va/router.py`
- Modify: `frontend/src/features/production/api.ts`
- Modify: `frontend/src/features/production/ProductionPage.tsx`
- Test: `backend/tests/test_h3_resolutions.py`
- Test: `frontend/src/features/production/ProductionPage.test.tsx`

**Interfaces:**
- Produces `LOCAL_H3_PRESETS: dict[str, tuple[int, int]]`, `resolve_local_resolution(preset: str) -> tuple[int, int]`, and `GET /api/h3-ref2va/resolutions` returning `{presets:[{id,label,width,height}]}`.
- Later tasks bind the selected preset ID to a run, never model-supplied width/height.

- [ ] **Step 1: Write failing backend and frontend tests.** Check that `landscape-768` resolves to `(1376,768)`, `portrait-1080` to `(1088,1920)`, an unknown preset raises `ValueError`, and both desktop/mobile Production pickers display the returned dimensions. The backend test should call the real resolver and route; frontend tests may mock the HTTP boundary but must assert actual rendered options and submitted dimensions.

```python
def test_local_768_and_1080_presets_are_exact():
    assert resolve_local_resolution("landscape-768") == (1376, 768)
    assert resolve_local_resolution("portrait-1080") == (1088, 1920)
```
- [ ] **Step 2: Run RED.** `cd backend; python -m pytest -q tests/test_h3_resolutions.py` and `cd frontend; npm test -- --run src/features/production/ProductionPage.test.tsx`. Expected: missing resolver or missing options.
- [ ] **Step 3: Implement the preset source and UI consumption.** Use literal pairs `864×480`, `1280×704`, `1376×768`, `1920×1088` and swaps. Return the same list from the backend route; replace the frontend's hard-coded four-option map with fetched local presets. Keep manual Auto and do not imply the local picker controls MiniMax's 768P/2K setting.

```python
LOCAL_H3_PRESETS = {
    "landscape-480": (864, 480), "landscape-720": (1280, 704),
    "landscape-768": (1376, 768), "landscape-1080": (1920, 1088),
    "portrait-480": (480, 864), "portrait-720": (704, 1280),
    "portrait-768": (768, 1376), "portrait-1080": (1088, 1920),
}
```
- [ ] **Step 4: Run GREEN and build.** Run the two test commands above and `cd frontend; npm run build`.
- [ ] **Step 5: Commit only these files and inspect `git diff --cached --name-only`.** Message: `feat: expose local H3 resolution presets`.

### Task 2: Persist and validate a managed plan

**Files:**
- Create: `backend/app/core/managed_runs/models.py`
- Create: `backend/app/core/managed_runs/store.py`
- Create: `backend/app/core/managed_runs/planning.py`
- Create: `backend/app/api/managed_runs.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_managed_run_planning.py`
- Test: `backend/tests/test_managed_run_api.py`

**Interfaces:**
- Produces `ManagedRun` with `run_id`, `project_id`, `steps`, `resolution_preset`, `state`, `current_index`, `current_job_id`, `processed_event_ids`, and revision fingerprints.
- `create_draft(project_id: str, steps: list[RunStep]) -> ManagedRun` validates current ordered Shots and tail sources. `activate_run(run_id: str, preset: str) -> ManagedRun` verifies fingerprints and one-active-run-per-project.
- API: `POST /api/projects/{project_id}/managed-run/plan`, `POST .../start`, `GET ...`, and `POST .../stop`; plan response is structured but is not an Agent tool.

- [ ] **Step 1: Write failing tests.** Build a two-Shot project; assert a valid Shot 1→2 tail dependency is stored, a future/nonexistent source is rejected, changed Shot brief blocks activation, unknown resolution is rejected, and a second active run gets 409. Test API status from the real store, not a mocked response.

```python
def test_cannot_activate_stale_plan(two_shot_project):
    draft = create_draft(two_shot_project.id, [RunStep(shot_id=s.id) for s in two_shot_project.shots])
    change_shot_brief(two_shot_project.shots[1].id, "A new action")
    with pytest.raises(ValueError, match="changed"):
        activate_run(draft.run_id, "landscape-768")
```
- [ ] **Step 2: Run RED.** `cd backend; python -m pytest -q tests/test_managed_run_planning.py tests/test_managed_run_api.py`. Expected: module or route absent.
- [ ] **Step 3: Implement models/store/validation/routes.** Store run JSON below the project directory with atomic replacement and a per-project lock. Compute fingerprints from `shot_id`, brief/authored fields, refs, and project order; never rely on `updated_at` alone. The plan route calls the configured local model with a constrained JSON schema, validates the result, and saves only a draft. The start route binds one preset and persists `active` before any continuation is scheduled.

```python
def activate_run(run_id: str, preset: str) -> ManagedRun:
    width, height = resolve_local_resolution(preset)
    with project_run_lock(run_id):
        run = load_run(run_id)
        assert_current_plan_fingerprints(run)
        assert_no_other_active_run(run.project_id, run_id)
        return save_run(run.model_copy(update={"state": "active", "resolution_preset": preset}))
```
- [ ] **Step 4: Run GREEN.** Run the Task 2 pytest command and the existing project API tests affected by router registration.
- [ ] **Step 5: Commit only Task 2 paths.** Message: `feat: persist local H3 managed plans`.

### Task 3: Reuse H3 submission and expose one Agent tool

**Files:**
- Create: `backend/app/agents/director/tool_handlers/video.py`
- Create: `backend/app/agents/director/video_submission.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/app/agents/director/tool_execution.py`
- Modify: `backend/app/agents/director/tool_schema.py`
- Modify: `backend/app/agents/director/harness_runtime.py`
- Test: `backend/tests/test_managed_h3_start_tool.py`
- Test: `backend/tests/test_harness_runtime.py`

**Interfaces:**
- Extract `submit_local_h3_shot(shot_id: str, width: int, height: int, svc: DirectorService) -> Shot` into `video_submission.py` from the existing submit endpoint so UI and tool execute the same preflight and Job creation.
- `start_h3_video(shot_id)` returns `{ok:true, shot_id, job_id, status}`. In a managed turn it gets dimensions from the persisted run; outside management it is offered only for an explicit one-off user request.

- [ ] **Step 1: Write failing tests.** For an active two-Shot run, call the real `BackendTurn` tool boundary and assert the first call returns the Job ID saved on Shot 1, a duplicate returns the same ID without a second Job, Shot 2 is rejected before Shot 1 succeeds, and a stopped run rejects a late call. Assert an ordinary chat turn does not offer the new tool.

```python
first = await turn.dispatch("tool", {"name": "start_h3_video", "arguments": {"shot_id": shot1.id}, "call_id": "call-1"})
assert first["job_id"] == load_shot(project.id, shot1.id).h3_job_id
assert len(list_shot_h3_generations(project.id, shot1.id)) == 1
```
- [ ] **Step 2: Run RED.** `cd backend; python -m pytest -q tests/test_managed_h3_start_tool.py tests/test_harness_runtime.py`. Expected: `start_h3_video` absent.
- [ ] **Step 3: Implement service extraction and tool routing.** Keep current submit preflight byte-for-byte where possible. Pass only `shot_id` from LLM; look up run token and preset server-side. Bind Job ID to the exact run step before returning. Keep `get_status` behavior unchanged.

```python
async def start_h3_video(project_id: str, shot_id: str, run_id: str | None,
                         svc: DirectorService) -> dict:
    run, width, height = require_authorized_local_start(project_id, shot_id, run_id)
    shot = await submit_local_h3_shot(shot_id, width, height, svc)
    return bind_or_return_existing_job(run, shot_id, shot.h3_job_id)
```
- [ ] **Step 4: Run GREEN.** Run the Task 3 tests and existing H3 submit tests. Inspect the diff around dirty `tool_schema.py` and `harness_runtime.py` so unrelated loop changes are not staged.
- [ ] **Step 5: Commit only feature hunks.** Message: `feat: let Director start local H3 video jobs`.

### Task 4: Planned tail-frame direct connection

**Files:**
- Create: `backend/app/core/managed_runs/tail.py`
- Modify: `backend/app/core/projects/transitions.py`
- Modify: `backend/app/core/managed_runs/models.py`
- Test: `backend/tests/test_managed_run_tail.py`

**Interfaces:**
- `attach_planned_tail(run: ManagedRun, source_job_id: str, target_shot_id: str) -> str` returns the extracted `layout_ref_id` after selecting it for H3.
- The selected Layout records `feedback_source="managed_run"`; it does not claim human visual QC. Existing `extract_clip_tail_frame` remains unchanged for manual chat.

- [ ] **Step 1: Write a failing test.** With a real short source clip fixture and a preauthorized Shot 1→2 plan step, assert the target receives exactly one selected clip-tail Layout with the source Job ID, correct Picture binding, and managed-run provenance. A plan without that dependency must not extract or select anything.

```python
layout_id = attach_planned_tail(run, source_job.id, target_shot.id)
selected = next(x for x in load_shot(project.id, target_shot.id).layout_refs if x.id == layout_id)
assert selected.selected_for_h3 and selected.feedback_source == "managed_run"
assert selected.origin.source_job_id == source_job.id
```
- [ ] **Step 2: Run RED.** `cd backend; python -m pytest -q tests/test_managed_run_tail.py`. Expected: attach function missing.
- [ ] **Step 3: Implement direct extraction and selection.** Call the existing tail-frame extraction with `source_job_id` (not an inferred latest), mark the resulting Layout usable with managed-run provenance, select it, and trigger the normal prompt rewrite path before submission. Do not call user-approval handlers or create a generated Layout.

```python
extracted = extract_clip_tail_frame(project_id=run.project_id, source_shot_id=source_id,
                                    target_shot_id=target_id, source_job_id=source_job_id)
reviewed = review_layout_reference(load_shot(run.project_id, target_id), extracted["layout_ref_id"],
                                   LayoutReviewStatus.usable, "Preauthorized tail handoff",
                                   feedback_source="managed_run")
save_shot(select_layout_reference(reviewed, extracted["layout_ref_id"], True))
```
- [ ] **Step 4: Run GREEN and existing tail tests.** `cd backend; python -m pytest -q tests/test_managed_run_tail.py tests/test_tail_frame_extraction.py tests/test_director_tail_frame_tool.py`.
- [ ] **Step 5: Commit only Task 4 paths.** Message: `feat: connect preauthorized clip tails in managed runs`.

### Task 5: Terminal-event continuation, recovery, and stop

**Files:**
- Create: `backend/app/core/managed_runs/worker.py`
- Modify: `backend/app/core/managed_runs/store.py`
- Modify: `backend/app/core/jobs/shot_sync.py`
- Modify: `backend/app/core/jobs/runner.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/managed_runs.py`
- Test: `backend/tests/test_managed_run_worker.py`

**Interfaces:**
- `record_h3_terminal(job: JobRecord) -> None` persists a unique `(run_id,job_id,status)` event only for the bound local Job.
- `resume_managed_runs() -> None` reconciles active run records against persisted Jobs at startup.
- `stop_managed_run(run_id: str) -> ManagedRun` persists `stopping` before calling `cancel_job(current_job_id)`.

- [ ] **Step 1: Write failing event/race tests.** Assert success enqueues one compact Agent continuation and advances exactly one Shot; a duplicate terminal callback does not submit twice; failure pauses; stop before completion prevents continuation; restart finds one unprocessed terminal event. Assert the feedback packet has IDs/status/next brief but no video payload or full chat history.

```python
record_h3_terminal(succeeded_job)
record_h3_terminal(succeeded_job)
assert pending_events(run.run_id) == [f"{run.run_id}:{succeeded_job.id}:succeeded"]
await stop_managed_run(run.run_id)
await drain_events(run.run_id)
assert load_run(run.run_id).state == "stopped"
assert load_run(run.run_id).current_index == 0
```
- [ ] **Step 2: Run RED.** `cd backend; python -m pytest -q tests/test_managed_run_worker.py`. Expected: worker functions absent.
- [ ] **Step 3: Implement the event worker.** The synchronous Job terminal hook only records a durable event; it never calls the LLM inline. `runner._run_job` schedules the event drain only after releasing the Comfy GPU lease, avoiding a GPU-busy race on the next inference. A fresh Harness turn receives the compact event and current plan step, may use existing prompt/tail tools, and may call `start_h3_video` for exactly the next Shot. Recheck active state before dispatch and before Job submission. Reconcile on app startup. Stop cancels the bound local Job and marks `stopped` after terminal handling.

```python
def record_h3_terminal(job: JobRecord) -> None:
    run = active_run_bound_to(job.id)
    if run and job.params.get("h3_provider") == "local":
        persist_event_once(run.run_id, job.id, job.status.value)
# The job runner schedules drain after Comfy lease release.
```
- [ ] **Step 4: Run GREEN plus Job regression tests.** Run Task 5 pytest, `tests/test_harness_runtime.py`, and H3 Job/shot-sync tests.
- [ ] **Step 5: Commit only Task 5 paths.** Message: `feat: continue and stop managed H3 runs safely`.

### Task 6: Production control and integrated verification

**Files:**
- Create: `frontend/src/features/production/ManagedRunControls.tsx`
- Modify: `frontend/src/features/production/api.ts`
- Modify: `frontend/src/features/production/ProductionPage.tsx`
- Modify: `frontend/src/features/production/ProductionPage.test.tsx`
- Modify: `frontend/src/shared/styles.css`
- Test: `backend/tests/test_managed_run_api.py`

**Interfaces:**
- Production displays the plan, exact resolution, current Shot/job, `Start management`/`Stop management`, and a paused reason. It polls only the run status endpoint; it never orchestrates the sequence client-side.

- [ ] **Step 1: Write failing UI tests.** Assert Start is disabled until plan and explicit local preset are available, shows Shot-brief tail handoffs, Stop works during an active Job, a page remount restores active state, and MiniMax provider never exposes Start management. Test desktop and mobile rendering.

```tsx
expect(screen.getByRole("button", { name: "Start management" })).toBeDisabled();
await user.selectOptions(screen.getByLabelText("Resolution"), "landscape-768");
expect(screen.getByText(/1376×768/)).toBeVisible();
```
- [ ] **Step 2: Run RED.** `cd frontend; npm test -- --run src/features/production/ProductionPage.test.tsx`. Expected: managed controls absent.
- [ ] **Step 3: Implement a compact control beside the existing H3 submit controls.** Reuse current surface hierarchy; show selected preset's real dimensions and a 1080 VRAM caution. Send plan/start/stop to backend and refresh run status without embedding LLM orchestration in React.

```tsx
<ManagedRunControls
  plan={runPlan}
  run={managedRun}
  resolutionPreset={resolutionPreset}
  onStart={() => startManagedRun(projectId, resolutionPreset)}
  onStop={() => stopManagedRun(projectId)}
/>
```
- [ ] **Step 4: Verify.** Run frontend Production tests and `npm run build`; run the backend managed-run, H3, Harness, and tail-frame tests. Then run a two-short-Shot local ComfyUI smoke test: confirm one selected resolution, terminal-event wakeup, planned tail connection, and Stop before the next submission. Record exact Job IDs and outcomes without generating a full film.
- [ ] **Step 5: Stage only feature files, run `git diff --cached --check`, and commit.** Message: `feat: add Production managed-run controls`.

## Final review

- [ ] Confirm every spec section has a passing test or explicit smoke evidence.
- [ ] Confirm no managed-run path can submit to MiniMax API or change resolution mid-run.
- [ ] Confirm no duplicate or late event can submit the next Shot after Stop.
- [ ] Confirm existing dirty Agent-loop edits remain separate from these commits.
- [ ] Report any live-GPU limitation honestly; do not claim complete video QC.
