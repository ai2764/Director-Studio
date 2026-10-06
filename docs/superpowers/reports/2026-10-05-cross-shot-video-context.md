# Cross-shot video context routing

## Reproduced faults

- Core source validation required the immediately adjacent predecessor, rejecting a selected earlier shot.
- Director's tool schema did not expose `source_shot_id`; its handler dropped that field. Explicit requests could report success while silently selecting the adjacent shot (6 → 5 instead of 6 → 2).
- UI preview, version discovery and dependency labels assumed the adjacent shot. Frame/audio edits omitted the saved source ID and could reset a non-adjacent dependency.
- Reordering unrelated shots was rejected even when the selected source still preceded its target.
- The experiment launcher did not pin the branch's Director guide. The loader could choose an older global skill without the video-continuation section; editing the repository guide alone did not guarantee its delivery.

## Changes

- Allow any earlier shot on the same storyboard. Default to the adjacent predecessor only when no source ID is supplied. Reject unknown, foreign, self and future sources.
- Expose and preserve the source ID in Director tools and UI saves. Preview and show the actual selected source. A compact source selector is available inside active continuation settings; ordinary shots still hide these controls.
- Keep resolution inheritance, frozen submitted bytes, historical job selection and prompt freshness tied to the selected source.
- Allow unrelated storyboard changes while requiring sources to remain before their targets.
- Pin this isolated instance's Director skill to the current branch's guide so the Agent receives the continuation rules without modifying the global skill.

## Live local Agent checks

The running Harness/local Agent on backend 8792 received natural Chinese instructions. Three separate test projects reused existing finished clips explicitly labelled as fixtures. No GPU generation was submitted and existing production projects were not changed.

| Case | Saved relationships | Result |
| --- | --- | --- |
| 连续、断开、回接 | 2 ← 1; 3 ← 2; 4/5 off; 6 ← 2 | Correct source IDs, resolved Job IDs and source video SHA-256 |
| 另一条回接分支 | 3 ← 2; 4 ← 3; 5 ← 2 | Correct source IDs, resolved Job IDs and source video SHA-256 |
| 断开后接回第一镜 | 2 ← 1; 3 off; 4 ← 1 | Correct source IDs, resolved Job IDs and source video SHA-256 |

Live artifact: `.run/video-context/branch-chat-probe.result.json`.

The third reply incorrectly said all three targets inherited Shot 1's size. The stored independent Shot 3 was actually off and had no inherited lock. This is an Agent summary error, distinct from the routing faults. The Director guide now explicitly states that constraints apply per target and that off targets inherit no source-video resolution lock.

After pinning the branch guide and reloading backend 8792, a real Agent read-only follow-up correctly distinguished Shot 3's historical 864×480 output from an inherited lock. It reported no source or resolution constraint for Shot 3, and source-locked 864×480 for Shots 2 and 4; returned actions were empty. Artifact: `.run/video-context/branch-chat-summary-check.json`.

## Verification boundary

Backend regressions cover Agent-tool execution, persistence, status, Writer source-ending selection, canonical submission with non-adjacent source bytes, source reruns, source-relative reorder validation and invalid dependencies. Frontend checks cover source preview/labels and preservation during settings edits. These checks do not establish generated visual continuity quality; no new GPU videos were rendered for this routing test.

- 181 related backend tests passed after the final changes.
- 250 additional Director/Harness/prompt/managed-start tests passed.
- 304 frontend tests passed, with production build success.
- The restarted experiment reports healthy, LAN frontend 5174 returns HTTP 200, and ComfyUI's queue is empty.

Previously misconfigured shots are not automatically migrated: the intended source cannot be inferred from an already saved adjacent source ID. Reissue the source selection for those shots.
