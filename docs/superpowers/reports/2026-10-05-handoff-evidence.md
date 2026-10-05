# Continuation prompt evidence and consistency

## Findings

- The independent continuation reviewer received the candidate and tail evidence but omitted all ordinary reference observations. It could not compare identity-sheet styling with a selected costume's intended contribution.
- The source ending and candidate both showed the same frontal pose, which the model incorrectly used to approve a stationary full-body opening after a cropped medium ending.
- A four-dimension verdict alone still missed this difference in live inference. Requiring a separate check of each shot field and each prompt section caught the claimed opening crop.
- Shot 3's old prompt asserted bare feet copied from an identity sheet even though its ending image cropped out footwear. Its old composition described a back view during the turn; that wording is ambiguous rather than proof of an incompatible final pose.

## Changes

- Writer and reviewer receive the same reference descriptions, facts, notes and layout responsibilities. Explicit costume selection controls the outfit over incidental identity-sheet styling.
- A capable visual adapter first reads the source ending without candidate prose or casting references. Its visible state and evidence limits go to both drafting and review and are saved with accepted review metadata. This is one bounded observation call per write, not per repair attempt. Legacy/text adapters retain their existing path.
- Review requires four dimension checks and eleven field checks, each with a compatibility decision and evidence. A failed check cannot coexist with overall approval. Every readable candidate still has at most one repair before atomic publication. Audit output is bounded to 4,096 tokens; neutral observation to 1,024.
- Neutral-observation failures stop drafting and preserve the saved prompt. Existing transport, concurrent-edit and creative-decision boundaries remain intact.
- Active video prompt contract version 3 invalidates previous stamps; ordinary shots retain their prior off signature. Existing production prompts are not silently replaced.
- Common Writer guidance also improves reference responsibility and section consistency for ordinary mode. No keyword-based appearance or framing checks were added.

## Verification

- Five evidence/approval regressions failed before implementation; the tests now pass. Additional tests cover failed field approvals and neutral-observation failure before drafting.
- Read-only local Qwen replay of Shot 4 rejected the original full-body opening after observing the medium ending. Its repaired prompt starts from that crop and independently passed review.
- Fresh local Qwen drafting for Shot 3 omitted the incidental barefoot assertion. Its actual ending observation explicitly marked footwear unseen. The model still accepted the original Shot 3 prompt when reviewing it; semantic judgment is not guaranteed, and this limitation is retained here rather than presented as a deterministic rejection.
- Live probes verified source project, shot and chat files were unchanged. No generation Jobs were submitted.
- Full backend suite after the field-check changes: 1,989 passed, 13 skipped, two existing Pillow warnings, exit 0. The later neutral-observation addition is covered by the final affected Writer/material-review/video-context suite.
- Final affected Writer/material-review/video-context suite: **124 passed**, exit 0, including neutral-observation failure and atomic publication boundaries.
- Independent code review and follow-up review of the neutral-observation addition found no important issues.
- Staged secret scan and whitespace check passed. The idle 8792 backend restarted with the fix; authenticated Harness 8793 was ready and LAN frontend 5174 returned HTTP 200.

The live probe artifacts remain in ignored runtime storage. Generated media and user project data are excluded from the commit.
