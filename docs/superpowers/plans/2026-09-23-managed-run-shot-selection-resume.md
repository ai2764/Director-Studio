# Managed Run Shot Selection and Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users run any selected managed-plan Shots, rerun successful Shots, and resume after pause, stop, failure, completion, or backend restart without replaying unselected work.

**Architecture:** Keep `ManagedRun.steps` as the immutable reviewed plan and add a durable per-execution selection and pending queue. A focused selection module validates IDs, resolves existing successful tail sources, and computes warnings; the store owns locked lifecycle transitions, while continuation consumes only the persisted queue. The API exposes one Run-selected transition plus derived stale-plan state, and Production renders persisted checkboxes and warnings.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest/pytest-asyncio, React 19, TypeScript 5.8, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-23-managed-run-shot-selection-resume-design.md`

## Global Constraints

- Local ComfyUI H3 managed runs only; manual local runs and MiniMax API runs remain unchanged.
- A fresh managed plan defaults to every current Shot, but execution runs only the submitted selection.
- Successful Shots remain selectable and rerunnable; old Jobs and output files must remain in generation history.
- Pause, Stop, failure, completion, and backend restart preserve the plan, completion map, selection, and pending queue.
- A selected Shot with an unavailable tail source is warned and skipped; later independent selected Shots remain runnable.
- Screenplay text, Shot order, authored Shot fields, music-master/segment inputs, Picture bindings, or Voice bindings make the plan stale and non-runnable.
- Run, Resume, Stop, and Plan-again operations never delete Jobs, Layouts, prompts, or video files.
- Resolution is selected on the first batch and immutable for later batches on the same plan.

## Review Focus

- A Shot with a previous successful Job must still enter the pending queue when explicitly selected for rerun; pin this in Task 2 store tests.
- A newer failed H3 Job must not hide an older usable source video needed by a tail dependency; pin this in Task 1 selection tests.
- Stop after a Job was bound must clear only the active binding while retaining that Shot in the pending queue; pin this in Task 2 lifecycle tests.
- A brief/reference edit between Run-selected and H3 submission must pause without submitting a Job; pin this in Task 3 continuation/tool tests.
- After a source failure, resuming with only its dependent and an independent Shot must skip the dependent and run the independent Shot; pin this across Task 1 and Task 3 tests.

---

## File Structure

- Create `backend/app/core/managed_runs/selection.py`: selection normalization, successful-video discovery, dependency queue construction.
- Modify `backend/app/core/managed_runs/models.py`: durable batch fields and API view model.
- Modify `backend/app/core/managed_runs/store.py`: locked Run-selected, Stop/Resume, binding, and terminal transitions.
- Modify `backend/app/core/managed_runs/continuation.py`: queue-driven continuation and exact tail-source reuse.
- Modify `backend/app/agents/director/tool_handlers/video.py`: validate the first pending Shot rather than assuming a full-plan cursor.
- Modify `backend/app/api/managed_runs.py`: Run-selected endpoint, compatibility Start wrapper, stale-plan view.
- Modify `frontend/src/features/production/api.ts`: expanded managed-run types and Run-selected client.
- Modify `frontend/src/features/production/ManagedRunControls.tsx`: persisted selection, warnings, Run/Resume controls.
- Modify `frontend/src/shared/styles.css`: checkbox, completion, warning, and stale-plan presentation.
- Extend existing managed-run pytest and Vitest files; do not introduce a second test fixture framework.

### Task 1: Selection and dependency planner

**Files:**
- Create: `backend/app/core/managed_runs/selection.py`
- Modify: `backend/app/core/managed_runs/models.py`
- Create: `backend/tests/test_managed_run_selection.py`

**Interfaces:**
- Consumes: `ManagedRun.steps`, `ManagedRun.completed_job_ids`, `list_shot_h3_generations()`, and `resolve_source_clip()`.
- Produces: `ExecutionBatch`, `build_execution_batch(project_id: str, run: ManagedRun, requested_shot_ids: list[str]) -> ExecutionBatch`, and `latest_successful_video_job_id(project_id: str, shot_id: str, preferred_job_id: str | None = None) -> str | None`.

- [ ] **Step 1: Write failing model-default and selection-order tests**

```python
def managed_run_with_steps(*shot_ids: str) -> ManagedRun:
    return ManagedRun(
        run_id="mrun_test", project_id="prj_test",
        steps=[RunStep(shot_id=shot_id) for shot_id in shot_ids],
        plan_fingerprint="fp",
    )


def test_new_run_batch_fields_are_backward_compatible():
    run = ManagedRun(
        run_id="mrun_old", project_id="prj_old",
        steps=[RunStep(shot_id="sht_a")], plan_fingerprint="fp",
    )
    assert run.selected_shot_ids == []
    assert run.pending_shot_ids == []
    assert run.skipped_shots == {}
    assert run.tail_source_job_ids == {}


def test_selection_is_deduplicated_by_rejection_and_normalized_to_plan_order():
    run = managed_run_with_steps("sht_a", "sht_b", "sht_c")
    batch = build_execution_batch("prj", run, ["sht_c", "sht_a"])
    assert batch.selected_shot_ids == ["sht_a", "sht_c"]
    with pytest.raises(ValueError, match="duplicate"):
        build_execution_batch("prj", run, ["sht_a", "sht_a"])
    with pytest.raises(ValueError, match="outside this plan"):
        build_execution_batch("prj", run, ["sht_unknown"])
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run from `backend/`:

```powershell
python -m pytest -q tests/test_managed_run_selection.py
```

Expected: collection/import failure because `selection.py`, `ExecutionBatch`, and the new model fields do not exist.

- [ ] **Step 3: Add durable model fields and the batch result type**

```python
# backend/app/core/managed_runs/models.py
class ManagedRun(BaseModel):
    # existing fields remain unchanged
    selected_shot_ids: list[str] = Field(default_factory=list)
    pending_shot_ids: list[str] = Field(default_factory=list)
    skipped_shots: dict[str, str] = Field(default_factory=dict)
    tail_source_job_ids: dict[str, str] = Field(default_factory=dict)


class ManagedRunView(ManagedRun):
    is_stale: bool = False
    stale_reason: str = ""
```

```python
# backend/app/core/managed_runs/selection.py
@dataclass(frozen=True)
class ExecutionBatch:
    selected_shot_ids: list[str]
    pending_shot_ids: list[str]
    skipped_shots: dict[str, str]
    tail_source_job_ids: dict[str, str]
```

- [ ] **Step 4: Implement successful-video lookup without latest-generation ambiguity**

```python
def latest_successful_video_job_id(
    project_id: str,
    shot_id: str,
    preferred_job_id: str | None = None,
) -> str | None:
    ordered = list_shot_h3_generations(project_id, shot_id)
    by_id = {job.id: job for job in ordered}
    candidates = []
    if preferred_job_id and preferred_job_id in by_id:
        candidates.append(by_id[preferred_job_id])
    candidates.extend(job for job in reversed(ordered) if job.id != preferred_job_id)
    for job in candidates:
        try:
            resolve_source_clip(
                project_id=project_id, source_shot_id=shot_id,
                source_version=None, source_job_id=job.id, output_kind=None,
            )
        except ClipGenerationError:
            continue
        return job.id
    return None
```

- [ ] **Step 5: Implement ordered dependency evaluation**

```python
def build_execution_batch(project_id: str, run: ManagedRun,
                          requested_shot_ids: list[str]) -> ExecutionBatch:
    plan_ids = [step.shot_id for step in run.steps]
    if len(requested_shot_ids) != len(set(requested_shot_ids)):
        raise ValueError("selected Shot IDs contain a duplicate")
    unknown = sorted(set(requested_shot_ids) - set(plan_ids))
    if unknown:
        raise ValueError("selected Shot IDs are outside this plan: " + ", ".join(unknown))
    requested = set(requested_shot_ids)
    selected = [shot_id for shot_id in plan_ids if shot_id in requested]
    available_jobs = {
        shot_id: job_id
        for shot_id in plan_ids
        if (job_id := latest_successful_video_job_id(
            project_id, shot_id, run.completed_job_ids.get(shot_id)
        ))
    }
    producible = set(available_jobs)
    pending, skipped, tail_jobs = [], {}, {}
    for step in run.steps:
        if step.shot_id not in requested:
            continue
        source = step.tail_from_shot_id
        if source and source not in producible:
            skipped[step.shot_id] = (
                f"Planned tail source {source} has no successful video and is not runnable earlier in this selection"
            )
            continue
        if source and source not in requested:
            tail_jobs[step.shot_id] = available_jobs[source]
        pending.append(step.shot_id)
        producible.add(step.shot_id)
    return ExecutionBatch(selected, pending, skipped, tail_jobs)
```

- [ ] **Step 6: Add dependency-chain and older-success regression tests**

Add tests that create materialized `video.mp4` outputs and prove:

```python
def test_missing_source_skips_only_dependents_and_keeps_later_independent():
    run = ManagedRun(
        run_id="mrun_dependencies", project_id="prj_dependencies",
        plan_fingerprint="fp", steps=[
            RunStep(shot_id="sht_source"),
            RunStep(shot_id="sht_dependent", tail_from_shot_id="sht_source",
                    tail_reason="Continue the pose"),
            RunStep(shot_id="sht_independent"),
        ],
    )
    batch = build_execution_batch(
        "prj_dependencies", run, ["sht_dependent", "sht_independent"]
    )
    assert batch.pending_shot_ids == ["sht_independent"]
    assert "sht_dependent" in batch.skipped_shots


def test_newer_failed_job_does_not_hide_older_usable_source(tmp_path, monkeypatch):
    project, source, dependent = project_with_tail_dependency()
    succeeded = save_h3_generation(project.id, source.id, JobStatus.succeeded, b"old")
    save_h3_generation(project.id, source.id, JobStatus.failed, None)
    run = ManagedRun(
        run_id="mrun_history", project_id=project.id, plan_fingerprint="fp",
        steps=[RunStep(shot_id=source.id), RunStep(
            shot_id=dependent.id, tail_from_shot_id=source.id,
            tail_reason="Continue the pose",
        )],
    )
    batch = build_execution_batch(project.id, run, [dependent.id])
    assert batch.pending_shot_ids == [dependent.id]
    assert batch.tail_source_job_ids[dependent.id] == succeeded.id
```

Use these helpers in the new test file; the existing autouse data-directory
fixture supplies isolated paths, so do not monkeypatch production lookup
functions:

```python
def project_with_tail_dependency():
    project = create_project("Tail selection", "Two shots")
    source = Shot(id="sht_source", project_id=project.id, scene_id="sc",
                  title="Source", script_beat="Source", duration_s=5)
    dependent = Shot(id="sht_dependent", project_id=project.id, scene_id="sc",
                     title="Dependent", script_beat="Dependent", duration_s=5)
    save_shot(source)
    save_shot(dependent)
    save_project(project.model_copy(update={"shot_ids": [source.id, dependent.id]}))
    return project, source, dependent


def save_h3_generation(project_id: str, shot_id: str, status: JobStatus,
                       video_bytes: bytes | None):
    job = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions", name=shot_id,
        project_id=project_id, params={"shot_id": shot_id},
    )
    job.status = status
    if video_bytes is not None:
        path = save_output_file(
            job.id, "video", "video.mp4", video_bytes, project_id=project_id,
        )
        job.outputs = build_output_slots(job.id, {"video": path})
    save_job(job)
    return job
```

- [ ] **Step 7: Run focused tests and commit**

```powershell
python -m pytest -q tests/test_managed_run_selection.py tests/test_clip_generations.py
git add backend/app/core/managed_runs/models.py backend/app/core/managed_runs/selection.py backend/tests/test_managed_run_selection.py
git commit -m "feat(managed): plan selected execution batches"
```

Expected: both files pass with no failures.

### Task 2: Durable Run-selected lifecycle

**Files:**
- Modify: `backend/app/core/managed_runs/store.py`
- Modify: `backend/tests/test_managed_run_planning.py`
- Modify: `backend/tests/test_managed_run_continuation.py`

**Interfaces:**
- Consumes: `build_execution_batch()` from Task 1.
- Produces: `run_selected(project_id: str, run_id: str, shot_ids: list[str], resolution_preset: str | None) -> ManagedRun`, `current_step(run: ManagedRun) -> RunStep | None`, and queue-aware `bind_job()`/`record_terminal()` behavior.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_run_selected_can_rerun_a_completed_shot():
    project, first, second = _project_with_two_shots()
    draft = create_draft(project.id, [
        RunStep(shot_id=first.id), RunStep(shot_id=second.id),
    ])
    run = _save_run(draft.model_copy(update={
        "state": "completed", "resolution_preset": "landscape-480",
        "completed_job_ids": {first.id: "job_old"},
    }))
    resumed = run_selected(project.id, run.run_id, [first.id], None)
    assert resumed.state == "active"
    assert resumed.pending_shot_ids == [first.id]
    assert resumed.completed_job_ids[first.id] == "job_old"


def test_stop_preserves_pending_queue_and_clears_cancelled_binding():
    project, first, second = _project_with_two_shots()
    draft = create_draft(project.id, [
        RunStep(shot_id=first.id), RunStep(shot_id=second.id),
    ])
    running = run_selected(
        project.id, draft.run_id, [first.id, second.id], "landscape-480"
    )
    job = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions",
        name="managed first", project_id=project.id,
        params={"shot_id": first.id},
    )
    bind_job(project.id, running.run_id, first.id, job.id)
    request_stop(project.id, running.run_id)
    stopped = finish_stop(project.id, running.run_id)
    assert stopped.state == "stopped"
    assert stopped.current_job_id is None
    assert stopped.pending_shot_ids == [first.id, second.id]
```

Also test empty selection, immutable resolution, another active run, paused and completed reactivation, and an all-warning selection returning non-active state with persisted warnings.

- [ ] **Step 2: Run lifecycle tests and confirm RED**

```powershell
python -m pytest -q tests/test_managed_run_planning.py tests/test_managed_run_continuation.py
```

Expected: failures because `run_selected()` and queue semantics are absent.

- [ ] **Step 3: Add current-step helpers and initialize fresh drafts**

```python
def current_step(run: ManagedRun) -> RunStep | None:
    if not run.pending_shot_ids:
        return None
    shot_id = run.pending_shot_ids[0]
    return next((step for step in run.steps if step.shot_id == shot_id), None)


def _plan_index(run: ManagedRun, shot_id: str | None) -> int:
    if shot_id is None:
        return len(run.steps)
    return next(i for i, step in enumerate(run.steps) if step.shot_id == shot_id)
```

Set `selected_shot_ids=[step.shot_id for step in steps]` in `create_draft()` so the first UI load defaults to all Shots without starting execution.

Extend `_fingerprint()` at the same time so its project payload includes
`project.music_master.content_sha256` when present, and every authored Shot
payload includes `music_segment.model_dump(mode="json")` plus
`source_audio_path`. Add a planning test that changes only
`music_segment.core_start_s` and proves `run_selected()` rejects the old plan.

- [ ] **Step 4: Implement the locked Run-selected transition**

```python
def run_selected(project_id: str, run_id: str, shot_ids: list[str],
                 resolution_preset: str | None) -> ManagedRun:
    with _project_lock(project_id):
        run = load_run(project_id, run_id)
        if run is None:
            raise ValueError("Managed run not found")
        if run.state not in {"draft", "paused", "stopped", "completed"}:
            raise ValueError("Managed run is already active")
        if run.plan_fingerprint != _fingerprint(project_id):
            raise ValueError("Shot brief or project order changed since this plan")
        preset = run.resolution_preset or resolution_preset
        if not preset:
            raise ValueError("Managed H3 resolution is required")
        resolve_local_resolution(preset)
        if run.resolution_preset and resolution_preset and resolution_preset != run.resolution_preset:
            raise ValueError("Managed H3 resolution cannot change within one plan")
        if not shot_ids:
            raise ValueError("Select at least one Shot")
        if any(item.run_id != run_id and item.state in {"active", "stopping"}
               for item in list_runs(project_id)):
            raise ValueError("Another managed run is active for this project")
        batch = build_execution_batch(project_id, run, shot_ids)
        active = bool(batch.pending_shot_ids)
        return _save_run(run.model_copy(update={
            "state": "active" if active else "paused",
            "resolution_preset": preset,
            "selected_shot_ids": batch.selected_shot_ids,
            "pending_shot_ids": batch.pending_shot_ids,
            "skipped_shots": batch.skipped_shots,
            "tail_source_job_ids": batch.tail_source_job_ids,
            "current_index": _plan_index(run, batch.pending_shot_ids[0] if active else None),
            "current_job_id": None,
            "pending_event_id": f"selection:{uuid.uuid4().hex}" if active else None,
            "paused_reason": "" if active else "No selected Shots have satisfiable dependencies",
            "current_fingerprint": _fingerprint(project_id),
        }))
```

- [ ] **Step 5: Make binding, terminal advancement, and Stop queue-aware**

Update `bind_job()` to compare `shot_id` with `current_step(run).shot_id`.
On terminal success, remove only the first pending ID and update the latest Job map:

```python
remaining = run.pending_shot_ids[1:]
completed = {**run.completed_job_ids, step.shot_id: job_id}
next_id = remaining[0] if remaining else None
return _save_run(run.model_copy(update={
    "pending_shot_ids": remaining,
    "current_index": _plan_index(run, next_id),
    "current_job_id": None,
    "completed_job_ids": completed,
    "pending_event_id": f"{job_id}:succeeded" if remaining else None,
    "state": "active" if remaining else "completed",
    "paused_reason": "",
}))
```

On failure/cancellation, clear the active binding but keep the pending queue and first Shot. `finish_stop()` must clear `current_job_id` and `pending_event_id` without changing `pending_shot_ids`. Keep `activate_run()` as a compatibility wrapper that calls `run_selected()` with all plan IDs.

- [ ] **Step 6: Run lifecycle and race tests and commit**

```powershell
python -m pytest -q tests/test_managed_run_planning.py tests/test_managed_run_continuation.py tests/test_managed_h3_start_tool.py
git add backend/app/core/managed_runs/store.py backend/tests/test_managed_run_planning.py backend/tests/test_managed_run_continuation.py
git commit -m "feat(managed): persist resumable selected queues"
```

Expected: all focused lifecycle, stop-race, and binding tests pass.

### Task 3: Queue-driven continuation and exact tail reuse

**Files:**
- Modify: `backend/app/core/managed_runs/continuation.py`
- Modify: `backend/app/agents/director/tool_handlers/video.py`
- Modify: `backend/tests/test_managed_run_continuation.py`
- Modify: `backend/tests/test_managed_h3_start_tool.py`

**Interfaces:**
- Consumes: `current_step()`, `pending_shot_ids`, `tail_source_job_ids`, and `latest_successful_video_job_id()`.
- Produces: one continuation per persisted pending event, exact source-Job tail extraction, and managed H3 submission limited to the first pending Shot.

- [ ] **Step 1: Write failing continuation tests for sparse queues**

```python
@pytest.mark.asyncio
async def test_continuation_runs_only_first_selected_pending_shot(monkeypatch):
    from app.core.managed_runs import continuation
    run = _run()
    request_stop(run.project_id, run.run_id)
    finish_stop(run.project_id, run.run_id)
    resumed = run_selected(run.project_id, run.run_id, ["sht_2"], None)
    started = []

    async def fake_agent_turn(current, _svc):
        step = current_step(current)
        assert step is not None
        started.append(step.shot_id)
        bind_job(current.project_id, current.run_id, step.shot_id, "job_selected")

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent_turn)
    await continuation.continue_run(resumed.project_id, resumed.run_id)
    assert started == ["sht_2"]


@pytest.mark.asyncio
async def test_resume_skips_missing_dependent_but_starts_later_independent(monkeypatch):
    from app.core.managed_runs import continuation
    project = create_project("Selective", "Three shots")
    source = Shot(id="sht_source", project_id=project.id, scene_id="sc",
                  title="Source", script_beat="Source", duration_s=5)
    dependent = Shot(id="sht_dependent", project_id=project.id, scene_id="sc",
                     title="Dependent", script_beat="Dependent", duration_s=5)
    independent = Shot(id="sht_independent", project_id=project.id, scene_id="sc",
                       title="Independent", script_beat="Independent", duration_s=5)
    for shot in (source, dependent, independent):
        save_shot(shot)
    save_project(project.model_copy(update={
        "shot_ids": [source.id, dependent.id, independent.id],
    }))
    draft = create_draft(project.id, [
        RunStep(shot_id=source.id),
        RunStep(shot_id=dependent.id, tail_from_shot_id=source.id,
                tail_reason="Continue the source pose"),
        RunStep(shot_id=independent.id),
    ])
    resumed = run_selected(
        project.id, draft.run_id, [dependent.id, independent.id], "landscape-480"
    )
    assert dependent.id in resumed.skipped_shots

    started = []
    async def fake_agent_turn(current, _svc):
        step = current_step(current)
        assert step is not None
        started.append(step.shot_id)
        bind_job(current.project_id, current.run_id, step.shot_id, "job_independent")

    monkeypatch.setattr(continuation, "_agent_turn", fake_agent_turn)
    await continuation.continue_run(project.id, resumed.run_id)
    assert started == [independent.id]
```

Add a start-tool test that edits a Shot after Run-selected and proves no H3 Job is submitted and the run pauses with a stale-plan message.

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
python -m pytest -q tests/test_managed_run_continuation.py tests/test_managed_h3_start_tool.py
```

Expected: sparse-selection tests fail because continuation still indexes the full `steps` list.

- [ ] **Step 3: Route every current-Shot lookup through `current_step()`**

Replace direct `run.steps[run.current_index]` reads in `_agent_turn()`,
`continue_run()`, `schedule_pending_runs()`, and `start_h3_video()` with:

```python
step = current_step(run)
if step is None:
    return
shot_id = step.shot_id
```

Keep `current_index` only for UI numbering and include the full-plan ordinal in the managed Agent message.

- [ ] **Step 4: Resolve and verify the exact tail source Job**

```python
def _tail_source_job_id(run: ManagedRun, step: RunStep) -> str | None:
    if not step.tail_from_shot_id:
        return None
    return (
        run.completed_job_ids.get(step.tail_from_shot_id)
        or run.tail_source_job_ids.get(step.shot_id)
        or latest_successful_video_job_id(run.project_id, step.tail_from_shot_id)
    )
```

In `prepare_planned_tail()`, reject a missing source with the persisted warning text. Do not return merely because the target appears in `prepared_tail_layout_ids`; load the referenced Layout and verify `layout.origin.source_job_id == source_job_id`. If it differs, extract/select the tail for the newly resolved Job and overwrite the target's prepared mapping. This ensures rerunning a selected source feeds its new result to a selected dependent.

- [ ] **Step 5: Preserve stale-plan and event idempotency checks**

Before tail preparation, Agent invocation, prompt writing, and H3 submission, retain the existing `_fingerprint()` equality checks. Ensure `schedule_pending_runs()` reconciles a tagged Job against `current_step(run)` and the current unique selection event, then binds once. A repeated terminal event must find the Job in `completed_job_ids.values()` and make no state change.

- [ ] **Step 6: Run continuation/tool suites and commit**

```powershell
python -m pytest -q tests/test_managed_run_continuation.py tests/test_managed_h3_start_tool.py tests/test_job_recovery.py
git add backend/app/core/managed_runs/continuation.py backend/app/agents/director/tool_handlers/video.py backend/tests/test_managed_run_continuation.py backend/tests/test_managed_h3_start_tool.py
git commit -m "feat(managed): continue sparse selected shot queues"
```

Expected: sparse queues, rerun-tail replacement, stale mutation, restart recovery, and duplicate-event tests pass.

### Task 4: Run-selected API and stale-plan view

**Files:**
- Modify: `backend/app/api/managed_runs.py`
- Modify: `backend/tests/test_managed_run_api.py`

**Interfaces:**
- Consumes: `run_selected()` from Task 2 and `ManagedRunView` from Task 1.
- Produces: `POST /api/projects/{project_id}/managed-run/{run_id}/run`, enriched GET/plan/start/stop responses, and compatibility `/start` behavior.

- [ ] **Step 1: Write failing API tests**

```python
def test_run_route_accepts_exact_selection_and_returns_warnings(client):
    response = client.post(
        f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
        json={"shot_ids": [first.id, third.id], "resolution_preset": "landscape-768"},
    )
    assert response.status_code == 200
    assert response.json()["selected_shot_ids"] == [first.id, third.id]
    assert response.json()["pending_shot_ids"] == [first.id, third.id]


def test_get_marks_changed_brief_stale_and_run_refuses_it(client):
    save_shot(second.model_copy(update={"script_beat": "Changed"}))
    view = client.get(f"/api/projects/{project.id}/managed-run").json()
    assert view["is_stale"] is True
    response = client.post(
        f"/api/projects/{project.id}/managed-run/{draft.run_id}/run",
        json={"shot_ids": [second.id]},
    )
    assert response.status_code == 409
```

Add coverage for default-all `/start`, empty selection, unknown ID, immutable resolution, Resume from paused/stopped/completed, all-warning non-start, and no deletion of an existing Job output directory.

- [ ] **Step 2: Run API tests and confirm RED**

```powershell
python -m pytest -q tests/test_managed_run_api.py
```

Expected: 404 for the new route and missing `is_stale` fields.

- [ ] **Step 3: Add request and view helpers**

```python
class RunManagedRunBody(BaseModel):
    shot_ids: list[str]
    resolution_preset: str | None = None


def _run_view(run: ManagedRun) -> ManagedRunView:
    try:
        stale = run.plan_fingerprint != _fingerprint(run.project_id)
        reason = "Shot brief, order, dialogue, or references changed" if stale else ""
    except ValueError as exc:
        stale, reason = True, str(exc)
    return ManagedRunView(**run.model_dump(), is_stale=stale, stale_reason=reason)
```

Use `response_model=ManagedRunView` (or `ManagedRunView | None`) on plan, GET, Run, Start, and Stop so derived fields are returned but never written to run JSON.

- [ ] **Step 4: Add Run-selected and compatibility Start routes**

```python
@router.post("/projects/{project_id}/managed-run/{run_id}/run",
             response_model=ManagedRunView)
async def run_managed_selection(project_id: str, run_id: str,
                                body: RunManagedRunBody) -> ManagedRunView:
    try:
        run = run_selected(project_id, run_id, body.shot_ids, body.resolution_preset)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if run.state == "active":
        schedule_continuation(project_id)
    return _run_view(run)
```

Change `/start` to load the draft, pass every `step.shot_id` to `run_selected()`, and schedule only if active. Do not remove the existing route because packaged or older frontends may still call it.

- [ ] **Step 5: Run API plus application tests and commit**

```powershell
python -m pytest -q tests/test_managed_run_api.py tests/test_projects_api.py tests/test_managed_run_planning.py
git add backend/app/api/managed_runs.py backend/tests/test_managed_run_api.py
git commit -m "feat(managed): expose run-selected and stale-plan API"
```

Expected: API tests pass and existing project routes remain green.

### Task 5: Production selection and Resume UI

**Files:**
- Modify: `frontend/src/features/production/api.ts`
- Modify: `frontend/src/features/production/ManagedRunControls.tsx`
- Modify: `frontend/src/features/production/ManagedRunControls.test.tsx`
- Modify: `frontend/src/shared/styles.css`

**Interfaces:**
- Consumes: `ManagedRunView` JSON from Task 4.
- Produces: `runManagedSelection(projectId: string, runId: string, shotIds: string[], resolutionPreset?: string)`, controlled Shot checkboxes, warnings, and Run/Resume labels.

- [ ] **Step 1: Write failing UI tests for selection and Resume**

Extend the API mock with `runManagedSelection`. Add tests that prove:

```tsx
const draftWithSelection = {
  run_id: "mrun_1", project_id: "prj_1", state: "draft",
  resolution_preset: null, current_index: 0, current_job_id: null,
  pending_event_id: null, paused_reason: "", is_stale: false,
  stale_reason: "", selected_shot_ids: ["sht_1", "sht_2"],
  pending_shot_ids: [], skipped_shots: {}, tail_source_job_ids: {},
  completed_job_ids: {}, steps: [
    { shot_id: "sht_1", tail_from_shot_id: null, tail_reason: "" },
    { shot_id: "sht_2", tail_from_shot_id: "sht_1", tail_reason: "Match the door" },
  ],
} as const;

const stoppedRun = {
  ...draftWithSelection, run_id: "mrun_2", state: "stopped",
  resolution_preset: "portrait-768", selected_shot_ids: ["sht_2"],
  pending_shot_ids: ["sht_2"], completed_job_ids: { sht_1: "job_done" },
} as const;

function renderControls() {
  return render(<ManagedRunControls projectId="prj_1" shots={[
    { id: "sht_1", title: "Open" }, { id: "sht_2", title: "Enter" },
  ]} provider="local" presets={[
    { id: "portrait-768", label: "Portrait 768", width: 768, height: 1376 },
  ]} onProjectChanged={() => {}} />);
}

it("defaults a fresh plan to all shots and submits only checked shots", async () => {
  vi.mocked(planManagedRun).mockResolvedValue(draftWithSelection as never);
  renderControls();
  fireEvent.click(await screen.findByRole("button", { name: "Plan managed run" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Enter/ }));
  fireEvent.change(screen.getByLabelText("Managed run resolution"), {
    target: { value: "portrait-768" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run selected" }));
  await waitFor(() => expect(runManagedSelection).toHaveBeenCalledWith(
    "prj_1", "mrun_1", ["sht_1"], "portrait-768",
  ));
});


it("allows a successful shot to be selected again after stop", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(stoppedRun as never);
  renderControls();
  fireEvent.click(await screen.findByRole("checkbox", { name: /Open/ }));
  fireEvent.click(screen.getByRole("button", { name: "Resume selected" }));
  expect(runManagedSelection).toHaveBeenCalledWith(
    "prj_1", "mrun_2", ["sht_1", "sht_2"], undefined,
  );
});
```

Add tests for persisted dependency warnings, completed-batch empty defaults, stale Plan-again state, no-selection disabled state, and active Stop behavior.

- [ ] **Step 2: Run component tests and confirm RED**

Run from `frontend/`:

```powershell
npm test -- ManagedRunControls.test.tsx
```

Expected: missing mock export/function and no checkbox controls.

- [ ] **Step 3: Expand the API type and add the Run-selected client**

```ts
export interface ManagedRun {
  // existing fields remain
  selected_shot_ids: string[];
  pending_shot_ids: string[];
  skipped_shots: Record<string, string>;
  tail_source_job_ids: Record<string, string>;
  completed_job_ids: Record<string, string>;
  is_stale: boolean;
  stale_reason: string;
}

export async function runManagedSelection(
  projectId: string, runId: string, shotIds: string[], resolutionPreset?: string,
): Promise<ManagedRun> {
  const res = await fetch(`/api/projects/${projectId}/managed-run/${runId}/run`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shot_ids: shotIds, resolution_preset: resolutionPreset }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
```

- [ ] **Step 4: Implement deterministic checkbox defaults**

Store `selected` as `Set<string>`. Whenever a new run ID/state snapshot is loaded:

```ts
function defaultSelection(run: ManagedRun): Set<string> {
  if (run.state === "draft") return new Set(run.selected_shot_ids.length
    ? run.selected_shot_ids : run.steps.map((step) => step.shot_id));
  if (run.state === "paused" || run.state === "stopped") {
    return new Set(run.pending_shot_ids);
  }
  return new Set();
}
```

Do not recompute defaults on every 2.5-second poll when the run ID and lifecycle state are unchanged, or the poll will overwrite user checkbox edits. Disable selection only while active/stopping/busy.

- [ ] **Step 5: Render selection, completion, warnings, and actions**

Each plan row contains a labelled checkbox, Shot number/title, an optional
`Generated` marker from `completed_job_ids`, the existing tail reason, and
`skipped_shots[shot_id]` as a warning. Use:

```tsx
const resumable = run && ["paused", "stopped"].includes(run.state);
const actionLabel = resumable ? "Resume selected" : "Run selected";
const chosen = run.steps.filter((step) => selected.has(step.shot_id)).map((step) => step.shot_id);
```

Disable the action when `chosen.length === 0`, the draft resolution is invalid,
the run is stale, busy, active, or stopping. For stale plans, show
`run.stale_reason` and leave only **Plan again** available. Keep Stop unchanged
for active/stopping state. Add focused CSS classes for checkbox alignment,
generated status, and warning text; preserve the existing mobile card layout.

- [ ] **Step 6: Run frontend tests and build, then commit**

```powershell
npm test -- ManagedRunControls.test.tsx ProductionPage.test.tsx
npm run build
git add frontend/src/features/production/api.ts frontend/src/features/production/ManagedRunControls.tsx frontend/src/features/production/ManagedRunControls.test.tsx frontend/src/shared/styles.css
git commit -m "feat(production): select and resume managed shots"
```

Expected: component tests pass and TypeScript/Vite build exits 0.

### Task 6: Whole-system regression verification

**Files:**
- Test only; no planned product-file changes.

**Interfaces:**
- Consumes: the complete backend/API/UI feature from Tasks 1–5.
- Produces: fresh evidence that existing managed runs, Director tools, Production, and MV work remain compatible.

- [ ] **Step 1: Run the full backend suite**

From `backend/`:

```powershell
python -m pytest -q
```

Expected: all tests pass; platform-dependent skips remain skips, not failures.

- [ ] **Step 2: Run the full frontend suite and production build**

From `frontend/`:

```powershell
npm test
npm run build
```

Expected: all Vitest tests pass and `tsc && vite build` exits 0.

- [ ] **Step 3: Perform a local three-Shot smoke run**

Use three short local H3 Shots where Shot 2 has a planned tail dependency on
Shot 1 and Shot 3 is independent:

1. Plan and confirm all three checkboxes default selected.
2. Run Shot 1, then Stop while Shot 2 is pending; confirm Shot 1's video remains visible.
3. Resume with only Shot 3; confirm Shot 1 does not rerun and Shot 3 starts.
4. Resume with Shot 2 while Shot 1's successful video exists; confirm its tail is extracted from the recorded Shot 1 Job.
5. Select Shot 1 again; confirm a new Job becomes current while the old generation remains in history.
6. Edit Shot 3's brief; confirm the plan becomes stale and Run selected is disabled until Plan again.

Expected: every observation matches the six checks, and no Job/output directory is removed.

- [ ] **Step 4: Record the verified commit range**

```powershell
git log --oneline e727e21..HEAD
git status --short
```

Expected: five feature commits from Tasks 1–5 if no tasks were squashed, the pre-existing MV working-tree changes remain uncommitted, and no unexpected generated files are staged.
