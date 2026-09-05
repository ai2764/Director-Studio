# Shot Revision and Tail-Frame Transition Design

## Problem

Director Studio currently has two related failure modes:

1. A request to revise one existing Shot can be routed through `save_storyboard`. The model must then reproduce the entire ordered storyboard, and any drift in an otherwise untouched Shot causes `save_storyboard` to replace that Shot and discard its downstream Layout, prompt, and H3 linkage.
2. An accepted Layout extracted from the previous Shot's tail frame can be reduced to a palette/style reference. The generated H3 prompt may begin directly on the destination image or explicitly suppress visible carryover, making the requested transition impossible even though the tail frame remains connected as a Picture.

## Goals

- Provide a native `revise_shot` tool for changing authored fields on exactly one existing Shot.
- Preserve every other Shot byte-for-byte and preserve the target Shot's Picture, Voice, and Layout bindings.
- Invalidate only the target Shot's stale prompt and current H3 linkage while retaining historical Job files.
- Treat a selected `clip_tail_frame` Layout as a visible transition handoff unless the data explicitly represents another layout type.
- Require the generated prompt to describe visible carryover in the first action interval without claiming that a Picture activates only at that time or guarantees an exact first frame.
- Apply the shared service and validation behavior to both Legacy and Harness runtimes.

## Non-goals

- Guarantee pixel-identical first-frame continuity in pure H3 Ref2VA.
- Delete or rewrite historical H3 Jobs.
- Add a general undo/versioning system for Shot JSON.
- Change the semantics of full-storyboard replacement when the user genuinely changes Shot count, order, or multiple Shots.

## Single-Shot Revision Contract

`revise_shot` accepts one `shot_id` plus a non-empty partial update containing only authored fields:

- `scene_id`
- `title`
- `script_beat`
- `shot_type`
- `camera_angle`
- `camera_motion`
- `composition`
- `duration_s`
- `dialogue`

Unknown fields and empty updates are rejected. The service loads the existing Shot, applies only fields explicitly supplied by the caller, and preserves `refs`, `voice_refs`, `layout_refs`, layout review state, asset IDs, and all unrelated Shots. Because authored direction changed, it clears `prompt_sections` and `h3_job_id`, records the superseded Job ID in Shot metadata, clears prompt signatures, and places the Shot in `needs_review` so the prompt can be rewritten and submitted again.

Legacy system guidance must route exactly-one-Shot authored changes through `revise_shot`; `save_storyboard` remains for changes to Shot count/order or coordinated changes across multiple Shots. A request that also asks for a new prompt should call `revise_shot` followed by `write_prompt`.

## Tail-Frame Transition Contract

`selected_layout_prompt_context()` exposes `origin_kind`. When a selected Layout has `origin.kind == "clip_tail_frame"`, prompt context also includes a visible-transition requirement. The H3 prompt writer must:

- retain the tail frame's observed visual state as the handoff design;
- describe visible carryover, transformation, or dissolution in the first action interval of `detailed_description`;
- avoid a hard cut directly to the destination composition;
- avoid reducing the Layout to palette, lighting, or style only;
- avoid wording that suppresses the visible source state;
- continue to state that all Pictures condition the whole clip and never assign the Picture itself a time window.

A deterministic validator runs after prompt parsing. For each selected clip-tail Layout it requires the first timed action interval to start at zero and contain a transition verb such as `continue`, `carry`, `unwind`, `dissolve`, `transform`, `morph`, `open`, `clear`, `reveal`, or `resolve`. It rejects known negating patterns such as `style only`, `palette only`, `must not manifest`, `must not be visible`, or an opening `hard cut`. Validation failure enters the existing prompt repair path; an invalid prompt is never saved or submitted.

The validator does not promise an exact first frame. It verifies that prose direction makes visible handoff possible and does not contradict the user's accepted continuity reference.

## Harness Integration

The common model, context, validation, service, schema, handler, and Legacy prompt changes land first on `codex/deepseek-harness-spike`. That commit is then cherry-picked into `codex/deepseek-harness-phase1`.

Harness adds `revise_shot` and `write_prompt` to its allowlist. Both are mutating tools and therefore require the offered `state_version`; stale calls fail before reaching the shared executor. Harness continues to use FastAPI-owned tool implementations, so it cannot diverge from Legacy revision or tail-transition validation behavior.

## Verification

- Unit tests prove tail-frame context exposes the origin and signature.
- Unit tests prove valid visible carryover passes and style-only/direct-cut prompts fail.
- Service tests prove revising Shot 2 preserves Shot 1 and Shot 3, plus Shot 2 refs/layouts/voices, while invalidating only Shot 2 prompt/H3 state.
- Native tool tests prove `revise_shot` schema, routing, result payload, and `revise_shot` + `write_prompt` guidance.
- Harness tests prove the new tools are offered, successful calls use the shared executor, and stale mutations are rejected.

