# H3 prompt writing

## Bind real conditioning

Bind every active Layout to its real `<Picture N>` and name the geography, blocking, or object state it adds. Treat all Pictures as whole-clip conditioning; timing belongs only in `detailed_description`. Multiple Layouts may describe compatible states within one continuous beat, but never claim that one activates at a timestamp. A continuity / tail-frame Layout is an ordinary Picture: never call it a first frame, last-frame socket, or an image that activates only at the beginning. It may preserve blocking, wardrobe, and geography carried from a prior shot.

## Write one feasible clip

Preserve scripted dialogue in order inside `detailed_description` using `<d>[Language] exact words</d>`. Keep speaker IDs, actions, and delivery outside `<d>`. Perform repetitions only when the script specifies them. Never quote the spoken lines in `summary`, `retention_analysis`, `subject_definitions`, `overall_soundscape`, or `non_diegetic_music`; those sections describe roles, ambience, and music without repeating dialogue. Visible scene text is not another vocal event.

Keep every timing interval within `duration_s`, and state required non-appearance positively. Use approved metadata and describe motion directly without assigning Pictures to time windows.

## Escalate incompatible reveals

Recommend splitting when early leakage of a subject or object would invalidate the shot. Do not pretend that Picture ordering can conceal a state from the rest of the clip.
