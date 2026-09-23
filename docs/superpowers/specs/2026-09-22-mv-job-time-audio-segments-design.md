# MV Job-Time Audio Segments Design

## Goal

Add one final, centralized preparation step to H3 submission: for a Music Video project, a Shot may name a timestamp window in the project's single song master, and every submission entry point materializes that window as Job-local `<Audio 1>` immediately before the H3 Job is created.

The feature must not change normal Director projects or their existing Voice reference behavior.

## Product intent

- An MV Project imports one authoritative song master.
- The local Agent plans visual Shots and records song timestamps only when readable lips need synchronization.
- Audio-free cutaways and transitions continue to submit without an audio reference.
- Song segments are not pre-cut and are not added to the Library as Voice assets.
- Manual Production submission, Agent one-off submission, managed runs, and direct API submission all use the same preparation path.
- The actual Job input is retained with the Job for reproducibility.
- H3 audio is generation guidance, not the final editorial soundtrack. A later editing stage uses the original master and the core intervals.

## Mode isolation

The new behavior is gated by both conditions:

1. `project.mode == "mv"`.
2. `shot.music_segment` is present.

When either condition is false, submission follows the existing implementation exactly:

- `voice_refs` keep their current `Audio 1`–`Audio 3` numbering.
- Existing Voice asset validation is unchanged.
- Prompt voice signatures are unchanged.
- Frame calculation is unchanged.
- No song-master lookup, probe, extraction, or conversion runs.

A Shot with both `music_segment` and `voice_refs` is rejected with an actionable validation error in the first version. The system must not silently shift Voice indexes or reinterpret a normal Voice reference.

Non-MV projects may load legacy JSON containing no new fields. Attempts to assign an MV music segment to a non-MV Shot are rejected by the project API and Agent planning path.

## Data model

### Project music master

`Project` gains an optional `music_master` value:

```python
class ProjectMusicMaster(BaseModel):
    filename: str
    relative_path: str
    duration_s: float = Field(gt=0)
    content_sha256: str
    source_format: str
```

The imported file is stored below the project root, for example:

```text
data/projects/<project_id>/music/master.wav
```

Persist only a project-relative path. Never persist the source computer's absolute upload path.

Existing project JSON defaults `music_master` to `None`.

### Shot music segment

`Shot` gains an optional `music_segment` value:

```python
class ShotMusicSegment(BaseModel):
    core_start_s: float = Field(ge=0)
    core_end_s: float = Field(gt=0)
    submit_start_s: float = Field(ge=0)
    submit_end_s: float = Field(gt=0)
```

Validation rules:

- `core_start_s < core_end_s`.
- `submit_start_s < submit_end_s`.
- The submit interval fully contains the core interval.
- When a music master is available, `submit_end_s` must not exceed its duration, allowing only a small probe-tolerance margin.
- The submitted generation duration is `submit_end_s - submit_start_s`.

The core interval describes the portion needed in the final edit. The submit interval includes pre-roll and post-roll handles and drives H3 generation duration.

Existing Shot JSON defaults `music_segment` to `None`.

## Import and replacement

An MV-only multipart endpoint imports or replaces the project song master. It:

1. Rejects non-MV projects.
2. Enforces the existing upload-size policy and supported audio suffixes.
3. Copies the original upload into the project `music/` directory.
4. Probes duration and format.
5. Computes SHA-256.
6. Atomically updates `Project.music_master` only after the stored file validates.

Replacement must not rewrite Shot intervals. If an existing interval exceeds the new master, the Project may still load and be edited, but submission fails with the exact invalid Shot and interval.

The MV UI exposes import/replace controls and displays the stored filename and duration. The old test project can import `Anywhere Will Do (Remix).wav` without creating a Voice Library entry.

## Agent planning contract

MV planning schemas accept optional `music_segment` data for each new or revised Shot. The injected MV guide tells the Agent:

- Set `music_segment` only for readable singing, yodeling, or speech that must synchronize to the song.
- Use the authoritative timestamps supplied by the project or user.
- Preserve the core interval.
- Prefer approximately 0.5 seconds of pre-roll and 0.75 seconds of post-roll when the master boundary permits, then adjust around breaths, performer changes, phrase boundaries, and provider limits.
- Leave `music_segment` absent for cutaways, distant or rear views, environments, objects, and reactions without readable lips.
- Never create or request a Voice Library asset for a song segment.

Saving or changing `music_segment` invalidates the saved MV audio-prompt signature. It does not modify the normal Voice reference signature.

## Prompt contract

For an MV Shot with `music_segment`:

- The effective audio count is exactly one.
- The six-section H3 prompt must bind `<Audio 1>` as the submitted song excerpt and synchronize the readable performance to it.
- The prompt must not claim that H3 preserves the source waveform exactly.
- Prompt timing is relative to the submitted generation interval, beginning at `0.0`.

Changing the master hash or any of the four interval values makes the prompt stale. The canonical submit preflight uses the existing prompt-refresh mechanism before Job creation. If refresh fails, submission stops before creating a Job.

For every other Shot, the existing `voice_refs` prompt contract is unchanged.

## Canonical submission preparation

The song-segment logic lives below all submission callers in the canonical Shot submit implementation, not in the Agent tool.

Callers remain thin:

```text
Agent start_h3_video ─┐
Production Submit H3 ─┼─> canonical submit preflight
Managed run ──────────┤
Direct REST API ──────┘
```

The preflight selects exactly one audio path:

```text
MV + music_segment
    -> project song master -> Job-time segment preparation -> Audio 1
otherwise
    -> existing voice_refs behavior, unchanged
```

For an MV segment, a focused media helper:

1. Resolves the project-relative master path safely within the project root.
2. Revalidates interval bounds against the stored duration.
3. Invokes ffmpeg with the submit start and duration.
4. Produces PCM WAV, 32 kHz, stereo.
5. Returns the exact bytes and filename for Job input staging.

The canonical submit code stages the result as one audio input, records it in the Job input snapshot, validates the prompt with `audio_count=1`, and computes frames from the submit interval duration. No Library record or project-level segment file is created.

Extraction failure, missing ffmpeg, unreadable master, invalid interval, or incompatible simultaneous `voice_refs` must return a clear 4xx submission error before a durable H3 Job starts.

## Legacy compatibility

- `voice_refs` remain supported without behavioral changes in every project mode.
- `source_audio_path` remains readable for legacy data but is not the new MV path.
- Existing projects and Shots require no migration to load.
- The manually created Shot 1 Voice asset in the old test project is a test-era fallback. When Shot 1 receives `music_segment`, its `voice_refs` must be cleared explicitly before submission; the system must not delete the Library asset automatically.

## UI behavior

For MV projects only:

- Project controls display the song master filename and duration.
- The user can import or replace the master.
- Shot details display core and submit intervals when present.
- The user can correct the four values manually.
- Production submission requires no additional audio checkbox or preparation action.

Normal Director and JSON Production interfaces do not show the MV song-master or segment controls.

## Test strategy

### Model and persistence

- Legacy Project and Shot JSON load with new fields absent.
- Valid segment ranges persist and round-trip.
- Invalid ordering or containment is rejected.
- MV planning saves segment data; non-MV planning rejects it.

### Import

- MV upload stores a project-relative master with duration and hash.
- Non-MV upload is rejected.
- Invalid audio does not change an existing master.
- Replacement is atomic.

### Submission isolation

- MV plus a segment stages one Job-local 32 kHz stereo `Audio 1` and uses submit duration for frames.
- The same behavior occurs through manual REST submission and Agent `start_h3_video`, proving both reach the canonical path.
- MV without a segment stages no song audio.
- Normal Director Voice refs produce byte-for-byte equivalent Job parameters and numbering to the pre-feature behavior.
- MV segment plus Voice refs is rejected before Job creation.
- Missing master, out-of-range interval, ffmpeg failure, and stale prompt failure create no H3 Job.

### UI

- Song controls appear only in MV mode.
- Import/replace refreshes Project state.
- Shot interval editing persists all four values.
- Manual Submit H3 requires no separate audio preparation step.

### End-to-end test

Using the copied `MV Agent Test — Anywhere Will Do Opening` project:

1. Import the original song master once.
2. Replace Shot 1's temporary Voice binding with its planned music segment.
3. Ask the local Agent to refresh the prompt and explicitly generate Shot 1.
4. Verify the Job input contains only the expected excerpt as `Audio 1`.
5. Verify a manual resubmit uses the same preparation path.
6. Verify an audio-free Shot submits without a generated audio input.

## Out of scope

- Final MV timeline assembly or muxing the original song onto rendered Shots.
- Waveform editing UI.
- Caching reusable segment files across Jobs.
- Mixing song segments with Voice Library references in one Shot.
- Automatic transcription or timestamp discovery.
