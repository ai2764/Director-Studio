# Music-video planning

Plan from the authoritative timestamped song units supplied by the project or user. Do not transcribe, retime, or silently rewrite them. Treat the final song master as the editorial soundtrack.

## Progressive batches

- Propose concrete images, action, framing, and one dominant camera behavior for every Shot.
- Plan 1–10 new Shots per batch. Stop at a useful musical boundary; ten is a limit, not a target.
- Preserve existing Shots. Discuss, save, and generate the current batch without waiting for a complete song-wide storyboard.
- Finish only the current batch in one turn. After its discussion, save, or generation work, report the covered song boundary and stop for user review; do not append the next batch in the same turn even when the user requested more than ten Shots overall.
- Normal MV cuts do not need film-style pose, geography, or screen-direction continuity across Shots or batches. Preserve approved identity, wardrobe, visual language, and deliberate recurring motifs; vary scale, energy, setting, and camera to avoid repetition.
- One song unit may support multiple Shots, and one Shot may cover multiple units. Favor complete lyrical or musical thoughts over arbitrary equal durations.

## Ref2AV-only design

Every video Shot uses H3 Ref2AV. Express a single state, first/last intent, first/middle/last intent, or other timed change as positive chronological action in the prompt. Pictures condition the whole clip; do not describe them as timed sockets or guaranteed endpoints. If one generated Shot carries too many precise changes, split it into separate Ref2AV Shots.

## Song audio and edit room

- Request the source-song segment only when a clearly readable mouth must synchronize to singing, yodeling, or speech. A cutaway, rear view, distant figure, environment, prop, or reaction normally receives no generation audio even though the master song continues in the final edit.
- When source audio is used, preserve the core content interval and prefer a wider generation interval. Start with about 0.5 seconds of pre-roll and 0.75 seconds of post-roll, then adjust around breaths, singer changes, song boundaries, and provider limits. Overlap between neighboring generation windows is allowed.
- Never shorten away required words or the landing of a sustained note merely to fit a convenient duration. Reduce handles first; split only at a defensible musical boundary.
- Until the project exposes a real source-audio binding, record the exact core and proposed generation intervals in the Shot plan and state plainly that audio is not yet attached. Do not substitute a Voice asset.

## Visual proposals and assets

First propose the visual content that serves the lyric or musical beat. Then identify the minimum evidence needed to produce it.

1. Reuse suitable approved Library assets and exact file keys. Inspect an asset when its visible contents are uncertain.
2. If the idea depends on a specific real person, proprietary design, brand, location, or continuity image that the user may own, ask the user to provide it.
3. If the missing material can be designed and the user requested or authorized generation, use the currently offered DS tool: local Actor design for a new performer, or local Layout generation for a Shot composition. GPT generation requires an explicit GPT request.
4. If generation is not yet authorized, name the missing asset and offer the user the choice to upload it or generate it. Do not invent asset IDs, file keys, tool availability, or a successful result.

Generated images remain subject to normal human review. A planning request alone does not require generating every proposed asset, and a missing asset does not prevent the Agent from giving a useful visual proposal.

## Batch plan shape

For each proposed Shot state: source content interval and lyric/beat, generation interval when different, duration, visual content, framing, subject action, camera behavior, whether readable lip sync requires source audio, required existing assets, and any missing-material action.

Example: a three-phrase yodel may use one face-readable performance master with source audio and edit handles, plus two audio-free cutaways. The performance source protects sync; the cutaways provide scale and visual contrast without forcing exact mouth motion.
