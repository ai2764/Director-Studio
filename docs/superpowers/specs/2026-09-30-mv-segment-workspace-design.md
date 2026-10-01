# MV Segment Workspace Design

## Purpose

Make the original song and an externally prepared, timecoded transcript the starting point for Music Video projects. A user uploads the song in Assets, imports segment text through one flexible entry in Music, then selects a segment or adjacent segments to discuss visuals and produce Shots with the Director Agent. The original song remains the authority for sound and timing. Imported words are working text and can be corrected during production discussion.

The workflow must not add Whisper, another transcription engine, or an automatic transcription dependency to Director Studio.

## Current state

- MV projects already have one `music_master` upload. Today the Director page shows its filename and duration and exposes the upload control; the new UI moves that control to Assets.
- `Project.script_text` is freeform. The older Anywhere Will Do project embeds timecoded lyrics, visual rules, and production instructions in that one field. There is no dedicated segment importer, song timeline, or segment-scoped Agent discussion.
- `Shot.music_segment` already carries core and submit intervals for source-song Audio 1 at H3 job time. It is distinct from Voice Library references. The UI does not yet expose segment timing or a relationship between song units and Shots.
- Assets, Director, and Production reuse the non-MV navigation. Existing project data must remain loadable.

## Product flow

For `mode == "mv"`, show **Assets → Music → Director → Production**. Other project modes retain their current navigation. Assets owns media intake; Music owns segment preparation and segment-led discussion.

1. In the MV-only Music category of Assets, import or replace the original song using the existing Music master operation. This is one Project song master, not a Voice Library asset. Show its filename and duration there.
2. Use one **Import segments** action. The same panel accepts pasted text or a text file. The user can supply prose, a table, CSV, LRC/SRT-like text, or the existing Whisper JSON structure. There are no separate format-specific user flows.
3. When the user requests a preview, the existing configured LLM converts that text to a draft of ordered segments. Opening the panel does not call the model or change the project. Display the parsed result alongside the supplied text, with editable start, end, and words. Validate it against the song before saving.
4. Save the confirmed segments as structured project data. Show them as proportional blocks against the song duration; unannotated spans remain visible as unannotated time, without an assumed lyric or instrumental label. The user may import only a portion of the song.
5. Selecting one segment or adjacent segments scopes the Director conversation. The Agent proposes visuals and Shots for that selection, and stops after the current user-reviewed batch. A segment may support multiple Shots; a Shot may reference multiple adjacent segments. Segment boundaries are source facts, not compulsory Shot cuts.
6. During discussion, the user may correct imported words or times. The saved segment changes explicitly; the song file does not. The UI shows linked Shot status and identifies Shots whose source assumptions need review after a segment correction.
7. Production uses the existing Shot and H3 flow. Only a face-readable singing or speaking Shot receives a `music_segment` and job-time source-song Audio 1. Other Shots may visually cover a segment without generation audio. The final edit continues to use the original song.

## Segment data and authority

Persist a separate, versioned segment document under the MV project's `music/` data, rather than placing the transcript in `script_text`. Its canonical fields are:

```json
{
  "revision": 1,
  "master_sha256": "sha256-of-current-song-master",
  "segments": [
    {
      "id": "seg_stable_id",
      "start_s": 4.54,
      "end_s": 13.08,
      "text": "Yodelie, yodelie, yodelie, yodelie."
    }
  ]
}
```

IDs remain stable for ordinary edits. Every save produces a new revision; older revisions remain recoverable so existing Shot links are not silently rewritten. A Shot records the segment IDs and the content signature of each segment used when it was planned, separately from its existing `music_segment` generation interval. The signatures identify which linked Shots need review after a change without making unrelated Shots stale. This link is planning provenance, not an alternative audio source. Retain the original imported text as project-local provenance, and never treat it as executable instructions.

The source-song timestamp is always relative to the imported original master. Do not introduce final-edit timestamps in this phase. The UI never substitutes a pre-cut WAV or the transcript for the master when preparing H3 audio. The existing `script_text` remains available for creative direction and legacy projects; a new MV project does not need lyrics duplicated into it.

## Import and validation

The LLM parser returns only proposed segment records and concise unresolved items. It must preserve explicit wording and timestamps, not transcribe audio, infer missing lyrics, or invent timecodes. A deterministic validator runs after parsing and again before persistence:

- Each saved row has finite `0 <= start_s < end_s <= master.duration_s` values.
- Segments are presented in source-time order. Overlaps and duplicate ranges are surfaced for review; the importer does not silently merge or discard rows.
- Gaps and incomplete song coverage are allowed. A missing timestamp stays unresolved until the user supplies it.
- The displayed preview is what gets saved. A failed parse or validation leaves the existing segment revision untouched.
- File input is handled as text within a bounded size; unsupported binary input gets a clear error. The file is not uploaded to any transcription service.

Replacing the song master preserves segment revisions and Shot work but marks segments tied to the previous master hash, and their linked Shots, as needing review. Re-importing or editing segments similarly marks only affected linked Shots for review. No existing Shot, prompt, or generated Job is silently deleted or retimed.

## Interface

The MV-only Assets Music category contains the only song import/replace control. The Music workspace has three connected regions:

- **Song header:** master filename, duration, playback, a link to manage the master in Assets, and the single Import segments action. It does not duplicate song upload.
- **Song map:** a duration-scaled strip and accessible segment list. Each block shows time, text preview, and derived progress (unplanned, planned, in production, or produced). Selecting a block seeks to its start; playback can be limited to the selected range.
- **Segment work area:** exact text and timing, editing controls, linked Shots, and the existing Director chat scoped to the selection. On narrow screens, the list and chat become sequential panels with the active segment kept visible in the chat header.

The Agent receives the selected segment records, their revision, the master identity/duration, relevant neighboring context, and existing linked Shots. It may propose or revise creative work and may apply a user's explicit lyric correction. It does not silently alter imported song facts. A conversation about one selection must not automatically advance through the rest of the song.

## Failure and compatibility behavior

- Music Video projects without a song or segments remain open and editable; Music shows the missing-song state and a route to Assets Music. Segment import can be drafted before song upload, but final save requires a master for duration validation.
- If the configured LLM is unavailable, the import panel retains the supplied text and reports that parsing is unavailable. Existing saved segments and production remain readable.
- Existing MV projects and Shots with only freeform script timestamps continue to load. There is no automatic migration or overwrite; the user can import the old source text when ready.
- Non-MV projects never receive the Music workspace or segment-only API behavior.
- Changes to segment text do not mutate a completed Job. A new generation after an affected change must use current, reviewed Shot timing and prompt state.
- For a custom local H3 workflow, inspect and validate the route for a job-time song excerpt as Audio 1 before an MV Shot is submitted. The current custom-profile validator's zero-audio synthetic fill alone is insufficient proof that a singing Shot can use the profile. Report an unsupported or ambiguous audio route before queueing rather than silently using a workflow-owned fixed audio file. Existing no-audio custom workflows continue to support visual cutaways.
- The three H3 audio-reference positions are ordered generic inputs, not dedicated music or timbre slots. In this phase DS submits the song excerpt as Audio 1 for face-readable singing Shots; role comes from the prompt. Reference audio does not guarantee an exact copy of the source song, so the final edit still uses the master.

## Acceptance checks

- The Anywhere Will Do `audio/whisper.json` can be supplied through the single entry and previewed as sentence-level blocks without running Whisper in Director Studio. Its optional word records do not have to become production data.
- MV song upload and replacement are available from Assets Music on desktop and mobile, with no duplicate upload control in Music or Director. The song remains a Project master, not a Voice Library reference.
- Pasted human-written timecoded prose follows the same preview and save path.
- A 325.12-second song validates boundaries; partial coverage and gaps remain visible; malformed or out-of-range rows cannot be saved silently.
- Selecting a segment scopes Agent context and allows a batch to create multiple linked Shots without forcing one Shot per segment.
- Text corrections during discussion update the saved segment, retain the original song, and flag linked Shots that require review.
- A readable singing Shot still uses the existing master-to-job Audio 1 path; a non-singing cutaway submits without song audio.
- A custom H3 profile that claims Audio 1 support passes a one-audio boundary validation and receives the prepared master excerpt in the intended input. A profile without a valid Audio 1 route remains usable for cutaways and gives a clear error for singing Shots before a Job starts.
- Existing Director and JSON Production behavior and legacy MV data remain intact.

## Outside this phase

- Automatic transcription, Whisper installation, or word-level alignment editing.
- Dedicated CSV, LRC, SRT, or Whisper-specific import buttons.
- A full audio workstation, source-song editing, or final timeline assembly and muxing.
- Automatic rewriting of imported lyrics, song boundaries, or previously approved Shots.
- Rebinding a custom workflow's independent `LoadAudio` node to the Project song. This phase verifies and uses the H3 `ref_audios` reference input.
