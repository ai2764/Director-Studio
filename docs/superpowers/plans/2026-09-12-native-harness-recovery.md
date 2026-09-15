# Native Harness Session Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for these tightly coupled tasks. Steps use checkbox syntax.

**Goal:** Preserve native compaction across calls and expose safe manual compaction.
**Architecture:** Existing short-lived runtimes resume stable persisted sessions. Python remains business owner; native JSONL owns execution history.
**Tech Stack:** Python/FastAPI, TypeScript/Cordis, DeepSeek Harness 0.1.1-rc.2, React.
**Spec:** docs/superpowers/specs/2026-09-12-native-harness-recovery-design.md

## Global Constraints

- Use pinned 0.1.1-rc.2 native persistence and compactNow; no custom summary database.
- Preserve unrelated workflow configuration.
- No automatic repeat of user actions; no infinite recovery.
- Short-lived runtime must flush/dispose before releasing session admission.
- Original Python chat history remains the UI transcript, not the resumed agent memory.

### Task 1: Native persistent runtime and safe adapter

Files: harness/src/kernel.ts, server.ts, kernel.test.ts, server.test.ts, package.json/lock.
- [x] Write failure tests for cross-call summary reuse, no reseeding, manual compact and no tools, failure preservation, contention and length/tool precedence.
- [x] Run npm test and confirm missing behaviors.
- [x] Mount native JSONL with compression none under a server-owned root. Stable session_id resumes existing history; absent id retains test/legacy ephemeral behavior.
```ts
const existing = (await ctx.sessionPersistence.list()).some(s => s.id === sessionId);
handle = existing ? await ctx.agents.resume({resumeSessionId: sessionId, setup})
                  : await ctx.agents.create({sessionId, setup});
```
- [x] Call native compactNow on operation compact; return compaction metadata, never followup.
```ts
const before = ctx.tokenMeter.measure(handle.agent.session).totalTokens;
const result = await ctx.compaction.compactNow(handle.agent, signal);
const after = ctx.tokenMeter.measure(handle.agent.session).totalTokens;
```
- [x] Forward maxTokens and usage; prioritize max-tokens finish. Run npm test and npm run typecheck.

### Task 2: Python host and manual API

Files: backend/app/agents/director/harness_runtime.py, harness_client.py, app/api/projects.py, core/vram/ollama_client.py, core/llm/openai_compatible.py, usage.py and tests.
- [x] Failing tests: stable project/data-root identity, summary cap forwarding, compact metadata survives transport, compact does not append user/assistant history or invoke tools, busy guard.
- [x] Add operation argument to host helper, reuse normal Harness transport; preserve optional compaction result.
```python
session_id = hashlib.sha256(str(settings.projects_dir.resolve()).encode() + b"/" + project_id.encode()).hexdigest()
```
- [x] POST /projects/{project_id}/chat/compact reserves existing chat registry, prepares chat_fn, runs operation compact, releases reservation in finally. Only Harness runtime; 409 on legacy or concurrent work.
- [x] Propagate validated max_output_tokens into providers and telemetry without changing default calls.
- [x] Run targeted pytest and real sidecar integration with an isolated persistence root.

### Task 3: Manual compression UI

Files: frontend/src/features/director/api.ts, DirectorPage.tsx, ContextUsage.tsx/css and corresponding tests.
- [x] Failing test: click Compact context, preserve draft, show before/after result, never send chat automatically; failure visible; disable while chat/compaction/generation busy.
- [x] Add compactDirectorContext(projectId, signal) request and typed result; separate pending/result state in page; use native runtime status to hide control in legacy.
```ts
const result = await compactDirectorContext(projectId, controller.signal);
// Display estimated before/after; do not invoke sendMessage.
```
- [x] Run related vitest and frontend build.

### Task 4: Verify, review and sync

- [x] Run complete Harness tests/typecheck, targeted backend tests and frontend build/tests.
- [x] Review native persistence lifecycle, failure and cancellation boundaries; no hand-written summary mechanism.
- [x] Verify the scoped shared changes without altering workflow configuration.
- [x] Report remaining limits explicitly: manual recovery, approximate counts, no automatic output continuation.

## Verification record

- Harness: 28 tests passed and TypeScript typecheck passed.
- Backend: 66 focused tests passed, including projects API regressions.
- Frontend: 44 related tests passed and production build passed.
- Review: explicit native persistence flush failure propagation and bootstrap-only history import verified by regression tests; fresh Node process restored the saved summary.
- Backend and sidecar restarted while all chat sessions were idle; native-sessions-v1 capability and API health verified.
- Changes remain uncommitted. No automatic recovery or production-model reliability estimate is claimed.
