# Harness Context Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this tightly coupled repair; independent final review uses superpowers:requesting-code-review.

**Goal:** Restore useful responses from long Director sessions without discarding history or replaying business mutations.

**Architecture:** Python owns authoritative project data, skills, provider IO and tool validation. Harness owns one metered request envelope, native durable history and native bounded compaction. Its pressure threshold is an early warning, not the provider's hard capacity.

**Tech Stack:** Python/FastAPI, TypeScript, DeepSeek Harness 0.1.1-rc.2, Ollama Qwen 27B.

**Spec:** User-approved design in this conversation: remove duplicate snapshots, focus one shot, reuse native recovery, verify real Qwen, sync shared changes.

## Global Constraints

- Preserve existing UI history, native JSONL and user project/material data.
- Do not change the H3 workflow or generation permissions.
- Prefer Harness's installed tools and compaction APIs; no replacement summary store.
- Failed inference must not replay an already executed business tool.
- Keep workflow configuration unchanged.
- Real-model tests use isolated neutral fixtures, not live production mutation.

### Task 1: One authoritative envelope

Files: harness/src/kernel.ts; backend/app/agents/director/harness_runtime.py; backend/app/api/projects.py; harness/src/kernel.test.ts; backend/tests/test_harness_session_recovery.py.

- [ ] Add failing kernel test: inspect host llm params and assert system and PROJECT_STATE occur once, never in a newly generated user snapshot; literal {{braces}} survive; next tool step sees new version.
  `expect(JSON.stringify(params.messages)).not.toContain("Current runtime context."); expect(params.system).toContain("PROJECT_STATE:");`
- [ ] Run `npm test -- src/kernel.test.ts` from harness and focused pytest; observe failures.
- [ ] Use native complete section with a variable value (no interpolation of project text), refresh after tools, forward system/tools. Prepare Python skill once in context; provider transport receives prepared_system=True, including skill-free summary calls.
- [ ] Verify actual provider boundary has one DIRECTOR_SKILL and one PROJECT_STATE; retain authoritative Python tool validation and stale-state checks.

### Task 2: Focused state and on-demand reads

Files: backend/app/agents/director/chat_context.py; tool_schema.py; tool_handlers/media.py; harness_runtime.py; backend/tests/test_harness_session_recovery.py.

- [ ] Add failing tests using two saved Shots: no-target context contains summaries only, explicit target contains its details but not neighbor details; get_status(shot_id) returns that shot without changing files.
  `assert "script_beat" not in state["shots"][1]; assert result["shot"]["id"] == target.id`
- [ ] Run focused pytest and verify intended failure.
- [ ] Opt Harness into focused serialization; extend existing get_status with optional shot_id, preserving no-argument status behavior.
- [ ] Run focused backend tests, including ref-change permissions and stale-state rejection.

### Task 3: Native bounded recovery and validation

Files: harness/src/kernel.ts; harness/src/kernel.test.ts; harness/src/session-recovery.test.ts; scripts/harness_context_probe.py.

- [ ] Add failing cases: durable shrinking summary above soft threshold but below input capacity proceeds; true summary failure stops with cause; irreducible fixed input stops before provider; old sourced runtime snapshots migrate through native compaction and remain in original JSONL.
- [ ] Run kernel tests and verify failures.
- [ ] Restore native bounded retries. Accept durable progress only within input budget; keep true failure distinct. Use native summarize hook to omit regenerable old host snapshots and business instructions from summary input, never actual user messages.
- [ ] Run Harness tests/typecheck and relevant backend suites.
- [ ] Run isolated neutral Qwen long-history, repeat-turn and summary-resume probes; record provider counts, finish reasons and mutation counts.
- [ ] Independent review, rerun tests, restart scoped idle services, and verify health.

## Progress

- Dialogue validation committed separately in 27a4717; 269/270 regression tests passed.
- Tasks 1–3 implemented and independently reviewed. Three review findings fixed:
  issued-envelope/version binding, overflow summary-cause preservation, and
  reuse of manual legacy-migration result.
- Final checks: backend 292 passed; Harness 34 passed and typecheck passed.
- Real Qwen final probes passed: four chat turns, automatic
  and manual compaction, checkpoint reuse, one exact targeted get_status read,
  zero mutations. Detailed counts: docs/HARNESS_CONTEXT_RECOVERY.md.
- Backend/sidecar restarted while idle and health confirmed context-envelope-v2.
- Commit checkpoint, 2026-09-13: the selected backend regression suite returned
  204 passed and 1 failed. The failure in
  `test_native_write_prompt_tool_is_executed_and_result_returns_to_model`
  expects `queue_ref_frame` for a prompt-only request. It also reproduces with
  the source HEAD versions of the changed application modules; this checkpoint
  does not change the existing explicit-Layout-request permission rule.
  Harness: 34 passed and typecheck passed in both worktrees.
