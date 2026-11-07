# Reference selection pilot

## Single-shot automation

**Auto run** beside the shot title runs only the current shot: save the current draft, refine its description, suggest and save references, write a prompt, then submit video generation. Voice references are preserved. Explicit continuation binds the current take of the adjacent previous shot before refinement, overriding an older source. Missing or unfinished source video stops the chain. Otherwise Motion Context settings are preserved. Each active UI region is highlighted; Video output remains highlighted while its job is queued/running and stops highlighting when that job finishes. Errors stop the chain with the failed stage named; completed changes remain available for manual adjustment. No automatic retry or next-shot batch is started.

The automatic refinement explicitly uses description-only context; reference selection still sees only the refined current description and candidate asset descriptions. No adjacent-shot descriptions are added to either of these two operations. Prompt writing retains its existing saved continuation contract.

In AI Assistant mode, click the hand-and-card sparkle icon beside **Link ref** to auto-select and save references in one action, without opening a dialog. The reference strip updates, elapsed time is displayed, and **Undo selection** restores the previous set. A failed or empty suggestion keeps the existing references. **Link ref** still opens the manual picker for adjustments. The model makes one bounded text-only request; no image inference, Director history, old bindings or previous-shot reference lists are included. Existing new-shot inheritance remains available.

Auto-selection uses the current editable shot description, including unsaved text, without silently saving that draft. A blank description disables the action. The model receives only this description and the candidate catalog (short IDs, category, names, file-key labels and saved descriptions), with no separate dialogue field or adjacent-shot descriptions. Voice references and Motion Context selection are not changed. This is not an automatic whole-film pipeline.

The endpoint is `POST /api/shot-workspace/shots/{id}/materials/suggest` with `expected_refs`. Its operation timing includes the model session/warm-up and is saved as `reference_selection`. The service also returns model-only and selection times. Unknown/duplicate keys, unavailable files, stale shot/library data and truncated outputs are rejected without saving references. The catalog is capped at 96 images; exceeding it returns an explicit error rather than silently dropping candidates. Candidates are scoped to the current project plus unassigned assets, with up to nine output references.

## Lighthouse trial, 2026-11-06

Three saved Demo shots, five existing image assets, three repetitions each. Source project assets and shots were hash-checked unchanged. Reference associations were cleared only in an isolated copy. Historical manual choices were held out from the model.

Model: locally loaded `qwen3.5-35b-a3b-hauhau-q6` on llama.cpp. Nine successful suggestions, 1.111–3.780 seconds, median 2.840 seconds. All three repeats selected the same asset set per shot:

| Shot | Selected images |
| --- | --- |
| Storm outside | Lighthouse |
| Cozy supper | Tao, Quarters, Radio, Ramen |
| The radio | Tao, Quarters, Radio, Ramen |

The historical Cozy supper manual list omitted Radio; the written shot mentions a radio, so this extra selection is grounded and not automatically an error. Manual-list equality is not a quality score. No missing visible Tao identity or irrelevant exterior set was observed in these results.

These are small-catalog, already-loaded-model timings with normal cache reuse. They exclude cold model loading, video generation, human review and manual correction. They do not establish large-library accuracy or prove full automation is ready. A previous separate same-model single-shot prompt-writing replay had a 28.69s median; that historical input is not a controlled before/after comparison with this selection task. Manual-selection time has not been measured.

Reproduce using the existing backend environment (no server required):

```powershell
python tools/benchmark_reference_selection.py --source-project <existing-project-directory> --output <separate-experiment-directory> --base-url http://127.0.0.1:8090/v1 --repeats 3
```

The output contains `baseline.json`, `results.json` and an isolated data copy. Use a project with reusable saved descriptions. Do not commit private experiment data.
