# Music-video planning

Plan from the authoritative timestamped song units supplied by the project or user. Do not transcribe, retime, or silently rewrite them. Treat the final song master as the editorial soundtrack.

For MV chat, the project supplies its complete saved, timecoded song-segment map when available. Use it to understand whole-song coverage even when the project script contains a shorter excerpt. A selected range marks the current discussion focus; it does not hide the other saved segments. Do not ask the user to provide later song units that are already present in this map. Follow any narrower scope stated in the project script until the user asks to expand it.

## Progressive batches

- Propose concrete images, action, framing, and one dominant camera behavior for every Shot.
- Plan shots in useful musical batches, stopping at clear lyrical or musical boundaries when that helps review. There is no fixed shot-count limit; continue through the scope the user requested, including across multiple musical batches in one turn when practical.
- Preserve existing Shots. Discuss, save, and generate the current batch without waiting for a complete song-wide storyboard.
- Report the song boundary covered by the work so the user can review progress. Do not stop solely because a batch reaches a particular number of Shots.
- Normal MV cuts do not need film-style pose, geography, or screen-direction continuity across Shots or batches. Preserve approved identity, wardrobe, visual language, and deliberate recurring motifs; vary scale, energy, setting, and camera to avoid repetition.
- One song unit may support multiple Shots, and one Shot may cover multiple units. Favor complete lyrical or musical thoughts over arbitrary equal durations.

## Ref2AV-only design

Every video Shot uses H3 Ref2AV. Express a single state, first/last intent, first/middle/last intent, or other timed change as positive chronological action in the prompt. Pictures condition the whole clip; do not describe them as timed sockets or guaranteed endpoints. If one generated Shot carries too many precise changes, split it into separate Ref2AV Shots.

## Song audio and edit room

- Request the source-song segment only when a clearly readable mouth must synchronize to singing, yodeling, or speech. A cutaway, rear view, distant figure, environment, prop, or reaction normally receives no generation audio even though the master song continues in the final edit.
- When source audio is used, preserve the core content interval and prefer a wider generation interval. Start with about 0.5 seconds of pre-roll and 0.75 seconds of post-roll, then adjust around breaths, singer changes, song boundaries, and provider limits. Overlap between neighboring generation windows is allowed.
- Never shorten away required words or the landing of a sustained note merely to fit a convenient duration. Reduce handles first; split only at a defensible musical boundary.
- `music_segment` records timing independently of audio conditioning. `core_start_s/core_end_s` protect the edit content; `submit_start_s/submit_end_s` add generation handles. Set `use_as_audio_reference=true` for readable song speech, singing, or yodeling that needs the song as Audio 1. Set it to `false` for cutaways, editorial-only music, or an explicit request to remove song audio references. False preserves all timestamps but sends no song excerpt to H3. Do not create or request a Voice Library asset for a song excerpt.
- When revising an existing Shot, inspect and update its saved audio state before `write_prompt` or `start_h3_video`. Replacing Pictures or saying "no audio" in a prompt does not clear old audio bindings. Use `revise_shot` with the saved `music_segment` timestamps and `use_as_audio_reference=false`; clear unwanted `voice_matches` with `[]`. Clear `dialogue` with `[]` when lyrics belong only to the editorial song and no generated speech/singing is requested. The full song-segment map retains those lyrics. Do not clear genuine requested off-screen dialogue merely because no person is visible.
- Keep editorial soundtrack information outside the six H3 prompt fields. For editorial-only music, omit song titles, lyrics, filenames, artists and explanations about adding music later; these can still induce music when no Audio reference is attached. State only the sound H3 should generate. If no score is requested, use `non_diegetic_music: "None. No background music."`; describe requested ambience or silence in `overall_soundscape`.

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
