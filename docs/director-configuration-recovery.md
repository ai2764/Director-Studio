# Director authoring error recovery

The Director checks saved shot parameters against explicit user instructions
before writing a prompt. These checks apply to both Harness and Legacy.

## Configuration conflicts

Material review receives voice, song, source audio and video-context bindings
alongside the shot's directing evidence. It may report `configuration_issues`
for `duration_s` or `voice_matches`; each requirement must cite supplied user
evidence. Vague pacing, empty dialogue or a person mentioned in narration is
insufficient authority to change those parameters.

`SHOT_CONFIGURATION_CONFLICT` returns affected fields to the Director. The
reviewer does not change the shot. In an ordinary chat turn, the Director may
call `revise_shot` and retry `write_prompt` once for that shot. Every reported
field must have changed; an unchanged retry or unrelated edit does not restart
the writer. A second conflict ends the turn.

Managed runs and material-editor prompt handoffs retain their existing write
scope. They report parameters that need correction in ordinary Director chat
or the shot editor instead of silently revising the plan.

## Layout acceptance

`accept_ref_frame` is available only for saved Layouts with an image asset. Its
schema lists eligible LayoutReference IDs; the handler verifies shot ownership.
Video Job IDs and pending Layouts cannot be accepted through this tool.

## Generation quality

Configuration validation does not guarantee that the model identifies every
constraint, H3 completes speech within the requested duration, or generated
audio is silent. Review the rendered video and sound separately from prompt
validation. See [architecture](ARCHITECTURE.md) for the submission path.
