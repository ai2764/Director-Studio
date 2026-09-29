# Managed Local H3 Video Runs

## Problem and decision

Director chat can plan Shots, write H3 prompts, and extract clip tail frames, but
it cannot submit an H3 video job. A single Harness turn also cannot wait through
several long-running video jobs: its tool-step limit and context window are not a
durable scheduler. Users need an opt-in Production control that lets the Agent
continue from one Shot to the next, at one chosen resolution, while remaining
interruptible.

This design uses a persisted backend run coordinator. A job's terminal event
wakes a **new**, compact Agent turn. The Agent gets one new tool,
`start_h3_video`; it does not get a polling or callback tool. The coordinator,
not the LLM, owns waiting, run state, cancellation, and idempotency.

## Scope

- Manage **local ComfyUI H3** jobs only. MiniMax API jobs remain manual and are
  never included in a managed run.
- Show a proposed ordered run plan before activation. The plan is grounded in
  each current Shot brief and explicitly names any source-to-target tail-frame
  handoff and its reason. It is an execution plan, not a rewritten Shot brief.
- Select one resolution for the whole run. Production's local picker supports
  Auto and landscape/portrait 480, 720, 768, and 1080 tiers. Managed runs
  require an explicit tier and orientation; manual submissions may keep Auto.
- The user starts and stops management with one Production control. Closing the
  page does not stop a run. Stopping prevents further Agent continuations and
  requests cancellation of the current local H3 job.
- No video visual QC, video sampling, quality retries, automatic downscaling,
  or automatic generation of new Layouts. A planned tail frame is extracted
  directly from the completed source video and connected to the target Shot.

## Resolution contract

The local H3 node requires widths and heights in increments of 32 pixels. Keep
the current 480 tier at 864×480 and 720 tier at 1280×704. Add 768 at
1376×768 and 1080 at 1920×1088; portrait tiers swap width and height. Show
the actual pixel dimensions beside each tier, especially 1080, which is a
near-1080 tier rather than exactly 1080 high. The UI warns that larger tiers
may exceed available VRAM. The backend validates the selected local preset and
binds it to the run; the Agent cannot change resolution between Shots.

The MiniMax API's independent 768P/2K setting is unchanged. Local numeric
presets must not be presented as changing its remote resolution.

## Plan and authorization

The Agent creates a structured plan through a dedicated planning response,
not another callable Agent tool. The backend validates that every Shot ID is
current, the order matches the project, each tail source precedes its target,
and every handoff has a reason grounded in the corresponding Shot brief or an
explicit Agent proposal. Persist the plan with project and Shot revision
fingerprints. Display it before enabling Start management.

Starting the displayed plan authorizes its listed tail-frame handoffs without
per-frame human approval. A new or changed dependency, missing asset, stale
Shot brief, or other material deviation pauses the run for the user. Starting
does not authorize unrelated Layout generation, asset replacement, or script
rewrites. A planned tail-frame selection records `managed_run` provenance;
it must not be recorded as a human visual-QC approval. The selected frame
enters the target H3 Picture pack, and the target prompt is written or
rewritten against those actual Pictures before video submission.

## New Agent tool

`start_h3_video(shot_id)` is the only new Agent-callable tool. It calls the
same local-H3 submission service used by Production, with no duplicate job
builder. In a managed turn, the backend supplies the run's selected
resolution and checks that the Shot is exactly the next planned Shot, its
prompt and references are ready, and no current H3 job is already bound to
that run. The tool returns the actual `job_id` and queued status promptly.

Outside management, an explicit user request to run a named Shot may offer
the same tool for a one-off local submission; it does not grant permission to
continue to another Shot. An ordinary chat turn without such a request does
not offer the tool. Existing `write_prompt`, `extract_clip_tail_frame`, and
`get_status` remain available according to their current contracts.

## Run coordinator and feedback

Persist a run record containing project ID, immutable plan and revision
fingerprints, selected resolution, lifecycle state, current Shot index,
current job ID, and last processed terminal event. Only one active managed
run may own a project. Lifecycle states include `draft`, `active`, `stopping`,
`paused`, `completed`, and `stopped`.

Start management persists the active state before enqueueing the first Agent
continuation. A continuation may run only with an active run token matching
the current plan step. Binding the returned H3 Job ID to that step is atomic;
repeated tool calls for the same step return the existing Job ID rather than
submitting a second video. A manually submitted Job cannot advance the run.

On a local H3 job's succeeded/failed/cancelled terminal transition, first
persist the normal Job and Shot state. Then enqueue a managed-run event keyed
by `(run_id, job_id, terminal_status)`; never call the LLM synchronously inside
the Job completion hook. The event worker verifies that the run is still
active and the job matches its current binding. It supplies a compact Agent
feedback packet: run/Shot/job IDs, terminal status, output identifiers or
error, selected resolution, the next Shot brief, and the relevant plan step.
No full video or sampled frames enter context.

On success, a fresh Agent turn can carry out the planned tail-frame handoff,
rewrite the next Shot's prompt when required, and call `start_h3_video` for the
next Shot. On failure or a plan deviation, pause and report the reason;
do not blindly retry. A duplicated event is acknowledged without another
video submission. At startup, reconcile active runs against persisted Jobs:
process an unhandled terminal result once, wait for a live job, or pause if
the outcome cannot be confirmed. Keep each continuation's context focused on
the run plan, the completed Shot result, and the next Shot, rather than replaying
the entire conversation.

## Interrupt semantics

The Stop management action is a direct backend operation, not an Agent tool.
Set the run to `stopping` before cancelling its bound local job. Every event
dispatch and every `start_h3_video` precondition rechecks this state so a
late job completion or in-flight LLM response cannot submit another Shot.
When cancellation settles, mark the run `stopped`. If no job is active,
stopping is immediate. Ordinary manual Production controls remain available
after the run stops; a stopped run never resumes implicitly.

## Verification

- Backend tests for plan validation, stale-brief rejection, one active run per
  project, local-only submission, exact next-Shot and resolution preconditions.
- Event tests for success-to-next-Shot continuation, planned tail extraction
  and selection without human-QC attribution, failure pause, duplicate event,
  process restart reconciliation, and stop-vs-completion race.
- Agent tests showing `start_h3_video` is offered only for an active managed
  turn or an explicit one-off request, returns a real Job ID, and never starts
  the next Shot when management is off.
- Frontend tests for shared desktop/mobile preset choices, actual dimensions,
  plan review, Start/Stop states, stop feedback, and remote-provider isolation.
- A local ComfyUI smoke run with two short Shots verifies terminal-event
  feedback, planned tail handoff, and the ability to stop before Shot 2.

## Out of scope

Remote MiniMax API management, visual video QC, automatic video retry,
automatic resolution fallback, arbitrary custom dimensions, workflow-profile
changes, and automatic unplanned Layout generation are separate decisions.
