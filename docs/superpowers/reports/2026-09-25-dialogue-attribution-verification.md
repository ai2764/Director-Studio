# Dialogue attribution verification — 2026-09-25

## Outcome and boundary

The failure layer was loss of speaker attribution at the screenplay → Shot → prompt boundary. The prior text/order check could accept the correct words assigned to the wrong person. This change adds source-backed dialogue records, occurrence-aware prompt bindings, current-input/output signatures and bounded repair. It does not introduce character-name rules, prescribe camera/acting prose, or claim that a sidecar understands arbitrary natural language.

Existing Titanic projects and generated clips were not edited or rerendered. All implementation changes remain uncommitted in `feature/mv-mode`; earlier overlapping dirty work is preserved.

## Delivered

- Optional `dialogue_lines` survives model/API/planner boundaries; old string dialogue remains readable. Legacy speech labels remain compatible; literal colons in structured text are preserved.
- Explicit attribution edits are authored revisions. Legacy extraction is derived metadata, never a silent storyboard edit. Speaker-only edits invalidate managed plans; derived prompt certification does not.
- Script evidence resolves to distinct ordered source positions, including repeated words and same-name people. Ambiguous source evidence is rejected rather than guessed. Checksums are freshness checks, not proof of semantic truth or authorization.
- Internal binding envelopes cover every line and speech occurrence. Same-speaker lines can share a block; one line can span cuts. Only the six prompt sections reach H3.
- Normal and tail writers validate bindings and reuse their existing repair budget. Rejected drafts are separate from saved prompts. Explicit model-reported prose conflicts with concrete quotes are repairable; uncertain opinions do not automatically constrain creative staging.
- Shared submit preflight rejects stale certification. Writer saves compare current state under the process-local project lock. Edits during H3 startup preserve the edit, cancel the stale local job and return 409; an already-started remote request can still incur cost, which the error explains.
- Versioned managed fingerprints migrate matching compatible persisted runs once. Unrepresented/new attribution or directing requirements are not silently approved through compatibility migration.
- No permanent UI panel was added. Unchanged API round trips preserve attribution, real dialogue edits invalidate it, and existing UI voice-reference saves remain compatible with attributed shots.

## Verification evidence

| Layer | Result |
|---|---|
| Before implementation | Backend 1,331 passed, 13 skipped |
| Pre-review full regression | Backend 1,373 passed, 13 skipped, 2 existing Pillow warnings |
| Review fixes | 37 focused tests passed after observed failures |
| Post-review full regression | Backend 1,378 passed, 13 skipped, 2 existing Pillow warnings (133.73 s) |
| Frontend | 256 tests in 33 files passed; `tsc && vite build` passed |
| Whitespace | `git diff --check` passed; existing LF/CRLF conversion notices only |
| Independent review | Fresh reviewer found two Important issues, no Critical/Minor; both fixed with RED→GREEN regressions |

Backend command: Python 3.12.7 `-m pytest -q --tb=short`. Frontend: `npm test -- --run`, `npm run build`. Evidence logs and exact task commands remain under `.superpowers/sdd/2026-09-25-dialogue-attribution/`.

The reviewer reproduced reversed repeated-line occurrences and reuse through different quote boundaries; canonical source spans now reject both. The reviewer also found unchanged old managed runs becoming stale after the fingerprint extension; versioned, durable compatibility migration now covers modern and older queue formats, while real edits remain stale.

### Actual local-model prompt evaluation

Provider `llama-swap`, model `qwen38-27b-mtp`. Isolated in-memory source fixtures, no project writes, assets or media jobs. The evaluation uses the actual Director provider/guides and VRAM session, production language-tag normalization, binding annotation, H3 validation and one repair maximum. It is a prompt-contract smoke test, not an end-to-end production, source-extraction or rendering benchmark.

| Case | Accepted | Model calls | Aggregate latency |
|---|---:|---:|---:|
| Injected wrong speaker → repair | 3/3 | 3 | 9.98 s |
| Authorized reassignment | 3/3 | 6 | 42.31 s |
| Creative variants | 3/3 | 4 | 27.54 s |
| Total | 9/9 | 13 | 79.83 s |

Four initial candidates needed the one available repair; no final contract failure or human intervention. The samples accepted whisper/close-up, warm/wide and offscreen/reaction alternatives. Inspection of accepted prose matched the requested speaker and included British-English direction for the sample non-Tao/Mia speakers. This small sample does not establish a general error rate; optional semantic self-checks can miss contradictions.

Exact test requirement carried as directing data: `除 Tao 和 Mia 外，其他说话角色使用英式英语；Tao 和 Mia 保留现有语言与口音设定。` No Tao/Mia production settings were changed. No audio was generated or heard: actual accent, voice identity and lip-sync remain unverified. Cost/token accounting was unavailable from the string-returning adapter; no cost estimate is asserted.

Evaluation setup history is retained: an unknown guide key caused an initial zero-inference setup failure. The first actual 9-case run made 15 calls and accepted 3/9 because the harness omitted production normalization and did not forward all validator errors to repair. After correcting the harness, the reported 9-case run made 13 calls. Total actual model calls across both runs: 28. Do not attribute the harness-only failures to production or hide them as successful tests.

## Rulings and costs

1. Move shared prompt errors to a dependency-free module with a compatibility re-export to break a demonstrated circular import; cost: one indirection.
2. Do not commit incremental overlapping dirty files; cost: no clean per-task integration boundary yet.
3. Do not add a separate grounding retry loop; cost: unresolved legacy attribution may need clarification or another managed attempt.
4. Keep derived attribution in metadata and explicit authored lines in Shot data; cost: two clearly separated representations.
5. Upgrade fake providers to the binding envelope rather than relaxing production checks; cost: test fixture maintenance.
6. Route old/manual prompts through the existing bounded writer with the old prompt supplied; cost: exact creative wording preservation is requested, not guaranteed by a binding-only edit.
7. Treat model-reported prose conflicts as optional evidence, not a deterministic parser or universal second reviewer; cost: unreported semantic contradictions can escape.
8. Migrate only demonstrably compatible old fingerprints. Previously untracked scene changes cannot be reconstructed; new directing/attribution fields may require replanning. Cost: conservative compatibility rather than blanket approval.
9. Exclude unrelated earlier dirty changes from this review claim; cost: not a complete audit of all prior work.
10. Leave media behavior, general semantic error rates and cross-process serialization unverified; cost: prompt tests cannot guarantee generated results or multi-process safety.
11. Preserve the worktree and scratch evidence. The commit-range review tool reported an empty range, so the reviewer inspected the working tree and untracked files directly; cost: evidence is local until a scoped commit.

Deferred minors: none reported. No second reviewer was commissioned after the single fix pass.

## Engineering takeaway

Use deterministic contracts for identity, ordering, freshness and publication; use model reasoning for interpretation and creative decisions. Keep inference separate from authorization and derived state separate from authored state. A bounded repair should receive the failed candidate plus specific evidence, and publish only after validation against the same input version. This reduces preventable errors without encoding artistic choices as keyword rules.

Remaining separate work: reference-fact/costume consistency, duplicate-character visual QC, semantic contradictions not surfaced by the model, actual voice/accent compliance, and video-level acceptance. None is claimed fixed here.
