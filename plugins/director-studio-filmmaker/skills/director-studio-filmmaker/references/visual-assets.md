# Visual Assets and Layouts

Read this when the storyboard needs an asset audit or a missing visual must be
generated.

## Asset Manifest

Track each asset with:

- stable label;
- type: actor, costume, scene, prop, layout, voice, or other;
- visual or audio responsibility;
- shots that use it;
- source: user-provided or generated;
- status: proposed, generating, QC pass, revise, or approved;
- approved characteristics that later prompts must preserve.

Never infer final user approval from generation success. Asset Plan approval
authorizes the planned generation sequence; one batch acceptance after QC approves
the resulting assets for reference casting.

## Decide What to Create

Prefer reusable design assets for identity and continuity. Create a shot-specific
Layout only when composition, blocking, scale, eyelines, screen direction, set
geometry, or handled props need stronger control than the reusable references
provide.

Avoid redundant references. A coherent shot usually needs fewer strong Pictures,
not every available image.

## Layout Continuity Pass

After the storyboard is drafted and before finalizing the asset manifest, inspect
every adjacent shot pair. Compare the outgoing state of the first shot with the
intended incoming state of the next:

- subject position, pose, movement, eyeline, and screen direction;
- camera axis, framing, scale, lens feel, and camera height;
- set geography, doorway or landmark relationships, and lighting direction;
- wardrobe, prop state, hand use, contact points, and other match-action details.

Recommend a **Transition Layout Pair** when the cut depends on several of these
relationships or when a continuity failure would be conspicuous. State the risk,
what the pair must preserve, and why reusable actor, scene, or prop references are
not enough. The useful options are:

1. reuse one Layout when both shots benefit from essentially the same composition;
2. give a receiving shot both a continuity Layout and a compatible reveal Layout
   when it must inherit the previous pose/geography and also land a new composition;
3. create coordinated exit and entry Layouts when framing or viewpoint changes but
   the physical handoff must match;
4. capture an approved tail frame during production and feed it forward as a
   continuity reference for the next shot;
5. add or redesign a bridge shot when the spatial change cannot be expressed as a
   credible direct cut.

Do not prescribe extra Layouts for an intentionally discontinuous cut, montage,
or low-risk transition. Make the recommendation during storyboard review or asset
planning, before JSON export, while the user can still approve or revise the cut.

A Layout is an empirically useful soft visual anchor: a prompt can ask the action
and camera to settle into its composition at a target moment, but Ref2VA does not
guarantee a hard first frame, last frame, or keyframe. Keep the Picture binding
untimed, then describe the desired target-moment composition in a separate timed
action sentence. Two compatible Layouts may condition one receiving shot when one
owns the incoming continuity responsibility and the other owns the reveal
composition responsibility. Both apply to the whole clip; never claim that they
activate at different times.

A tail frame is dynamic production output. Do not declare it in the initial JSON
before the preceding shot has been generated, inspected, and accepted. Add it to
the next shot's references during the production loop.

## Design Images

- Actor: identity, face, hair, body, age, and stable distinguishing features.
- Costume: exact garments, layers, materials, palette, fit, and accessories.
- Scene: architecture, geography, lighting sources, period, palette, and fixed
  environmental details.
- Prop: count, proportions, material, condition, and interaction surfaces.

For an actor, costume, or identity-sensitive prop, recommend one output type and
explain why before generation:

- **three-view sheet** - front, true side, and back views at matching scale when
  later shots need reliable silhouette, wardrobe, or multi-angle continuity;
- **large front view** - a single centered full-body view when overall design is
  primary, or a large chest-up view when facial identity is primary.

Both formats are one clean source asset, not a concept board, mood board,
presentation sheet, or collection of design callouts. Isolate the subject with no
environment background, no scenery, no horizon, no explanatory text, no captions,
no labels, no arrows, no measurements, no UI, and no decorative frame.
Prefer transparency when the image tool supports it; otherwise use a plain neutral
field that is easy to discard. Keep the complete subject inside frame. On a
three-view sheet, preserve the same identity, proportions, costume, materials,
colors, and accessories across all three views. Put all written explanation in
the conversation and asset manifest, never inside the image.

## Layout Images

A Layout is exactly one standalone visual target for one shot's composition at the
project aspect ratio. It is not a storyboard grid, contact sheet, diagram, or
annotated sequence. Its brief should specify:

- aspect ratio, framing, lens feel, and camera height/angle;
- subject positions, scale, poses, eyelines, and screen direction;
- set geometry and visible landmarks;
- exact wardrobe and handled props;
- lighting direction and exposure hierarchy;
- what must remain empty or unseen.

### Reference Casting Brief

Before generating a Layout, show the user the smallest useful reference set. For
each input, state its exact responsibility and what must be ignored:

| Reference type | Responsibility | Ignore |
| --- | --- | --- |
| Scene | Environment identity, architecture, geography, palette, and lighting sources | Incidental people or temporary action |
| Actor | Face, hair, body, and identity | Turnaround canvas, catalog background, text, and pose unless requested |
| Costume | Garments, layers, fit, materials, palette, and accessories | Mannequin or catalog background |
| Prop | Exact design, count, scale, material, and interaction surfaces | Product-display background |
| Transition Layout | Approved adjacent-shot composition and continuity handoff | Treating it as a guaranteed first or last frame |

Use only real, available, user-approved references. When the shot occurs in an
established location, include the approved Scene reference and explicitly assign
it control of the complete background; actor, costume, and prop references must
not replace that environment. Add a Transition Layout only when the continuity
pass calls for it. Do not invent `ref://` URIs, tail frames, filenames, or approvals.
If the required Scene reference is missing or the selected references conflict in
identity, wardrobe, geography, lighting, or composition, stop and resolve that
problem before generation.

Diagnose a rejected result, but do not reuse it as a repair reference unless the
user explicitly asks.

## Image Quality Review

After every generation, inspect the actual output and compare it with the approved
brief and every cast reference. Give one explicit verdict: `QC pass`, `Revise`, or
`Reject`. Then report the observed evidence and the single best next action.

For design images, check:

- identity and proportions, including consistency across all three views;
- complete framing, correct view angles, wardrobe, materials, colors, and props;
- clean isolation with no environment background;
- absence of text, labels, pseudo-writing, diagrams, borders, and UI artifacts;
- face, hands, anatomy, duplicated parts, and accidental extra subjects.

For Layouts, check:

- background identity against the Scene reference before judging style;
- actor identity, wardrobe, prop design/count, and handled-object contact;
- framing, blocking, scale, eyelines, screen direction, camera axis, and geography;
- lighting direction, exposure hierarchy, empty areas, and adjacent-shot continuity;
- text artifacts, malformed faces/hands, duplicated subjects, and contradictory refs.

Generation success is not final visual approval. A passing result becomes `QC pass
/ pending user review`; failed results become `revise`. After every planned asset
passes QC, ask for one batch acceptance before changing them to `approved`. If the
image is unavailable to inspect, state that QC is pending and ask the user to
attach it.

## Picture Semantics

Every Picture conditions the entire clip. Picture order is connection order, not
time. A Layout is not guaranteed to be the opening frame, and no Picture has a
start, middle, end, insertion time, or per-image strength.
