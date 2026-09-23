# Managed Run Shot Selection and Resume

## Problem and decisions

Managed local H3 runs already persist their plan, current Shot index, bound Job,
completed Jobs, lifecycle state, and tail-frame preparation. Active runs can
recover after a backend restart. The missing user workflow is selective and
repeatable execution: a stopped, paused, failed, or completed run cannot be
started again, and the current plan always executes every Shot.

The saved managed plan will become a reusable, immutable execution blueprint.
The user can select any planned Shots and run exactly that selection. A fresh
plan initially selects every Shot. Later executions default to the unfinished
selection but allow successful Shots to be selected and generated again.
Starting or resuming execution never replans, deletes a prior video, or silently
changes a Shot. A material screenplay or Shot change makes the saved plan stale
and non-runnable until the user explicitly plans again.

## Scope

- Local ComfyUI H3 managed runs only. Manual local runs and MiniMax API runs are
  unchanged.
- Add Shot selection to the existing Production managed-run plan.
- Allow the same valid plan to execute selected Shots from `draft`, `paused`,
  `stopped`, or `completed` state.
- Preserve successful outputs and allow an already successful Shot to be
  selected for another generation.
- Warn and skip a selected Shot whose planned tail dependency cannot be
  satisfied, while continuing other eligible selected Shots.
- Make screenplay, Shot order, authored Shot fields, or conditioning-reference
  changes invalidate the plan.

This change does not add automatic visual QC, quality retries, remote-provider
management, output deletion, or arbitrary edits to the plan during execution.

## Immutable plan and mutable execution batch

`ManagedRun.steps` remains the complete reviewed plan in project Shot order.
Each step retains its Shot ID and optional tail-frame source and reason. Running
selected Shots must not remove, reorder, or rewrite these steps. Planning is the
only operation that calls the planning model.

The run record adds durable execution-batch fields:

- `selected_shot_ids`: the exact ordered selection submitted by the user for
  the current or most recent batch.
- `pending_shot_ids`: selected Shots that have not succeeded in this batch and
  have not been skipped for an unsatisfied dependency.
- `skipped_shots`: a mapping from selected Shot ID to the dependency warning
  that prevented it from entering the pending queue.
- `completed_job_ids`: the existing mapping, interpreted as the latest
  successful managed Job for each Shot across every batch on this plan.

The persisted `steps` are the source of order. The backend rejects unknown or
duplicate selected IDs and normalizes the submitted selection into plan order.
`current_index` continues to identify the full-plan index of the first pending
Shot for compatibility and display. If no Shot is pending, it points just after
the last plan step.

Creating a fresh draft initializes `selected_shot_ids` to all plan Shot IDs for
the UI. It does not create a pending queue or start work. Submitting a batch
atomically replaces the selection, computes dependency warnings, and creates a
new pending queue. It never clears `completed_job_ids`.

## Lifecycle and resume behavior

The Production action is conceptually **Run selected**. The UI may label it
**Start managed run** on a draft and **Resume selected** on a stopped or paused
run, but both use the same backend transition.

The transition is allowed from `draft`, `paused`, `stopped`, and `completed`.
It requires at least one selected Shot, a valid plan fingerprint, no active or
stopping managed run for the project, and a valid fixed local resolution. A
draft receives its resolution on first execution. Later batches reuse that
resolution; changing resolution requires a new plan.

When execution begins, state becomes `active` and the first eligible selected
Shot becomes current. A successful Job removes that Shot from
`pending_shot_ids`, updates its latest `completed_job_ids` entry, and advances
to the next pending Shot. When the queue is empty, state becomes `completed`.
This means `completed` describes the latest selected batch, not that the plan
can never be used again.

On H3 failure or an Agent/prompt blocker, state becomes `paused`. The current
Shot stays unfinished. The next UI visit defaults the failed current Shot and
the remaining pending Shots to selected; successful earlier Shots are not
selected by default but remain selectable. The user may alter the selection
before resuming.

Stop retains the immutable plan, selection, pending queue, completed Job map,
prompt, Layouts, and video outputs. It cancels an in-flight Job using the
existing race-safe stop behavior and sets state to `stopped`. A cancelled Shot
remains unfinished and can be selected later. Closing the page has no effect.
Backend restart recovery for an `active` batch remains automatic; a `paused` or
`stopped` batch waits for an explicit user action.

Selecting a successful Shot creates a new H3 Job. On success, that Shot points
to the new Job as its current generation and `completed_job_ids` records the
new Job. Older Jobs and their output files remain available through generation
history. A rerun does not modify the saved plan or automatically rewrite the
Shot brief. It reuses the current valid saved prompt and assets; a missing
required prompt may be authored from the same saved Shot brief before launch.

## Tail dependency evaluation

Dependencies are evaluated before activating each selected batch. For a
selected target with `tail_from_shot_id`, the source is satisfied when either:

1. the source is selected earlier in the same batch and will produce a new
   successful result before the target; or
2. the source already has a recoverable successful H3 video, preferring the
   latest successful Job recorded by this managed plan and otherwise the
   Shot's latest successful H3 generation.

When neither condition holds, the backend records a warning for the target and
omits it from `pending_shot_ids`. This is not a fatal batch error. Independent
selected Shots later in plan order continue normally. The skipped target stays
selectable in a later batch after its source becomes available.

If a source selected earlier in the same batch later fails, the normal failure
rule pauses the batch with that source still unfinished. On the next Resume,
reselecting the source gives it another explicit attempt. If the user instead
leaves the failed source unselected and it has no earlier successful video,
dependency evaluation skips its targets with warnings while allowing selected
independent Shots to continue. A dependent target never silently runs without
the reviewed tail handoff. If the source is rerun successfully in the same
batch, the target uses that new Job; otherwise it uses the latest recoverable
successful source chosen during execution.

The UI displays dependency warnings beside affected Shots before execution and
retains the persisted skip reason afterward. Warnings require no confirmation
because the requested policy is to continue eligible work.

## Plan validity

The existing plan fingerprint remains the authority and covers:

- screenplay text;
- project Shot IDs and order;
- title, script beat, shot type, camera angle, camera motion, composition,
  duration, and dialogue for every Shot;
- the project music-master content identity plus each Shot's optional music
  segment timing and source-audio binding;
- ordered Picture and Voice reference bindings.

Any difference makes the plan stale. Reads expose `is_stale` and a concise
reason derived from the current project fingerprint. Run selected refuses a
stale plan. An active run that becomes stale pauses before another Agent turn
or H3 submission. The plan JSON remains as history, but the UI disables its
execution controls and presents **Plan again**.

Video Job state, current video links, prompts, Layout review state, and managed
execution progress do not alter the plan fingerprint. Generating or rerunning
a video therefore does not invalidate the plan. Planning again creates a new
run plan and leaves prior Jobs and output files intact.

## Backend API

Keep the existing planning and read endpoints. Add one execution endpoint:

`POST /api/projects/{project_id}/managed-run/{run_id}/run`

Request:

```json
{
  "shot_ids": ["sht_a", "sht_c"],
  "resolution_preset": "portrait-768"
}
```

`resolution_preset` is required only for a draft's first batch and must match
the persisted value afterward. The response is the updated `ManagedRun`,
including normalized selection, pending queue, skipped warnings, current state,
and stale status. Keep the existing `/start` route as a compatibility wrapper
that submits all planned Shots when no selection is supplied.

The stop endpoint keeps its current cancellation contract. The managed-run GET
endpoint continues returning an active/stopping run first and otherwise the
latest plan, enriched with derived stale state. A new plan does not overwrite
older run JSON files.

All selection validation and state transitions occur under the existing
per-project managed-run lock. The continuation worker consumes only the
persisted pending queue. Replayed run requests and terminal events must not
submit duplicate H3 Jobs.

## Production UI

The plan list gains a checkbox for every Shot:

- A fresh draft checks all Shots by default.
- After pause or stop, unfinished pending Shots are checked by default and
  successful Shots are unchecked, while every Shot remains selectable.
- After a completed batch, no automatic rerun begins; the user explicitly
  chooses the next selection.
- Existing video status is shown without disabling its checkbox.
- Tail dependencies and unsatisfied-dependency warnings are shown on the
  corresponding target Shot.

The primary action reads **Run selected** for a draft or completed batch and
**Resume selected** after pause or stop. It is disabled with no selection, an
invalid resolution, a stale plan, or while another run is active. Stop remains
available only while active/stopping. **Plan again** is explicit and is the only
way to replace a stale plan; it does not delete video history.

Polling remains limited to active/stopping execution. Project reload reads the
persisted run and reconstructs checkbox defaults from selection, pending,
completion, and lifecycle state rather than browser-local state.

## Error handling and safety

- Invalid, duplicate, or foreign Shot IDs reject the run request without any
  state change.
- A stale plan rejects execution without clearing its history.
- An empty effective queue caused entirely by dependency warnings returns the
  updated non-active run and warnings; it does not claim generation started.
- A Job can be bound only to the first pending Shot and current execution
  event. Existing atomic binding and terminal-event idempotency remain in use.
- Stop/run races are resolved by persisted state checks before Agent work,
  prompt writes, Job submission, and Job binding.
- No plan or resume operation deletes files, Jobs, Layouts, prompts, or videos.

## Verification

Backend model and store tests cover selection normalization, queue persistence,
successful-Shot reruns, completed Job replacement without old Job deletion,
and current-index compatibility. API tests cover default-all draft execution,
arbitrary selection, resume from paused/stopped/completed, immutable resolution,
empty selection, stale-plan rejection, and compatibility `/start` behavior.

Continuation tests cover failure retaining the current Shot, Stop preserving
pending work, service-restart recovery, execution of a later independent Shot
after a dependency skip, use of a prior successful source video, use of a newly
rerun source, and a Resume that skips dependents of an unselected failed source.
Race tests continue to prove that stop, replayed terminal events, and repeated
run requests cannot create duplicate Jobs.

Frontend tests cover default-all selection, successful Shot re-selection,
pause/stop checkbox defaults, warning display, stale-plan disabling, exact
selected request payloads, and Run/Resume/Stop labels. The full backend and
frontend suites must pass, followed by a local two- or three-Shot smoke run
that stops and resumes without regenerating an unselected successful Shot.

## Out of scope

Deleting generation history, pruning Job files, editing a plan in place,
changing resolution within one plan, remote MiniMax API management, automatic
quality judgment, and automatic retry after a failed video remain out of scope.
