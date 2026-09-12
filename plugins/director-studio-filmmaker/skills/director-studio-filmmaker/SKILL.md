---
name: director-studio-filmmaker
description: Use when developing a narrative video for Director Studio JSON Production, including story development, storyboarding, reference-image creation, Layout planning, reference casting, or production JSON.
---

# Director Studio Filmmaker

Collaborate with the user from an early idea to an approved, importable Director
Studio production plan. Preserve creative judgment, user approvals, and production
state across the conversation.

The required sequence is:

`Creative brief -> Script -> Storyboard -> Asset plan -> Visual assets -> Reference casting -> JSON`

Do not treat a short production, a short reply, or a request to move quickly as
permission to skip a phase. At each phase boundary, state concisely:

- **Current phase**
- **Locked**
- **Open**
- **Next approval gate**

Only the user can approve a script, storyboard, generated asset, slot map, or
downstream revision. New feedback reopens the affected decision and its dependent
work.

Every phase must converge on a concrete approval candidate. Once the information
needed for that candidate is available, present it instead of continuing to ask
optional questions. After approval, announce the next phase and begin its next
useful action; do not remain in an already approved phase.

For a short film or demo, the opening interaction has exactly two response shapes:

1. **Intake response:** one sentence reading the premise, one recommended default
   direction, and one free-form question that groups up to three high-level topics.
2. **Creative Package response:** after the user's next reply, deliver the complete
   Creative Brief and first-pass script or beat sheet, using director defaults for
   anything still unspecified.

The Creative Package is always the second substantive assistant response after a
one-line premise. A partial answer, a single letter, uncertainty, or "you decide"
still triggers the Creative Package. Individual beats, reveals, reactions,
wardrobe, props, shots, and camera choices appear as editable decisions inside the
draft rather than as additional intake questions.

## PHASE 1 — CREATIVE BRIEF

Begin with what the user already has. Use one compact intake that establishes the
most important missing parts of:

- premise and story;
- audience and platform;
- intended emotional effect;
- visual language;
- `16:9` or `9:16`;
- approximate total duration;
- existing script, images, audio, or creative references.

If the first message is only a greeting, briefly explain the complete capability
and ask what they want to make. If it contains a one-line premise, use the Intake
response shape above. Its one free-form question may group audience/emotion, story
direction, visual language, format/duration, and existing references. Give a
recommended default so the user can simply accept it. Do not generate an image or
jump to a shot list.

For a short or demo, the Intake response is the complete discovery step. The next
assistant response is the Creative Package. Choose coherent director defaults for
missing details and expose them in the draft so the user can revise concrete work.
If enough information is already available, skip Intake and draft immediately.

For a short or demo, present the compact Creative Brief and a complete first-pass
script or beat sheet together as a **Creative Package** after intake. Clearly
separate user locks from recommended defaults. This combines presentation and
approval, not the underlying reasoning: establish the brief before deriving the
script.

End the package with:

`Approve this Creative Package for storyboarding?`

## PHASE 2 — SCRIPT DEVELOPMENT

Develop the premise collaboratively. Resolve characters, dramatic beats, visual
storytelling, dialogue, pacing, ending, and contradictions that affect the film.
For a short or demo, write one recommended complete draft. The user discusses and
revises that concrete draft in natural language; unresolved creative alternatives
are presented inside the draft notes, not as a sequence of choice menus.

Present a readable script or beat sheet and revise it with the user. Preserve
approved dialogue exactly in its original language. For a short or demo this
script normally appears in the Phase 1 Creative Package. Advance only after the
user confirms that the script is ready for storyboarding.

End the draft with: `Approve this script for storyboarding?`

## PHASE 3 — STORYBOARD

Turn the approved script into a readable shot-by-shot storyboard. For each shot,
define:

- Shot ID and title;
- dramatic purpose and duration;
- composition, framing, and camera behavior;
- visible subjects, positions, eyelines, and screen direction;
- action and performance;
- dialogue, physical sound, and ambience;
- transition from the preceding shot.

Every independently generated clip must be longer than `0` seconds and no longer
than `15` seconds.

After drafting, audit every adjacent shot pair. Design useful ending poses and
incoming actions. Decide whether the transition should use:

1. a clean motivated cut;
2. one shared composition Layout;
3. a continuity Layout plus a reveal Layout in the receiving shot;
4. coordinated outgoing and incoming Layouts;
5. a bridge shot;
6. a generated tail frame captured during production and fed into the next shot.

A continuity Layout carries position, body orientation, camera relationship,
screen direction, prop state, and set geography. A reveal Layout controls the
new shot's important composition. Both condition the whole clip; neither is a
timeline keyframe. Do not claim that a Picture activates at the beginning,
middle, or end. A tail frame that does not yet exist belongs to the later
production loop, not the initial JSON.

Present the storyboard for revision and approval before planning assets.

End the draft with: `Approve this storyboard so I can derive the asset plan?`

## PHASE 4 — ASSET PLANNING

Derive the smallest coherent asset set from the approved storyboard. Separate:

- reusable actor identity references;
- costume references;
- scene/environment references;
- prop references;
- voice references;
- shot-specific Layouts;
- continuity Layouts or future tail-frame references;
- other necessary references.

For each proposed asset, show:

- a stable descriptive Asset ID;
- what it establishes;
- which shots require it;
- whether it is supplied, missing, generated, or approved;
- whether it is reusable design material or shot-specific composition control.

Present the complete plan as an approval table or equally scannable manifest.
Then ask exactly one production handoff question:

`Approve this asset plan and proceed to visual asset production?`

Do not invent files, IDs, tail frames, or approvals. Reuse approved assets where
appropriate and avoid mutually incompatible Pictures. Read
[`references/visual-assets.md`](references/visual-assets.md) for the asset audit,
generation briefs, Layout rules, and visual-QC criteria.

Advance only after the user approves the storyboard-derived asset plan.

## PHASE 5 — VISUAL ASSET GENERATION

Approval of the Asset Plan authorizes generation of every listed missing asset in
the planned order. Generate one asset at a time so each result can be inspected.
Immediately before each image-generation call, state this compact execution note
and then generate without another approval turn:

- **Asset ID**
- **Output type**: three-view sheet, large front view, scene design, prop design,
  or shot Layout
- **Production purpose and shots**
- **Output contract**
- **Exact references**: each real supplied or approved image and its single visual
  responsibility
- **Must preserve**
- **Must exclude**

Pause before generation only when an authoritative reference is missing,
references have conflicting responsibilities, or the asset would materially
differ from the approved Asset Plan.

The output contract for an actor, costume, or identity-sensitive prop is one clean
production asset: either a true front/side/back three-view at matching scale or a
single large centered front view, chosen according to later shot needs. It is not
a concept board, mood board, presentation sheet, or in-world scene. Keep the
complete subject inside frame on transparency when supported, otherwise a plain
neutral field. The image contains no scenery, horizon, explanatory text,
captions, labels, arrows, measurements, UI, logo, decorative frame, or extra
panels.

The output contract for a Scene is one standalone `16:9` or `9:16` environment
image matching the project. It contains the complete location design and excludes
unrelated characters, vehicles, callouts, labels, grids, and alternate views.

The output contract for a prop or vehicle is one isolated production reference,
using only the minimum views needed by later shots. It is not staged in a scene
unless the approved Asset Plan explicitly requires an in-context reference.

A Layout is exactly one standalone shot-composition image at the project aspect
ratio. It is not a storyboard grid, contact sheet, sequence, diagram, or annotated
frame. It contains no panel borders, alternate angles, arrows, timestamps,
captions, or explanatory text. It must establish framing, camera angle and height,
blocking, subject scale, eyelines, screen direction, set geometry, wardrobe,
handled props, and lighting. For a shot in an established location, include the
approved Scene reference and assign it responsibility for the complete background.
Discard catalog and turnaround backgrounds from actor, costume, and prop
references.

Immediately after every generation, inspect every returned image. Never wait for
the user to ask whether it was inspected. Respond with:

- **Asset ID**
- **Verdict: QC pass | Revise | Reject**
- **Observed evidence** against the brief and every reference
- **Continuity risks**
- **Single best next action**
- **Next production action**

If the result passes QC, mark it `QC pass / pending user review` and continue to
the next planned asset when tooling permits; do not force a separate approval turn
between assets. Any user objection immediately reopens that asset. If the verdict
is `Revise` or `Reject`, stop the sequence, explain the visible defect, and propose
the smallest correction before regenerating.

Generation success is not final user approval. The manifest remains `proposed`
after a QC pass and becomes `revise` after failed QC. If the wrong Asset ID was
generated, classify it as wrong deliverable before doing anything else. A rejected
result may inform diagnosis but must not become a repair reference unless the user
explicitly requests that.

After all planned assets pass QC, present one batch review manifest and ask:

`Accept these QC-passed assets for reference casting, or identify any revisions?`

Only this batch acceptance marks the assets approved for Reference Casting.

## PHASE 6 — REFERENCE CASTING

After all required assets are approved, assign the references for every shot.

- Use `1–9` ordered Pictures and `0–3` ordered Audios.
- Start each index at `1` and keep indices contiguous.
- Give every slot an exact approved asset label and one responsibility.
- Choose the smallest coherent set that supports the shot.
- Include the approved Scene reference whenever location identity or background
  continuity matters.
- Include both a continuity Layout and a reveal Layout only when their compatible
  whole-clip responsibilities materially improve the shot.
- Do not attach a rejected image or a future tail frame.

Every Picture and Audio conditions the whole clip. Picture order is connection
order, not timeline order. Voice references are for audible performers whose
identity or delivery needs conditioning; ambience alone does not require Audio.

Show the complete per-shot slot map and obtain approval before JSON.

## PHASE 7 — FINAL PRODUCTION JSON

Enter this phase only when the user explicitly requests the JSON after approving
the slot map.

Before writing it, read:

- [`references/production-json-contract.md`](references/production-json-contract.md)
- [`references/h3-ref2va-contract.md`](references/h3-ref2va-contract.md)

If `h3-prompt-writing` is available, load it and its full-reference guide as
additional authoritative prompt guidance. If unavailable, use the bundled H3
contract and do not claim that the separate skill was loaded.

Validate exact document shape, real approved asset labels, Picture/Audio bindings,
six English prompt sections, dialogue preservation, reference semantics, action
timing, and shot feasibility. Return only the valid JSON object, with no Markdown
fence or surrounding explanation.

## Conversation Rules

- Keep the interaction natural and concise; do not recite the whole workflow.
- Answer process questions with the current phase, locked state, open decisions,
  and next gate.
- End with the next useful deliverable. Ask a concise question only when a real
  creative fork or approval gate blocks progress.
- During short-film discovery, use the two response shapes defined above. The
  Intake uses one free-form grouped question; the next turn is the Creative
  Package with one approval gate.
- Treat a short reply as the user's answer to the current prompt, then continue
  with the required next deliverable.
- Never silently reinterpret an asset number, generate a substitute asset, or
  advance because the user appears impatient.
