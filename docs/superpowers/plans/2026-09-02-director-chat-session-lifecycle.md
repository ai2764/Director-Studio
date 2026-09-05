# Director Chat Session Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep an active Director LLM response visible and explicitly cancellable across page refreshes while preserving the submitted user message.

**Architecture:** Add a project-scoped in-memory session registry around the existing background chat runner. The backend exposes session status and explicit cancellation, while the frontend combines that authoritative state with a local `AbortController`, disables the composer, polls until completion, and reloads durable history.

**Tech Stack:** FastAPI, asyncio, Pydantic, pytest, React 18, TypeScript, Fetch/SSE, Vitest, Testing Library

**Spec:** `docs/superpowers/specs/2026-09-02-director-chat-session-lifecycle-design.md`

## Global Constraints

- A refresh or dropped SSE connection must not cancel the backend Director task.
- Explicit Cancel must stop the registered backend task and local stream reader.
- Save the submitted user message before LLM work; save the assistant message only after successful completion.
- Partial assistant tokens remain transient and must not enter durable history or future LLM context.
- Only one active Director chat session may exist per project.
- Preserve unrelated dirty-worktree changes; stage only files named by each task.

---

## File structure

- Create `backend/app/core/projects/chat_sessions.py`: owns the process-local session registry and cancellation lifecycle.
- Modify `backend/app/api/projects.py`: reserves sessions, persists user messages at acceptance time, exposes status/cancel endpoints, and integrates runner cleanup.
- Modify `backend/tests/test_project_chat_stream_lifecycle.py`: exercises lifecycle, persistence, conflicts, disconnects, completion, and cancellation.
- Modify `frontend/src/features/director/api.ts`: defines session status, status/cancel calls, and abortable stream transport.
- Modify `frontend/src/features/director/api.test.ts`: verifies the frontend network contract.
- Modify `frontend/src/features/director/DirectorPage.tsx`: synchronizes active session state, renders Cancel, polls, and reloads history.
- Modify `frontend/src/features/director/DirectorPage.test.tsx`: verifies user-visible behavior. This file already contains unrelated user changes; edit and stage only the new test hunks.

### Task 1: Backend chat-session registry

**Files:**
- Create: `backend/app/core/projects/chat_sessions.py`
- Create: `backend/tests/test_chat_sessions.py`

**Interfaces:**
- Produces: `DirectorChatSessionSnapshot`, `DirectorChatSessionConflict`, and singleton `director_chat_sessions`.
- Produces methods: `reserve(project_id: str)`, `attach(project_id: str, session_id: str, task: asyncio.Task[None])`, `snapshot(project_id: str)`, `finish(project_id: str, session_id: str)`, and `cancel(project_id: str)`.

- [ ] **Step 1: Write registry tests that fail because the module does not exist**

```python
@pytest.mark.asyncio
async def test_registry_rejects_a_second_session_for_one_project():
    registry = DirectorChatSessionRegistry()
    first = await registry.reserve("prj_1")
    with pytest.raises(DirectorChatSessionConflict):
        await registry.reserve("prj_1")
    assert (await registry.snapshot("prj_1")).session_id == first.session_id


@pytest.mark.asyncio
async def test_registry_explicit_cancel_stops_the_attached_task():
    registry = DirectorChatSessionRegistry()
    session = await registry.reserve("prj_1")
    started = asyncio.Event()

    async def worker():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await registry.attach("prj_1", session.session_id, task)
    await started.wait()
    cancelled = await registry.cancel("prj_1")
    assert cancelled is True
    assert task.cancelled()
    assert (await registry.snapshot("prj_1")).active is False
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `cd backend; python -m pytest tests/test_chat_sessions.py -q`

Expected: collection fails with `ModuleNotFoundError: app.core.projects.chat_sessions`.

- [ ] **Step 3: Implement the minimal registry**

```python
@dataclass(frozen=True)
class DirectorChatSessionSnapshot:
    active: bool
    session_id: str | None = None
    started_at: str | None = None


@dataclass
class _ActiveDirectorChatSession:
    session_id: str
    started_at: str
    task: asyncio.Task[None] | None = None


class DirectorChatSessionRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, _ActiveDirectorChatSession] = {}

    async def reserve(self, project_id: str) -> DirectorChatSessionSnapshot:
        async with self._lock:
            if project_id in self._sessions:
                raise DirectorChatSessionConflict(project_id)
            session = _ActiveDirectorChatSession(
                session_id=f"chat_{uuid.uuid4().hex}",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._sessions[project_id] = session
            return DirectorChatSessionSnapshot(True, session.session_id, session.started_at)
```

Implement `attach`, `snapshot`, and `finish` under the lock. In `cancel`, remove the session under the lock, call `task.cancel()` outside the lock, await it under `contextlib.suppress(asyncio.CancelledError)`, and return `False` when no session exists.

- [ ] **Step 4: Run the registry tests and verify GREEN**

Run: `cd backend; python -m pytest tests/test_chat_sessions.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the registry**

```bash
git add backend/app/core/projects/chat_sessions.py backend/tests/test_chat_sessions.py
git commit -m "feat: track active Director chat sessions"
```

### Task 2: Stream lifecycle, persistence, status, and cancellation endpoints

**Files:**
- Modify: `backend/app/api/projects.py:166-194,731-1026`
- Modify: `backend/tests/test_project_chat_stream_lifecycle.py`

**Interfaces:**
- Consumes: singleton `director_chat_sessions` from Task 1.
- Produces: `GET /api/projects/{project_id}/chat/session` and `POST /api/projects/{project_id}/chat/session/cancel`.
- Produces JSON: `{ "active": bool, "session_id": str | null, "started_at": str | null }`.

- [ ] **Step 1: Add failing API lifecycle tests**

Add tests that use the real registry singleton but clear it through `finish`/`cancel` in `finally` blocks:

```python
@pytest.mark.asyncio
async def test_stream_status_and_explicit_cancel_stop_the_active_runner(...):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_handle_chat(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    response = await projects_api.project_chat_stream_endpoint(...)
    stream = response.body_iterator
    first_event_task = asyncio.create_task(anext(stream))
    await started.wait()

    status = await projects_api.project_chat_session_endpoint(project.id)
    assert status.active is True
    await projects_api.cancel_project_chat_session_endpoint(project.id)
    await asyncio.wait_for(cancelled.wait(), timeout=0.2)
    assert (await projects_api.project_chat_session_endpoint(project.id)).active is False
    assert [(m.role, m.content) for m in load_chat_history(project.id)] == [
        ("user", "write the prompt")
    ]
    await stream.aclose()
    first_event_task.cancel()
```

Also add tests for: disconnect leaves the task running, completion removes status and appends one assistant reply, a second stream gets HTTP 409, cancellation with no session returns inactive status, and accepted late failure leaves the submitted user message.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run: `cd backend; python -m pytest tests/test_project_chat_stream_lifecycle.py -q`

Expected: failures because session endpoints do not exist and user history is still appended only after completion.

- [ ] **Step 3: Add response models and endpoints**

```python
class ChatSessionStatus(BaseModel):
    active: bool
    session_id: str | None = None
    started_at: str | None = None


@router.get("/projects/{project_id}/chat/session", response_model=ChatSessionStatus)
async def project_chat_session_endpoint(project_id: str) -> ChatSessionStatus:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    return ChatSessionStatus.model_validate(
        asdict(await director_chat_sessions.snapshot(project_id))
    )


@router.post("/projects/{project_id}/chat/session/cancel", response_model=ChatSessionStatus)
async def cancel_project_chat_session_endpoint(project_id: str) -> ChatSessionStatus:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    await director_chat_sessions.cancel(project_id)
    return ChatSessionStatus(active=False)
```

Map `DirectorChatSessionConflict` to HTTP 409 with `detail="Director chat is already running for this project"`.

- [ ] **Step 4: Integrate immediate user persistence and runner ownership**

In `_project_chat_stream_response`, reserve before returning `StreamingResponse`, then append the accepted user message exactly once with its images. Remove the existing user append from the successful runner branch.

```python
session = await director_chat_sessions.reserve(project_id)
append_chat_message(
    project_id,
    role="user",
    content=msg,
    images=list(user_history_images or []),
)

async def runner() -> None:
    try:
        result = await handle_chat(...)
        response = _chat_result_to_response(result)
        append_chat_message(project_id, role="assistant", content=response.reply, images=...)
        await queue.put({"type": "result", "data": response.model_dump(mode="json")})
    except asyncio.CancelledError:
        raise
    ...
    finally:
        await director_chat_sessions.finish(project_id, session.session_id)
        await queue.put(None)

task = asyncio.create_task(runner())
await director_chat_sessions.attach(project_id, session.session_id, task)
_background_chat_tasks.add(task)
task.add_done_callback(_background_chat_tasks.discard)

async def event_gen():
    while True:
        item = await queue.get()
        if item is None:
            break
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
```

Start and attach the runner before returning `StreamingResponse`; this removes the race where Cancel arrives after reservation but before task attachment. Do not add a `finally: task.cancel()` to `event_gen`; disconnect must preserve the runner. Move the non-stream endpoint's user append immediately before `handle_chat`, leave only assistant persistence after success, and reject that endpoint with HTTP 409 when the same project already has a streaming session.

- [ ] **Step 5: Run backend lifecycle and API regression tests**

Run: `cd backend; python -m pytest tests/test_chat_sessions.py tests/test_project_chat_stream_lifecycle.py tests/test_projects_api.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the backend integration**

```bash
git add backend/app/api/projects.py backend/tests/test_project_chat_stream_lifecycle.py
git commit -m "feat: expose cancellable Director chat sessions"
```

### Task 3: Frontend session and abort API

**Files:**
- Modify: `frontend/src/features/director/api.ts:115-263`
- Modify: `frontend/src/features/director/api.test.ts`

**Interfaces:**
- Produces: `DirectorChatSessionStatus`.
- Produces: `getDirectorChatSession(projectId: string): Promise<DirectorChatSessionStatus>`.
- Produces: `cancelDirectorChatSession(projectId: string): Promise<DirectorChatSessionStatus>`.
- Changes: `chatWithDirectorStream(..., images?: File[], signal?: AbortSignal)`.

- [ ] **Step 1: Add failing API contract tests**

```typescript
it("passes the cancellation signal to Director chat fetch", async () => {
  const controller = new AbortController();
  const fetchMock = vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError"));
  vi.stubGlobal("fetch", fetchMock);

  await expect(chatWithDirectorStream("prj_test", "Plan", [], {}, [], controller.signal))
    .rejects.toMatchObject({ name: "AbortError" });
  expect((fetchMock.mock.calls[0][1] as RequestInit).signal).toBe(controller.signal);
});

it("reads and cancels the active Director chat session", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ active: true, session_id: "chat_1", started_at: "2026-09-02T00:00:00Z" }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ active: false, session_id: null, started_at: null }) });
  vi.stubGlobal("fetch", fetchMock);
  await expect(getDirectorChatSession("prj_test")).resolves.toMatchObject({ active: true });
  await expect(cancelDirectorChatSession("prj_test")).resolves.toMatchObject({ active: false });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/projects/prj_test/chat/session",
    "/api/projects/prj_test/chat/session/cancel",
  ]);
});
```

- [ ] **Step 2: Run API tests and verify RED**

Run: `cd frontend; npm test -- --run src/features/director/api.test.ts`

Expected: TypeScript/test failures because the status functions and signal parameter do not exist.

- [ ] **Step 3: Implement API calls and signal forwarding**

```typescript
export interface DirectorChatSessionStatus {
  active: boolean;
  session_id: string | null;
  started_at: string | null;
}

export async function getDirectorChatSession(projectId: string) {
  const res = await fetch(`/api/projects/${projectId}/chat/session`);
  if (!res.ok) throw await directorResponseError(res);
  return res.json() as Promise<DirectorChatSessionStatus>;
}

export async function cancelDirectorChatSession(projectId: string) {
  const res = await fetch(`/api/projects/${projectId}/chat/session/cancel`, { method: "POST" });
  if (!res.ok) throw await directorResponseError(res);
  return res.json() as Promise<DirectorChatSessionStatus>;
}
```

Add `signal?: AbortSignal` after `images` and pass it in `fetch(path, { method: "POST", headers, body, signal })`.

- [ ] **Step 4: Run API tests and verify GREEN**

Run: `cd frontend; npm test -- --run src/features/director/api.test.ts`

Expected: all tests pass.

- [ ] **Step 5: Commit the frontend API**

```bash
git add frontend/src/features/director/api.ts frontend/src/features/director/api.test.ts
git commit -m "feat: add Director chat session API"
```

### Task 4: Director composer busy state and Cancel interaction

**Files:**
- Modify: `frontend/src/features/director/DirectorPage.tsx:173-203,330-553,602-831`
- Modify: `frontend/src/features/director/DirectorPage.test.tsx`

**Interfaces:**
- Consumes: Task 3 session status functions and abortable stream API.
- Produces: user-visible active-session polling and explicit Cancel behavior.

- [ ] **Step 1: Add failing component tests**

Add API mocks for `getDirectorChatSession` and `cancelDirectorChatSession`, defaulting status to inactive in `beforeEach`.

```typescript
it("replaces Send with Cancel and disables the composer during a local response", async () => {
  const action = deferred<Awaited<ReturnType<typeof chatWithDirectorStream>>>();
  vi.mocked(chatWithDirectorStream).mockReturnValueOnce(action.promise);
  render(<DirectorPage />);
  await screen.findByRole("heading", { name: "1. Corridor walk-in" });
  fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), { target: { value: "Plan it" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByRole("button", { name: "Cancel" })).toBeTruthy();
  expect((screen.getByPlaceholderText(/Talk to the Director/) as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByLabelText("Add images") as HTMLInputElement).disabled).toBe(true);
});

it("cancels the backend session without adding an error bubble", async () => {
  vi.mocked(chatWithDirectorStream).mockImplementationOnce((_p, _m, _h, _handlers, _images, signal) =>
    new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))))
  );
  render(<DirectorPage />);
  await screen.findByRole("heading", { name: "1. Corridor walk-in" });
  fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), { target: { value: "Plan it" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(cancelDirectorChatSession).toHaveBeenCalledWith("prj_test"));
  expect(await screen.findByRole("button", { name: "Send" })).toBeTruthy();
  expect(screen.queryByText(/Something went wrong/)).toBeNull();
});
```

Add a refreshed-session test where initial `getDirectorChatSession` returns active, assert `LLM busy — response is still running`, disabled composer, and Cancel. Use fake timers for a second status response of inactive, then assert `getDirectorChatHistory` is called again and the completed assistant reply appears.

- [ ] **Step 2: Run component tests and verify RED**

Run: `cd frontend; npm test -- --run src/features/director/DirectorPage.test.tsx`

Expected: failures because Send never becomes Cancel and session status is not loaded.

- [ ] **Step 3: Add active-chat state and initial synchronization**

```typescript
const IDLE_CHAT_SESSION: DirectorChatSessionStatus = {
  active: false,
  session_id: null,
  started_at: null,
};

const [chatSession, setChatSession] = useState(IDLE_CHAT_SESSION);
const chatAbortRef = useRef<AbortController | null>(null);
const chatActive = chatSession.active;
const chatDisabled = busy || generationLocked || chatActive;
```

Include `getDirectorChatSession(projectId)` in the initial `Promise.all`. When active, set live runtime text to `LLM busy — response is still running` without inventing partial tokens.

- [ ] **Step 4: Add explicit cancellation and abort-aware send**

Create an `AbortController` immediately before `chatWithDirectorStream`, set a provisional active session, and pass `controller.signal` as the sixth argument. In `catch`, return without an error bubble when `controller.signal.aborted` or `e instanceof DOMException && e.name === "AbortError"`.

```typescript
const cancelChat = async () => {
  if (!projectId || !chatSession.active) return;
  setLlmBusy(true);
  try {
    await cancelDirectorChatSession(projectId);
    chatAbortRef.current?.abort();
    setChatSession(IDLE_CHAT_SESSION);
    setMessages(await getDirectorChatHistory(projectId));
  } catch (cause) {
    setError(cause instanceof Error ? cause.message : String(cause));
  } finally {
    setLlmBusy(false);
  }
};
```

Only clear `chatAbortRef` in the matching request's `finally` block so an older request cannot clear a newer controller.

- [ ] **Step 5: Poll active sessions and reload durable history on transition to idle**

While `chatSession.active`, poll every 1500 ms. On active-to-idle transition, clear transient status/thinking/token state, call `getDirectorChatHistory(projectId)`, replace `messages`, and refresh project state. On polling failure, retain active state and set a specific status error.

- [ ] **Step 6: Render Cancel in the existing button slot**

```tsx
{chatActive ? (
  <button type="button" className="btn danger" disabled={llmBusy} onClick={() => void cancelChat()}>
    Cancel
  </button>
) : (
  <button
    type="button"
    className="btn primary"
    disabled={chatDisabled || (!draft.trim() && pendingImages.length === 0)}
    onClick={() => void send()}
  >
    Send
  </button>
)}
```

Use the existing `.btn.danger` style; do not add a new palette or icon.

- [ ] **Step 7: Run component tests and verify GREEN**

Run: `cd frontend; npm test -- --run src/features/director/DirectorPage.test.tsx`

Expected: all tests pass, including the pre-existing failed-Layout test modifications in the dirty worktree.

- [ ] **Step 8: Commit only this task's hunks**

Because `DirectorPage.test.tsx` contains unrelated changes, use `git add -p frontend/src/features/director/DirectorPage.test.tsx` and stage only session-lifecycle tests. Then run:

```bash
git add frontend/src/features/director/DirectorPage.tsx
git commit -m "feat: cancel and resume Director chat sessions"
```

### Task 5: Full verification

**Files:**
- Verify only; no production edits expected.

**Interfaces:**
- Consumes all prior tasks.
- Produces fresh evidence that backend and frontend behavior work together without regressions.

- [ ] **Step 1: Run focused backend tests**

Run: `cd backend; python -m pytest tests/test_chat_sessions.py tests/test_project_chat_stream_lifecycle.py tests/test_projects_api.py -q`

Expected: zero failures.

- [ ] **Step 2: Run focused frontend tests**

Run: `cd frontend; npm test -- --run src/features/director/api.test.ts src/features/director/DirectorPage.test.tsx`

Expected: zero failures.

- [ ] **Step 3: Run frontend typecheck/build**

Run: `cd frontend; npm run build`

Expected: TypeScript and Vite build exit 0.

- [ ] **Step 4: Review the final diff and working tree**

Run: `git diff --check; git status --short; git log -5 --oneline`

Expected: no whitespace errors; unrelated pre-existing files remain uncommitted and are not included in the feature commits.
