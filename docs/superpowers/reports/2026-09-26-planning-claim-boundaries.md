# Managed planning claim boundaries

## Incident and cause

Titanic DEMO planning treated a whole-film traveler/interviewer/camera-operator
assignment as a requirement for both travelers to appear and speak in each shot.
The screenplay's on-camera wording also required interpretation alongside the
saved shot design; quote presence alone could not establish a contradiction.

Initial source validation discarded some claims, but the unfiltered previous plan
still carried them into recovery. Residual repair claims bypassed independent
review and were returned together with the initial errors.

## Change

- Share evidence validation across initial planning, repair and final review.
- Remove rejected claims from all downstream plan copies.
- Always independently review residual repair claims before treating them as blockers.
- Require final review claims to cite the candidate shot fields, not stale originals.
- Report only independently confirmed conflicts. Unsupported reviewer claims fail
  as invalid review evidence, without telling the user to resolve invented requirements.
- Clarify whole-film versus shot-specific scope and distinguish visibility, scene
  participation, camera operation and speech. No character-name or style-keyword rules.

The existing bound remains one repair proposal and one independent review. Camera
edit authority, concurrency checks and guarded publication are unchanged.

## Verification

Targeted planning suites: **70 passed** (planning claims, managed-run API, run planning).
Six new regression cases cover rejected-claim propagation, resurrection by repair,
residual-claim adjudication, unsupported final review evidence, genuine explicit
on-camera requirements, and stale quotes after candidate camera refinements.

A real local-model probe used an isolated copy of Titanic DEMO and the production
planning entry point with the new instructions and response schema. It produced a
six-shot draft with no storyboard conflicts and no camera refinements in one call.
The real project was not replanned or rewritten, and no video job was submitted.

This single model trial is evidence for the reported scenario, not a guarantee
against future semantic mistakes. Continuity choices and generated video quality
were not independently validated by this probe. Invalid reviewer evidence still
fails closed; there is no additional retry loop for it.
