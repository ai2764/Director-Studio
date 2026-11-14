# Writer reference observation reuse

The Writer must not send an unchanged image back through vision merely because
an observation has malformed fields, a bad citation, or a different shot brief.
This fix is independent of PR 41's chat attachment reuse.

## Rollout and compatibility

The new flow is experimental and defaults to **off**:

```
DS_DIRECTOR_WRITER_REFERENCE_REUSE=false
DS_DIRECTOR_VISUAL_FACTS_CACHE=false
```

With the new flag off, observation prompts/parsing/retries use the original main
implementation. The original contextual-cache default and compact-vision option
are preserved. No pending model output is consumed by the default flow. Short
atomic filenames and cache persistence diagnostics do not change model prompts,
tool selection or publication guards.

The measurements below describe the opt-in flow, enabled with both variables set
to true. They do not justify enabling it for every main user or model.

## Evidence and intent (opt-in)

Pixel observation caching is explicitly enabled for the experiment. Its existing `pixels-only-v1`
scope includes the asset, selected file, role, image bytes, provider, endpoint,
model and reference policy. Library names, notes, story edits, Picture order and
prompt-writing requests are not pixel changes. The current full metadata and
directing sources still accompany the returned records and the subsequent pack
suitability review. Prompt publication signatures still cover current inputs.

An existing contextual cache entry may be reused only against its exact current
context and valid citations. It is never promoted into the pixel-only namespace.
This preserves usable old observations without treating metadata-based claims as
independent pixel evidence. `DS_DIRECTOR_VISUAL_FACTS_CACHE=false` retains the
contextual cache behavior for rollback.

Changing image bytes, model, endpoint, role or policy invalidates pixel evidence.
Explicit inspection and targeted pack-review reinspection still bypass the cache.
A genuine unresolved visual conflict retains one visual reinspection opportunity.

## Representation repair

The initial reference observation uses its own JSON schema, without H3 writing
instructions. The normal 24-fact allowance and configured output budget remain;
the optional compact mode retains its smaller limits. The schema explicitly
requires each fact's value, including null for unseen or uncertain attributes.

Known quote-field aliases and null values for uncertain/unseen facts are normalized
without inference. Identical Library notes/description may share a citation target
only when all provenance except the alias ID is identical. Different text,
authority or message provenance remains ambiguous and is rejected.

Other repairs use at most two text-only patch calls, with no image or H3 skill.
Allowed patches fill missing evidence/value from existing observation text, repair
existing citations against supplied sources, and anchor dispute quotes to the
original description. They cannot rewrite visible descriptions, replace existing
fact values, add/remove facts or disputes, or erase uncertainty. Backend schema,
source and evidence checks run again before an observation becomes usable.

## Interrupted and failed requests

Raw visual responses are stored separately under `reference_observations/pending`.
They are untrusted candidates, never accepted reference facts. A later request
resumes validation/text repair before reading the image again. A newer pending
inspection supersedes an older validated entry; failure cannot silently restore
old evidence. Successful validation and cache persistence clear the pending entry.

Reliability audit found that retaining an unusable pending response indefinitely
could block a later valid request, while main recovered by inspecting again. A
failed pending repair now permits one fresh observation in the new request. The
resumed repair and fresh attempt share at most two text-repair slots. Successful
pending repairs still use no images; bad stored output cannot permanently pin an
asset. This remains a validation path, not permission to accept failed evidence.

Atomic cache writes use short independent temporary names so a valid Windows
destination does not fail solely because its temporary filename is longer.
Verified-cache persistence failures are reported in progress instead of silently
claiming a reusable cache. Cache hits do not rewrite the cache unnecessarily.

Tests cover five-reference reuse, changed bytes, explicit reinspection, legacy
compatibility, pending retry across provider instances, rejected evidence edits,
duplicate versus ambiguous citations, Windows paths, current metadata review and
prompt publication guards. Isolated real-model tests must also inspect saved H3
sections; a successful chat reply alone does not establish completion.

## Local validation

A real local Qwen3.5-35B-A3B Q6 run used two full-body sheets, two face sheets
and one cabin image, with no fabricated/preloaded observations for the cold case.
Each case kept all six H3 sections nonempty, passed reference certification and
preserved the authored shot fields after the requested edits were saved.

| Case | Total seconds | Vision requests | Verified observations reused |
| --- | ---: | ---: | ---: |
| Cold five-image Writer | 180.5 | 5 | 0 |
| Duration changed to 8 seconds | 69.3 | 0 | 5 |
| Left/right positions exchanged | 73.5 | 0 | 5 |
| Native Harness chat requesting another write | 124.2 | 0 | 5 |

The native chat actually invoked `write_prompt`, changed the saved H3 text and
kept the authored shot, duration and references unchanged. No media jobs were
created. These are individual integration measurements, not a statistical speed
comparison with main. Text review and rewriting still account for substantial
latency; this fix does not establish planning quality or universal model accuracy.

Validation: 259 focused integration/regression cases passed across material review,
source validation, caching, provider routing, tail-frame review, H3 contracts,
managed prompt commits and retry guards. A subsequent 27-case cache/repair run
also passed, including the added check that a corrupt newer pixel cache cannot
silently restore older contextual evidence.

After the reliability audit, the complete 230-case original main test selection
was frozen from commit `1400f63` without editing its expectations and passed
against the candidate with the new flow disabled. The retained observation/parser
bodies also match main's implementations. Ten rollout/default/recovery checks,
56 enabled-flow checks and 61 enabled provenance/context/recovery checks passed
(these selections overlap). An additional real local-model enabled-flow rewrite
saved all six sections in 65.8 seconds with five cache hits and zero vision calls.

The earlier enabled-by-default revision is superseded by this guarded rollout.
Do not treat its successful cases as evidence that every main model, malformed
response, or first-request recovery sequence is equivalent. Enabling the new flow
remains a separate rollout decision.

## Rough idea to saved prompts

A subsequent native Harness test started with an empty script and zero Shots,
provided one short rough idea plus two actor master IDs and one cabin master ID,
and asked the Agent to finish storyboarding and prompts in the same request.
No script, shots, observations or prompt outputs were pre-seeded.

The first run saved the two 6-second Shots and both prompts, but content review
found an added sitting action in the soundscape. Its feedback turn exposed two
revision defects: unchanged `revise_shot` input cleared the existing prompt, and
silent-shot writing omitted the previous prompt from revision context. That
rewrite also imported the next Shot's nod into the current Shot.

In the opt-in flow, unchanged revisions now preserve prompts/video links and
report that nothing changed. Silent revisions receive their existing prompt;
Writer instructions preserve unaffected text and keep actions in their authored
Shot. The default main flow remains unchanged.

A fresh empty project then completed the entire chain in one request:

- 2 Shots, 6 seconds each, 12 seconds total; both six-section prompts saved.
- 390.0 seconds, 6 successful tool calls, no failed tool calls.
- All three reference images inspected once; the second Shot reused its two
  observations despite different Picture indices. No extra visual inspections.
- No image/video jobs or video continuation; no follow-up correction was needed.
- Manual review found the head-turn action in Shot 1 and the nod in Shot 2,
  without the earlier added sitting action or cross-Shot nod duplication.

The Agent chose a medium side shot with a slight right pan, then a locked close-up.
Six focused silent-revision tests, 66 related tests, and the same untouched
230-case main regression selection passed. These are bounded workflow results,
not a guarantee of visual generation quality or every model's reliability.
