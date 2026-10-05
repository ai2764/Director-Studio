# H3 prompt writing

## Bind real conditioning

Bind every active Layout to its real `<Picture N>` and name the geography, blocking, or object state it adds. Treat all Pictures as whole-clip conditioning; timing belongs only in `detailed_description`. Multiple Layouts may describe compatible states within one continuous beat, but never claim that one activates at a timestamp. A continuity / tail-frame Layout is an ordinary Picture: never call it a first frame, last-frame socket, or an image that activates only at the beginning. It may preserve blocking, wardrobe, and geography carried from a prior shot.

When backend `video_context_observation` is active, the finished source video is supplied through a separate Motion Context channel. Its labelled Writer tail image is observation evidence, not another Picture or Audio slot; no `<Video N>` syntax is defined. Ground the opening in the source ending and describe the action/camera path to the requested next framing, keeping all sections consistent. A single image does not prove motion or speed. If the image is unavailable, do not invent its contents. Disabling source-audio inheritance does not clear current-shot dialogue, Audio references or the song interval. Prompt prose cannot enable or disable the saved video setting.

## Write one feasible clip

Resolve each reference's contribution before writing. An explicitly selected costume controls the worn outfit over incidental clothes on an identity sheet; use reference notes and user direction to determine that scope. A cropped view cannot establish unseen footwear. Preserve established design or omit unsupported nonessential details. Compare composition and script beat with all six sections at both the opening and ending; remove superseded pose and camera descriptions.

For continuous video handoffs, inherit the observed time-zero crop and viewpoint. A requested wider view can be reached by a described camera or subject move; a locked camera and stationary subject cannot instantly produce that wider opening. Match pose and framing separately.

Preserve scripted dialogue in order inside `detailed_description` using `<d>[Language] exact words</d>`. Keep speaker IDs, actions, and delivery outside `<d>`. Perform repetitions only when the script specifies them. Never quote the spoken lines in `summary`, `retention_analysis`, `subject_definitions`, `overall_soundscape`, or `non_diegetic_music`; those sections describe roles, ambience, and music without repeating dialogue. Visible scene text is not another vocal event.

Keep every timing interval within `duration_s`, and state required non-appearance positively. Use approved metadata and describe motion directly without assigning Pictures to time windows.

## Escalate incompatible reveals

Recommend splitting when early leakage of a subject or object would invalidate the shot. Do not pretend that Picture ordering can conceal a state from the rest of the clip.
