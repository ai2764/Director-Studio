# Director Studio JSON Production Contract

Read this only after the script, storyboard, assets, and per-shot slot maps are
approved and the user explicitly requests final JSON.

Return one valid JSON object with no Markdown fence or surrounding explanation.

## Document

```json
{
  "version": 1,
  "revision": 0,
  "aspect_ratio": "16:9",
  "shots": []
}
```

- `version` must be `1`.
- `revision` must be an integer `>= 0`; use `0` for a new import.
- `aspect_ratio` must be `"16:9"` or `"9:16"`.
- Shot IDs must be non-empty and unique.

## Shot

```json
{
  "id": "shot_001",
  "title": "Corridor entry",
  "script_beat": "Lu enters the archive and senses that she is being watched.",
  "duration_s": 6,
  "dialogue": [],
  "pictures": [
    {
      "index": 1,
      "role": "actor",
      "label": "Approved Lu identity and navy wardrobe reference"
    }
  ],
  "audio": [],
  "prompt": {
    "subject_definitions": "<Picture 1> defines Lu's approved identity and wardrobe.",
    "summary": "A six-second reference-guided corridor entrance.",
    "retention_analysis": "Preserve Lu's identity, navy wardrobe, and restrained performance.",
    "detailed_description": "0-6 seconds: Lu enters the corridor, slows, and stops beside the archive desk as the camera makes a restrained forward push.",
    "overall_soundscape": "Quiet rain outside and a steady fluorescent hum.",
    "non_diegetic_music": "No non-diegetic music."
  }
}
```

## Field Rules

- `title` is non-empty.
- `script_beat` is a string and may be empty.
- `duration_s` is a number `> 0` and `<= 15`.
- `dialogue` is an array of strings. Preserve approved lines exactly.
- `pictures` contains 1-9 objects. Indices must be ordered and contiguous from 1.
- Picture `role` is one of `actor`, `costume`, `scene`, `prop`, `layout`, `other`.
- `audio` contains 0-3 objects. Indices must be ordered and contiguous from 1.
- Every `label` is non-empty and identifies the exact approved asset the user
  should attach; it is not an invented path or ID.
- All six `prompt` values are non-empty strings.

## Final Validation

First enforce the exact document shape shown above. Reject generic lookalike
schemas that add a `project` wrapper, use `duration_sec` instead of `duration_s`,
use Picture fields such as `type`, `src`, or `character_reference`, or provide
`prompt` as one string instead of the six-field object. Reject invented `ref://`
URIs, ungenerated tail frames, filenames, IDs, or asset approvals. Every Picture
and Audio label must resolve to a real user-provided or user-approved asset.

Before formatting the final object, confirm that every shot's requested actions,
camera changes, dialogue, and state transitions can plausibly fit its duration.
The approved storyboard is the production baseline. Its shot count, ordered Shot
IDs, durations, dramatic beats, and dialogue must survive export unchanged. If a
shot is overloaded or needs to be split, merged, added, removed, reordered, or
renumbered, stop the export and propose that as a storyboard revision. Resume
only after the user approves the revised storyboard and affected downstream work.

Across all six prompt fields:

- every declared Picture index appears as `<Picture N>`;
- every declared Audio index appears as `<Audio N>`;
- no undeclared Picture or Audio tag appears;
- no Picture is assigned a start, middle, end, or activation time;
- no timed clause contains a Picture/Layout tag or says that a reference controls
  a timed state; whole-clip bindings and action timing are separate sentences;
- all action timing fits `duration_s`;
- every string in `dialogue` appears exactly once, unchanged;
- JSON contains no comments, trailing commas, placeholders, or extra prose.

## Serialization Safety

Schema and contract checks do not prove that the final text is valid JSON. The
export candidate must be serialized once and treated as one immutable artifact.

When an execution tool can parse the candidate:

1. Construct the complete production object from the approved production state.
2. Serialize it as JSON and save or retain that exact serialized text.
3. Parse the exact same text or file bytes. Do not manually retype, paste,
   reconstruct, normalize, or repair a second copy inside validator code.
4. Compare the parsed artifact with the approved storyboard and slot-map
   baseline, including exact shot count and ordered Shot IDs.
5. If parsing or contract validation fails, repair the source object, serialize a
   new revision, and validate that new artifact from the beginning.
6. Return the already validated serialized text without editing it afterward.

For an uploaded or generated `.json` file, validation targets that exact file.
When reporting file-level validation, identify the filename and SHA-256 so the
validated artifact is unambiguous. A validator run against an embedded,
retranscribed copy does not validate the original file.

When no execution tool can parse the exact artifact, perform a careful contract
review but do not call it mechanical, strict, parser, runtime, or file-level
validation. If the user asks whether it was mechanically validated, report
`NOT MECHANICALLY VALIDATED` and make Director Studio import the authoritative
check. Do not insert this status or any other prose into the JSON export itself.

All double quotes inside JSON strings must be escaped by the serializer. Dialogue
may be phrased without decorative quotation marks in a prompt while preserving
the approved line exactly once:

```json
{
  "detailed_description": "4-6 seconds: Mia speaks the line Hello?, then listens."
}
```

If quotation marks are needed, the serialized JSON must escape them:

```json
{
  "detailed_description": "4-6 seconds: Mia calls, \"Hello?\", then listens."
}
```

An object that satisfies the schema but whose exact exported text cannot be
parsed is a failed export.
