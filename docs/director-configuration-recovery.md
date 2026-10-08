# Director authoring error recovery

The real six-room Agent test exposed three related failures: an explicitly
three-second ending was saved as thirteen seconds, a silent hold retained a
voice reference, and accepting a completed video called the Layout approval
tool with a video Job ID.

## Repair path

- Material review receives saved voice, song, source audio and video context
  bindings alongside the existing shot-local directing evidence.
- Its semantic verdict may report `configuration_issues` for `duration_s` or
  `voice_matches`. Each requirement must quote supplied user evidence. Vague
  pacing, empty dialogue or a character mentioned in narration is insufficient
  authority to change those parameters.
- `SHOT_CONFIGURATION_CONFLICT` returns the affected fields to the authoring
  Agent. The reviewer does not change saved shot fields. Existing cached material
  decisions are rechecked once if they predate this verdict format.
- In an ordinary Director turn, the Agent may use `revise_shot` and retry
  `write_prompt` once for that shot. Every reported field must have changed
  before re-entry. Unchanged retries and unrelated edits do not start another
  writer call; alternate shot selectors cannot bypass the check.
- A second configuration conflict ends the turn. Ambiguous creative choices,
  missing material and failed prompt repair retain their existing failure gates.
  Managed runs and material-editor prompt handoffs retain their write scope and
  cannot silently revise the plan. They report that the saved parameters need
  correction in an ordinary Director turn or the shot editor.
- Both the legacy and Harness runtime use the same repair budget. Tests exercise
  real `revise_shot` storage writes, clearing references, preserving neighbors,
  and handing the failure back to the model before the next tool call.

## Layout acceptance

`accept_ref_frame` is offered only when a saved Layout has an image asset. Its
schema enumerates actual eligible LayoutReference IDs. A fresh context after
extraction or generation exposes the new ID. Video Job IDs and pending Layouts
cannot be accepted through this tool; the handler still verifies shot ownership.

## Remaining limits

This changes authoring recovery, not the video model. It does not guarantee
that the Agent always identifies every constraint correctly, that H3 respects
speech timing, or that a zero-reference silent prompt produces silent audio.
The previously observed unwanted spoken outro is still a generation-quality
issue. Dialogue lost in a trimmed context prefix still needs a fixed-seed
comparison of raw and trimmed audio before a pipeline fix is justified.

Other observations from the production test remain separate follow-up work:
visible-actor casting versus people mentioned in narration, retaining explicitly
required head references, preserving required continuity after a frame-budget
error, and detecting implausible speech windows. This patch does not claim those
issues are fixed.
