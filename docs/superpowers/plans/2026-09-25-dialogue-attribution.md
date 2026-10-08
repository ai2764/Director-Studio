# Dialogue Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve source-grounded speaker attribution from the current Director storyboard to H3 submission, recover locally from attribution errors, and permit authorized creative revisions.

**Architecture:** Add optional structured dialogue to existing Shot records and a small internal prompt-binding sidecar. Keep H3's six strings, existing writer/repair paths, and current directing-request channel; do not build a prompt language or a new universal reviewer. Share deterministic source/version/binding checks between normal writing, tail writing, and submission, with evidence-backed semantic recovery where prose cannot be checked deterministically.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, pytest/pytest-asyncio; React/TypeScript and Vitest for API compatibility.

**Spec:** `docs/superpowers/specs/2026-09-25-dialogue-reference-facts-design.md`, dialogue stage only (sections 0, 4, 5, applicable parts of 8–13).

**Status:** User approved the dialogue-stage direction and requested execution. Written implementation plan prepared for review; product implementation has not started. Reference-fact persistence and output-video QC remain separate stages.

## Global Constraints

- 产品逻辑不能根据 Titanic、角色名、场景名、服装词或英文报错文案分支；这些具体值仅用于测试样本。
- 最终送往 H3 的格式仍是现有六个非空字符串字段，不改 Ref2AV 接口。
- 无对白镜头不要求说话人映射。
- 缺少某人的 Voice 参考本身不等于不能生成，也不能把其台词转交给有 Voice 的角色。
- 旧项目读取时不写回、不批量迁移。
- 继续使用现有失败草稿隔离和托管重试预算，不增加互相嵌套的无界重试。
- MV 的精确源音频、JSON Production、无 Shot 独立 H3 不改变语义；只有 Director Shot 的下一次明确生成执行新检查。
- Do not edit completed Titanic shots, overwrite videos, restart services, or launch renders as part of implementation. Check for active jobs before source edits because the development backend uses reload.
- Preserve all existing uncommitted changes. Work in the already attached `.worktrees/director-agent-loop-fix` checkout on `feature/mv-mode`; no new worktree required. Do not bulk-stage files or include `output/` in commits. Separate task commits only when the incremental changes can be staged without absorbing pre-existing work.
- Test requirement added by user: everyone except Tao and Mia speaks British English. Preserve Tao/Mia's existing language/accent settings, do not invent replacements. This is test-project directing data, never a global name-based condition. Preserve exact dialogue; an accent instruction is not permission to rewrite words.

## Review Focus

1. Repeated words and same-name people: preserve occurrence identity, not text/name uniqueness (Tasks 1–3).
2. Several lines in one speech block and one line across cuts: preserve existing accepted creative forms (Task 3).
3. A valid sidecar beside contradictory prose: metadata correctness must not masquerade as semantic correctness (Tasks 3–4).
4. Agent enrichment versus actual author edits during managed execution: only the latter invalidates the authored plan (Tasks 2 and 5).
5. Regional delivery instructions and missing voice assets: transfer requirements without assigning another person's voice or claiming ASR proves accent (Tasks 4 and 6).

## Commands and file responsibilities

Use PowerShell from the linked worktree. Backend commands below run from `backend`:

```powershell
python -m pytest tests/test_h3_dialogue_contract.py -q
```

New focused modules:

- `backend/app/core/projects/dialogue.py`: data types, compatibility projection, source/version verification, execution-contract digest. No LLM imports.
- `backend/app/agents/director/dialogue_grounding.py`: source-backed recovery for legacy dialogue using the existing provider, and scoped semantic attribution evidence. No file writes before current-input check.
- `backend/app/core/h3/dialogue_binding.py`: internal draft bindings, speech-block coverage and minimal explicit H3 speaker annotation. No creative direction generation.
- `backend/app/agents/director/dialogue_preflight.py`: orchestration shared by writer and submit paths; mode gating, freshness, evidence persistence.
- New tests: `backend/tests/test_director_dialogue_attribution.py`, `backend/tests/test_dialogue_binding.py`, `backend/tests/test_dialogue_preflight.py`.

Existing integration points inspected:

- Shot model: `backend/app/core/projects/models.py`; HTTP patches/submission: `backend/app/api/projects.py`.
- Draft/revision schemas: `backend/app/agents/director/planner.py`; materialization: `casting_service.py::_shot_from_draft`.
- Normal writer: `service.py::write_prompts_after_layout`; tail writer: `tail_prompt_review.py::draft_and_review`.
- Recovery: `prompt_repair.py`; tool schemas: `tool_schema.py` (Pydantic-generated); context: `service.py::_shot_context_summary`, `chat_context.py`.
- Managed fingerprint: `backend/app/core/managed_runs/store.py::_authored_shot_payload`; H3 tool: `backend/app/agents/director/tool_handlers/video.py::start_h3_video`.
- Frontend transport type: `frontend/src/shared/api/types.ts`; compatibility coverage: `frontend/src/features/production/ProductionPage.test.tsx`.

---

### Task 1: Preserve dialogue attribution as optional, source-backed data

**Files:** Create `core/projects/dialogue.py` and `tests/test_director_dialogue_attribution.py`; modify `core/projects/models.py` (all backend paths).

**Interfaces:** Introduce `DialogueSource`, `DialogueLine`, `DialogueIssue`, `DialogueContractError`; `project_dialogue(lines: list[DialogueLine]) -> list[str]`; `validate_dialogue_projection(dialogue: list[str], lines: list[DialogueLine]) -> None`. The error extends existing `PromptFailureError` with `failure_kind="contract"` and an `issues` list.

- [ ] Write failing tests using this neutral fixture (helper defined in the same test module):

```python
def line(line_id="l1", speaker_id="char_1", text="Hello."):
    return DialogueLine(
        line_id=line_id, speaker_id=speaker_id, speaker_name="Visitor",
        text=text, language="English",
        source=DialogueSource(kind="script", source_hash="a" * 64,
                              scene_id="sc1", quote="Visitor: Hello.",
                              occurrence=0),
    )

def test_same_words_are_different_line_instances():
    lines = [line("l1"), line("l2", "char_2")]
    assert project_dialogue(lines) == ["Hello.", "Hello."]
    validate_dialogue_projection(["Hello.", "Hello."], lines)

def test_projection_disagreement_is_not_silently_resolved():
    with pytest.raises(DialogueContractError) as err:
        validate_dialogue_projection(["Goodbye."], [line()])
    assert err.value.issues[0].code == "dialogue_projection_mismatch"
```

- [ ] Run `python -m pytest tests/test_director_dialogue_attribution.py -q`; expect import/missing-interface failure before implementation.
- [ ] Implement these Pydantic records, preserving text and punctuation (do not apply global whitespace stripping):

```python
class DialogueSource(BaseModel):
    kind: Literal["script", "shot_revision"]
    source_hash: str
    scene_id: str
    quote: str
    occurrence: int = Field(ge=0)

class DialogueLine(BaseModel):
    line_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    speaker_name: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: str = ""
    source: DialogueSource

class DialogueIssue(BaseModel):
    code: str
    line_id: str | None = None
    expected: str = ""
    actual: str = ""
    evidence: str = ""
    action: str = ""
```

Add `Shot.dialogue_lines: list[DialogueLine] | None = None`. `None` means legacy/unresolved; `[]` is resolved silence. Validate unique line IDs, not unique speaker names or line text. Speaker IDs are narrative identities independent of asset IDs and Picture numbers. Legacy speaker-prefixed `dialogue` strings use the existing H3 dialogue normalization for comparison; do not rewrite them merely on read. The fixture source is structural only; semantic source validation belongs to Task 2.
- [ ] Add tests for legacy load/no writes, duplicate line IDs, two equal display names with different IDs, exact ellipses/Unicode, and empty dialogue. Run new tests plus `test_project_store.py`, `test_h3_dialogue_contract.py`.
- [ ] Review incremental diff; record test evidence. Commit only isolated new changes if safe (`feat: preserve structured dialogue attribution`).

### Task 2: Ground legacy lines and preserve lawful revisions across write paths

**Files:** Create `agents/director/dialogue_grounding.py`; modify `planner.py`, `casting_service.py`, `service.py`, `chat_context.py`, `api/projects.py`, `frontend/src/shared/api/types.ts`; extend `test_director_dialogue_attribution.py`, `test_projects_api.py`, `test_director_native_tools.py`.

**Interfaces:** `apply_dialogue_update(shot: Shot, updates: dict) -> Shot` in `core/projects/dialogue.py`; `async ground_dialogue(project: Project, shot: Shot, provider: PlanProvider) -> list[DialogueLine]` in `dialogue_grounding.py`. It returns a candidate, never saves. `verify_dialogue_sources(project: Project, shot: Shot, lines: list[DialogueLine]) -> None` checks source hash/quote occurrence, scene and ordered text coverage. Source meaning remains model-mediated, not a claim that substring equality proves speaker identity.

- [ ] Add failing compatibility tests:

```python
def test_legacy_edit_drops_old_attribution():
    shot = Shot(id="s1", project_id="p1", scene_id="sc1", title="Greeting",
                script_beat="A greeting.", duration_s=6,
                dialogue=["Hello."], dialogue_lines=[line()])
    changed = apply_dialogue_update(shot, {"dialogue": ["Goodbye."]})
    assert changed.dialogue_lines is None
    assert not changed.meta.get("prompt_dialogue_signature")

def test_structured_revision_updates_the_legacy_projection():
    shot = Shot(id="s1", project_id="p1", scene_id="sc1", title="Greeting",
                script_beat="A greeting.", duration_s=6, dialogue=["Hello."])
    changed = apply_dialogue_update(shot, {"dialogue_lines": [line(text="Goodbye.")]})
    assert changed.dialogue == ["Goodbye."]
```

- [ ] Run targeted tests and confirm missing behavior; then implement one update function used by HTTP patch, revise, append and storyboard materialization:

```python
if "dialogue_lines" in updates and updates["dialogue_lines"] is not None:
    lines = [DialogueLine.model_validate(x) for x in updates["dialogue_lines"]]
    if "dialogue" in updates:
        validate_dialogue_projection(updates["dialogue"], lines)
    else:
        updates = {**updates, "dialogue": project_dialogue(lines)}
elif "dialogue" in updates and updates["dialogue"] != shot.dialogue:
    updates = {**updates, "dialogue_lines": None}
```

Revalidate the merged Shot (Pydantic `model_copy(update=...)` alone does not validate), invalidate only dependent prompt bindings, and retain unrelated metadata. Identical legacy text is a no-op, not a spurious invalidation. A speaker-only explicit revision invalidates even when words stay unchanged.

Add the optional field to `ShotDraft`, `ShotRevisionSubmission`, `ShotPatchBody` and frontend transport types. Existing schema generation carries it to native/Harness tools. Retain it in `_shot_from_draft` and context summaries.

Legacy grounding passes current script, scene, consecutive lines and directing requests to the existing provider. Require exact source quotes with occurrence locations; verify version and coverage. Use one candidate extraction and the existing bounded recovery policy, not an independent retry loop. Preserve existing stable narrative IDs when resolving aliases; same-name ambiguity produces a line-specific issue. Missing Voice assets do not participate in speaker selection. Do not persist grounding until the preflight/current-input comparison succeeds.

For an explicit authored revision, stamp `kind="shot_revision"` and source digest server-side using the saved revision payload and existing request context. Do not accept a model-supplied `authorized=true` as proof. Apply the existing user/agent revision authority; prompt writing itself never edits authored dialogue. A new authorized revision supersedes the previous execution contract without requiring an additional approval for each directing decision.
- [ ] Test exact-quote grounding, repeated quotes, stale script hash, unsupported quote, no guess on ambiguity, no Voice dependency, same-name IDs, and authorized speaker-only revisions. API and native-tool tests must both exercise the helper; verify rejected updates perform no write. Run `test_director_append_shot.py`, `test_director_native_tools.py`, `test_projects_api.py` plus the new attribution tests.
- [ ] Review and record compatibility evidence; isolate a commit if safe (`feat: ground and revise dialogue without losing provenance`).

### Task 3: Bind speech blocks to lines without a creative template

**Files:** Create `core/h3/dialogue_binding.py`, `tests/test_dialogue_binding.py`; extend `tests/test_h3_dialogue_contract.py`. Do not change the public six-field `PromptSections` model.

**Interfaces:** `DialogueUse(line_ids: list[str], speaker_id: str, block_indexes: list[int])`; `DialoguePromptDraft(prompt_sections: PromptSections, dialogue_uses: list[DialogueUse])`; `validate_dialogue_uses(draft: DialoguePromptDraft, lines: list[DialogueLine]) -> None`; `annotate_speakers(draft: DialoguePromptDraft, lines: list[DialogueLine]) -> PromptSections`.

- [ ] Write a failing test where all words are correct but the declared speaker is wrong:

```python
def test_correct_words_do_not_allow_a_different_speaker():
    from app.core.projects.dialogue import DialogueLine, DialogueSource
    expected = DialogueLine(line_id="l1", speaker_id="char_1", speaker_name="Visitor",
        text="Hello.", language="English",
        source=DialogueSource(kind="script", source_hash="a" * 64,
                              scene_id="sc1", quote="Visitor: Hello.", occurrence=0))
    sections = PromptSections(subject_definitions="A visitor by a door.",
        summary="A greeting.", retention_analysis="Preserve the doorway.",
        detailed_description="0–6 seconds: <d>[English] Hello.</d>",
        overall_soundscape="Room tone.", non_diegetic_music="N/A")
    draft = DialoguePromptDraft(prompt_sections=sections, dialogue_uses=[
        DialogueUse(line_ids=["l1"], speaker_id="char_2", block_indexes=[0])])
    with pytest.raises(DialogueContractError) as err:
        validate_dialogue_uses(draft, [expected])
    assert err.value.issues[0].code == "dialogue_speaker_mismatch"
```

- [ ] Confirm failure; implement the sidecar with `extra="forbid"`. `block_indexes` are zero-based occurrences of existing `<d>` blocks, not approximate prose offsets. Each block belongs to one speaker. One use may cover several consecutive lines by that speaker and/or several blocks across cuts. Compare ordered spoken text through existing dialogue normalization, preserving raw source text and `<scenetrans>` behavior. Reject unknown IDs, wrong declared speaker, duplicate coverage, missing blocks and extra speech. When different speakers have been packed into one block, return a local split request rather than forbidding multi-line blocks globally.

Keep the writer's free direction. Add only a stable speaker label outside each bound `<d>` block, plus a subject-level mapping to the narrative identity when necessary; no generated camera, action, timing or delivery sentences. IDs must not depend on asset count. The adapter submits only `draft.prompt_sections`, never sidecar JSON, new H3 keys or invented sockets.

Do not infer speaker identity by searching for neighboring names, pronouns or speech verbs. A correct binding beside contradictory prose is not a deterministic success proof. Carry an exact conflicting quote and its claimed interpretation into local semantic repair (Task 4); unsupported model disagreement is advisory, not a hard failure. Keep raw candidate, bindings and annotated output separately for diagnosis.
- [ ] Add positive tests for grouping consecutive same-speaker lines, English/Chinese speech across cuts, repeated text, silent shots, renamed characters, and alternative camera/performance prose. Add negative tests for each coverage error and wrong-speaker IDs. Assert annotation is idempotent and changes no spoken words or unrelated sections. Run `test_dialogue_binding.py`, `test_h3_dialogue_contract.py`, `test_h3_prompt.py`.
- [ ] Review incremental diff and record evidence; isolate a commit if safe (`feat: validate dialogue bindings without constraining direction`).

### Task 4: Use the same contract in normal and tail writing, with bounded repair

**Files:** Create `agents/director/dialogue_preflight.py`; modify `service.py`, `tail_prompt_review.py`, `prompts.py`, `prompt_repair.py`; extend `test_director_dialogue_attribution.py`, `test_tail_prompt_review.py`, `test_director_material_review.py`.

**Interfaces:** `async prepare_dialogue(project, shot, provider) -> list[DialogueLine]` (mode-aware); `parse_dialogue_draft(raw: str) -> DialoguePromptDraft`; `validate_and_annotate_dialogue(draft, lines) -> PromptSections`; `dialogue_contract_signature(project, shot, lines, directing_requests) -> str` (pure, in core dialogue module). Semantic evidence records contain line ID, exact candidate quote, source quote, proposed mismatch and uncertainty; existence checks are deterministic but interpretation is not.

- [ ] Add provider-spy tests using existing `saved_shot`, `Provider`, `Orchestrator` patterns in `test_h3_dialogue_contract.py`. Seed source-backed lines to isolate writer behavior; return a wrong-speaker draft then a corrected draft. Assert two writer calls, repair contains line/expected/actual IDs, previously accepted prompt survives failure, and no unrelated shot is changed. Add the same behavior to tail-writer fixtures.
- [ ] Confirm failure; extend only Director writer's internal response envelope:

```python
payload = _extract_json_payload(raw)
draft = DialoguePromptDraft.model_validate(payload)
validate_dialogue_uses(draft, lines)
sections = annotate_speakers(draft, lines)
validate_h3_prompt(sections.as_ordered_text(), shot.dialogue,
                   audio_count=effective_audio_count,
                   submitted_picture_indices=[r.picture_index for r in shot.refs])
```

Use the same envelope inside the existing tail candidate alongside `shot_patch`; keep its allowed patch fields unchanged. Scope writer instructions so the internal envelope is explicitly distinct from the H3 six-section output. For non-Director paths retain the existing parser/provider shape.

Handle old six-section/manual prompts by bounded attribution extraction on their actual speech blocks with exact evidence, not by fabricating sidecar metadata from expected speakers. Unambiguous candidates can be annotated without changing creative prose; ambiguity produces a focused revision request. Recheck existing cached/preserved prompts when no current contract signature exists.

For detected prose conflicts, require an existing quote and compare against the current immutable attribution; route well-grounded conflicts into the same repair attempt. Keep uncertain interpretations visible without treating a reviewer opinion as proof. Do not add a universal second reviewer on every prompt. Evaluate residual semantic errors in Task 6.

Update `merge_repair` to merge nested `prompt_sections`, replace `dialogue_uses` as a whole, and invalidate/revalidate bindings whenever `detailed_description` changes. Preserve existing plain and tail envelopes. Persist structured issues with the rejected candidate. Do not increase total repair attempts or create a retry loop inside grounding. Consume the shared attempt budget across extraction/writing repair; if exhausted retain evidence for a later user-directed attempt.

Pass source-backed lines and `directing_requests(project)` into both writer paths. Include the test-specific British-English instruction as normal request data outside spoken words; no role-name conditionals and no forced Voice reference per speaker.
- [ ] Add tests for changed description with stale block indexes, malformed sidecar, unauthorized dialogue changes via tail patch, unsupported reviewer complaint, lost response/retry, manual-prompt recovery and English-accent requirement propagation. Confirm Tao/Mia existing settings are not overwritten and words/ellipses remain exact. Run writer/tail/material-review/repair tests with stub providers only.
- [ ] Review and record call counts and failure preservation; isolate a commit if safe (`feat: recover dialogue attribution through existing writer paths`).

### Task 5: Guard submission freshness without falsely staling managed runs

**Files:** Modify `dialogue_preflight.py`, `api/projects.py`, `core/managed_runs/store.py`, `agents/director/prompt_repair.py`; if needed `core/projects/store.py` for scoped compare-and-save; create `tests/test_dialogue_preflight.py`; extend `test_managed_run_continuation.py`, `test_managed_h3_start_tool.py`, `test_projects_api.py`.

**Interfaces:** `require_current_dialogue_contract(project, shot, lines, saved_signature) -> None`; use the same `dialogue_contract_signature` as Task 4. Any new compare-and-save must atomically compare the affected shot and script version under the existing project lock, perform no await while locked, and write through a temporary file plus atomic replacement. Do not retrofit all storage or claim cross-process serialization from a process-local lock.

- [ ] Add failing race tests with an `asyncio.Event`-controlled fake writer: pause response, edit speaker/source/requirements, release response; assert the new authored edit survives and stale candidate never reaches fake H3 submission. Also test input update after validation but before enqueue.
- [ ] Compute the signature from a versioned canonical payload:

```python
payload = {
    "version": 1,
    "script": project.script_text,
    "scene_id": shot.scene_id,
    "dialogue": shot.dialogue,
    "lines": [item.model_dump(mode="json") for item in lines],
    "directing_requests": directing_requests,
}
digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
```

Existing reference/audio/layout signatures still apply; dialogue signature supplements rather than replaces them. Bind the final prompt bytes and sidecar to the saved validation record so an edited prompt cannot reuse it. Recheck at writer save and immediately before submitting the accepted immutable input snapshot. Do not silently regenerate when no relevant input changed.

Distinguish explicit `dialogue_lines` authored changes from source-grounded enrichment: only authored revision content belongs in `_authored_shot_payload`; derived script attribution is tied to the script/shot source already fingerprinted. Derivation origin is stamped by trusted service code, not a user-controlled flag that bypasses staleness. Preserve legacy fingerprint compatibility; do not change existing run digests by adding empty fields for old shots. A forged/stale derived record never qualifies for reuse.

Hook the shared preflight into `submit_shot_endpoint` and verify both `start_h3_video` and Harness/native tools reach it. Keep unrelated standalone H3 routes untouched. Store the accepted contract snapshot in job metadata for diagnosis; no historical metadata rewrites.
- [ ] Add tests for speaker-only authored edits changing fingerprint; same-input enrichment leaving it unchanged; script/scene/source/requirement changes expiring drafts; forged origin; manual prompt edits; all four Director entry paths; unchanged MV/JSON/standalone H3; and cancellation isolation. Use fake submit calls to assert zero duplicate enqueues. Run new preflight tests and all managed-run/API suites.
- [ ] Review and record concurrency evidence; isolate a commit if safe (`fix: reject stale dialogue submissions without staling derived state`).

### Task 6: Demonstrate improvement and creative counterexamples

**Files:** Extend the preceding tests and `frontend/src/features/production/ProductionPage.test.tsx`; create `backend/tests/fixtures/dialogue_attribution_cases.json`. Keep verification evidence locally.

**Interfaces:** Fixture entries have `id`, `source_lines`, `candidate_uses`, `expected_issue_codes`, `directing_request`, `allowed_variants`. They are test data, not a production lookup table. The report separates deterministic checks, stubbed orchestration, actual-model evaluation and actual media QC.

- [ ] First reproduce the recorded three-speaker mismatch using a minimal neutral fixture, then generalize names, language, repeated lines and scene setting. Include correct authorized reassignment and multiple valid creative candidates. Example data:

```json
{
  "id": "authorized-reassignment",
  "source_lines": [{"line_id": "l1", "speaker_id": "char_new", "text": "All clear."}],
  "candidate_uses": [{"line_ids": ["l1"], "speaker_id": "char_new", "block_indexes": [0]}],
  "expected_issue_codes": [],
  "directing_request": "Use the newly saved speaker assignment; choose the performance freely.",
  "allowed_variants": ["whisper with a close-up", "normal speech in a wide shot"]
}
```

- [ ] Add API/frontend round-trip tests: legacy UI can read/save shots, unchanged full payload does not erase bindings, real dialogue edits invalidate old bindings, error display remains compact, and no new permanent panel is added.
- [ ] Run all backend tests, frontend tests and frontend build; run `git diff --check`. Record commands, counts, failures and skips without claiming a fix from historical test counts:

```powershell
# cwd: backend
python -m pytest -q
# cwd: frontend; run separately
npm test
npm run build
# cwd: worktree
git diff --check
```

- [ ] Run a small, explicitly scoped real-model prompt evaluation after deterministic tests pass and services are idle: three executions each of wrong-speaker recovery, authorized reassignment, and an alternative valid performance, using a test project or isolated fixtures, not overwriting Titanic. Log error escapes, false blocks, repair success, human interventions, model calls, latency and available cost. Do not infer general error rates from nine samples.
- [ ] For any subsequently authorized rendered test, persist the user's exact requirement in that test project's directing requests: `除 Tao 和 Mia 外，其他说话角色使用英式英语；Tao 和 Mia 保留现有语言与口音设定。` Check its presence in writer input and resulting per-speaker direction. Assess actual audio by listening or a suitable audio-capable reviewer with uncertainty recorded; ASR only checks words. If listening is unavailable, label accent unverified. Do not silently choose an RP/regional dialect or rewrite dialogue spelling to simulate accent.
- [ ] Finish with a compact report: failure layer (lost attribution at a data boundary), chosen mechanism (source-backed data + current-version checks + bounded repair), gains/costs, allowed creative variants, and remaining limits (prose semantics, actual speaker/voice and generated-video quality). Do not claim duplicate-character or reference-fact defects are solved. Review the complete incremental diff before suggesting any integration/commit of prior unrelated work.

## Plan self-review

- Scope: dialogue only; ref observations, costume resolution, automatic video QC and rerender are explicitly deferred.
- Coverage: data compatibility (1–2), provenance/authorized revision (2), prompt binding/creative variants (3), normal/tail/manual recovery (4), signatures/all-entry freshness/managed behavior (5), generalization and British-English test delivery (6).
- Known epistemic limit: an exact source quote and a schema prove origin/shape, not semantic truth. The implementation must report uncertainty rather than claim deterministic understanding of arbitrary prose.
- Risk: binding extraction can add model work for legacy/manual prompts. Reuse signatures and repair budget; measure the added calls before considering broader rollout.
- Recommendation: native execution in this session because tasks share tightly coupled models and writer interfaces; complete a whole-change independent review after verification. Alternative: subagent-driven task-by-task implementation/review with higher coordination/context cost.
- Next gate: user review of this written plan and execution-method choice. No product-code edits or paid renders performed while preparing it.
