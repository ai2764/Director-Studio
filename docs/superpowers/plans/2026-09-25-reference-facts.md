# Reference Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Preserve provenance and uncertainty from current reference images through both prompt writers and submission, with one evidence-directed observation repair.

**Architecture:** Extend the existing observation envelope with attributed visual facts and explicit conflicts. A shared module owns source context, durable derived records and source/output signatures. Keep semantic judgment in the model, while code checks evidence locations, source IDs, versions and publication freshness.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, pytest, existing Director provider.

**Spec:** `docs/superpowers/specs/2026-09-25-dialogue-reference-facts-design.md`, reference-fact stage only.

## Global Constraints

- User explicitly requested autonomous iteration while resting on 2026-09-25; carry forward native execution and record technical rulings rather than wait at new document handoffs.
- No character, wardrobe or scene keyword rules. Attribute names/values are data; model observations never become user confirmation.
- Preserve existing completed shots and media. No paid render, service restart or automatic historical regeneration.
- Use the existing two-attempt observation budget for structural OR semantic repairs, not nested retry loops. An unresolved nonessential attribute is unknown, not a universal stop.
- Keep legacy observations readable, but invalidate old policy caches/reviews before new submission. MV behavior remains unchanged.
- Test requirement remains project data: 除 Tao 和 Mia 外，其他说话角色使用英式英语；Tao 和 Mia 保留现有语言与口音设定。

## Review Focus

1. False source promotion: quoted user/library evidence must really exist; model guesses stay model observations.
2. Partial visibility: an unseen attribute must not become a visible contradictory fact or force a creative stop.
3. Same asset ID with replaced bytes and reordered Pictures: stale prompt certification must be rejected while narrative identity survives order changes.
4. Cached observations and concurrent edits: reused records must include source versions, and no stale derived publication may overwrite newer intent.
5. Valid creative variation and new authorized appearances: no historical bad word becomes a forbidden value; no global second reviewer is required.

### Task 1: Grounded observations and bounded repair

**Files:** Create `backend/app/agents/director/reference_facts.py`; modify `material_review.py`; create `backend/tests/test_reference_facts.py`.

**Interfaces:** `reference_sources(project, record) -> list[dict]`; `VisualFact(attribute,value,visibility,evidence,source_id,source_quote)`; `ObservationConflict(attribute,quote,reason)`; `validate_observation_sources(observation, sources)`; `sanitize_observation(observation)`.

- [x] Add tests reproducing a contradictory observation followed by correction, unresolved crop uncertainty, invented source authority, and a corrected appearance using unrelated names/values.
```python
result = await observe_reference(provider_with_conflict_then_correction, record, image)
assert result['description'] == 'A beige blazer and pencil skirt.'
assert result['facts'][0]['value'] == 'pencil skirt'
assert len(provider.calls) == 2
```
- [x] Run `python -m pytest tests/test_reference_facts.py -q`; expect missing behavior/import failures.
- [x] Extend the existing schema and request. Validate exact conflict quotes against description and source quotes against server-supplied sources. Reinspect only the same image once. Preserve unresolved findings separately; remove disputed text and facts from trusted writer-facing material. Facts with `visibility != observed` cannot assert a visible value.
```python
for attempt in range(2):
    observation = parse(await inspect(...))
    validate_observation_sources(observation, sources)
    if not observation.conflicts or attempt:
        return sanitize_observation(observation)
```
- [x] Run `python -m pytest tests/test_reference_facts.py tests/test_director_material_review.py tests/test_tail_prompt_review.py -q`; expect pass, including existing singleton wrapper and structure repair behavior.
- [x] Commit verified observation changes.

### Task 2: Source/versioned ledger and writer/submission integration

**Files:** Extend `reference_facts.py`, `material_review.py`, `service.py`, `tail_prompt_review.py`, `api/projects.py`; extend tests above and `test_projects_api.py`/cold submission fixtures as necessary.

**Interfaces:** `reference_context_signature(project, records) -> str`; `persist_reference_facts(project_id, references, check_current)`; `certify_reference_prompt(project, shot) -> dict`; `reference_contract_current(project, shot) -> bool`; `require_current_reference_contract(project, shot)`.

- [x] Add failing tests for writer preservation of facts/uncertainties, edited metadata/bytes/directing requirements, reordered refs, stale saved prompts, derived ledger not changing managed fingerprints, and a mid-observation edit.
```python
before = certify_reference_prompt(project, shot)
replace_image_bytes_same_asset_id()
assert not reference_contract_current(project, shot.model_copy(update={'meta': {'prompt_reference_contract': before}}))
```
- [x] Pass current user requests and library text as distinct sources, not flattened confirmed facts. Cache by policy, image bytes, source context and provider/model. Atomically merge project-local `agent/reference_facts.json` under the project lock after freshness check; do not modify library metadata.
- [x] Both writers receive whole reviewed evidence including uncertainties. Store source + actual prompt digest after successful validation. Reuse observation cache only under the new policy/context. Refresh old/manual prompts through the existing bounded writer, then require current certification before job creation.
- [x] Run focused backend integration tests; expect no stale contract reaches `create_job`, no completed production data changes, and cold transport fixtures explicitly certify their test-only prompts.
- [x] Commit verified integration.

### Task 3: Real-model probes, regression and report

**Files:** Create `docs/superpowers/reports/2026-09-25-reference-facts-verification.md`; test-only evaluation script and evidence under this plan's ignored workspace.

- [x] Run complete backend suite, frontend suite/build and `git diff --check`.
- [x] If durable jobs are idle, run bounded prompt/vision-only actual-model probes on isolated images/fixtures. Include a conflicting old observation, a crop where clothing is unseen, and an authorized alternative appearance; do not edit Titanic or render media. Report model calls/latency, false blocks and unknowns honestly.
- [x] Independent whole-change read-only review with the five focus cases above; fix Important/Critical findings in one RED→GREEN pass and rerun full suite.
- [x] Record engineering rationale, remaining semantic/media/restart verification limits and all rulings. Commit scoped changes and report hashes; no push/merge or automatic recurring job.

## Self-review

Data provenance, image-version invalidation, current author intent, targeted recovery and both prompt/submission paths are covered. Explicit fact-authoring UI and universal prose semantic verification are not added. Existing accepted metadata/user messages supply source evidence; uncertain model interpretations retain that status. Media QC and restart survival remain separate subsystems, not silently claimed by this phase.
