# H3 Full-Reference Prompt Contract

This is the Director Studio plugin's bundled Ref2VA contract. Always read it
before exporting production JSON. When the more detailed `h3-prompt-writing`
skill is also available, use it for additional H3 guidance without weakening
the constraints below.

Write these six non-empty fields in order, in English. Preserve dialogue, lyrics,
and visible text in their original language.

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

## References

- Bind each declared Picture as `<Picture N>` and state exactly what it controls.
- Bind each declared voice reference as `<Audio N>` and state the speaker identity,
  timbre, language/accent, and delivery it controls.
- Keep labels consistent across all six fields.
- Every Picture and Audio conditions the complete clip. Never assign a reference
  to a time range or describe switching between references.
- A Layout controls composition and blocking; it is not a guaranteed first frame.
- Put Picture and Layout bindings in untimed sentences. In
  `detailed_description`, establish their whole-clip responsibilities before the
  timed action timeline. A clause containing a time window must not also contain
  a `<Picture N>` tag or say that a reference controls, defines, activates, or
  becomes visible during that interval.

## Content

- `subject_definitions`: define referenced people, places, costumes, props,
  Layouts, and voices from their approved metadata.
- `summary`: one short paragraph describing the target clip and its main reference
  relationships.
- `retention_analysis`: say what identity, design, geography, composition, or
  audio traits must be preserved and name continuity risks concisely.
- `detailed_description`: describe one coherent clip in playback order. Include
  composition, visible appearance, positions, environment, lighting, actions,
  expressions, camera movement, physical sound, and dialogue. Put action timing
  in seconds and keep every interval within `duration_s`; keep reference bindings
  outside those timed clauses.
- `overall_soundscape`: ambience and physical sounds, not audience-only score.
- `non_diegetic_music`: audience-only music with instrumentation, tempo, and
  dynamics, or `No non-diegetic music.`

Include every exact dialogue line once and only once. Do not copy words from a
voice-reference recording when it provides only vocal identity. H3 has one
positive prompt and no separate negative-prompt field; express essential
continuity prohibitions concisely inside the relevant section.
