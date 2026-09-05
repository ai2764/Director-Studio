# Director chat session lifecycle

## Goal

Make a running Director LLM response visible and cancellable across page refreshes. A refresh must not silently make the composer appear idle while the backend continues working, and an explicit Cancel action must stop the active response.

## User-visible behavior

- A submitted user message is saved before LLM work begins, so it remains visible after refresh.
- While a Director response is active, the Send button is replaced in place by a red Cancel button.
- On refresh, Director Studio detects the active project chat session and shows `LLM busy — response is still running`.
- The textarea and image upload control are disabled while the session is active.
- Cancel stops the active backend task and immediately returns the composer to its idle state.
- A browser refresh or dropped SSE connection does not cancel the backend task.
- The page polls the session while it is active. When it completes, the page reloads durable chat history and displays the final assistant response.
- Partial assistant tokens are transient. They are not restored after refresh and are not added to later LLM context.

## Backend design

Maintain an in-memory active chat-session registry keyed by project ID. Each entry owns a generated session ID, the runner task, its start time, and a cancellation state. Only one active Director chat session may exist for a project.

The streaming endpoint registers the session before starting the runner and removes it in the runner's terminal cleanup. Closing the SSE iterator leaves the runner alive. Explicit cancellation cancels the registered runner task, cleans up uncommitted image uploads, removes the session, and does not persist an assistant reply.

Persist the user message immediately after availability checks and before the runner starts. Successful completion appends only the assistant message. Failures and cancellation leave the already-submitted user message in history without fabricating an assistant response.

Add two project-scoped endpoints:

- `GET /api/projects/{project_id}/chat/session` returns whether a session is active, plus its ID and start time when active.
- `POST /api/projects/{project_id}/chat/session/cancel` cancels the active session. It is idempotent when no session is active.

The existing non-streaming chat endpoint keeps its current request/response contract but adopts the same immediate user-message persistence rule without registering a cancellable streaming session.

## Frontend design

`chatWithDirectorStream` accepts an `AbortSignal` so explicit cancellation can stop local stream reading immediately. A new API pair reads session status and requests server-side cancellation.

`DirectorPage` separates active-chat state from unrelated `busy` state. On initial project load it reads project data, durable chat history, and chat-session status together. While a session is active, it shows the existing working panel with the LLM-busy message, disables composer inputs, and renders Cancel in the Send button slot.

For a locally started request, Cancel first calls the server cancellation endpoint and then aborts the local stream. `AbortError` is treated as an expected cancellation, not as a chat error. For a session discovered after refresh, the same button calls the server endpoint even though there is no local stream controller.

While active, the page polls session status. A transition from active to idle triggers a durable-history reload. This handles both completion and cancellation without reconstructing partial tokens.

## Concurrency and failure handling

- A second chat submission for the same project while a session is active is rejected with a conflict response.
- Cancelling an already-finished or absent session succeeds as a no-op.
- Cancelling cannot undo Director tools that completed before cancellation.
- If status polling fails, the composer remains conservatively locked and displays the polling error; a later successful poll restores the authoritative state.
- Session state is process-local. A backend restart clears the active registry and terminates its tasks; durable user messages and completed assistant messages remain on disk.

## Tests

Backend tests cover immediate user persistence, session status while running, explicit task cancellation, disconnect without cancellation, duplicate-session rejection, cleanup after completion, and idempotent cancellation.

Frontend API tests cover forwarding `AbortSignal`, status retrieval, and cancellation requests. `DirectorPage` tests cover Send-to-Cancel replacement, disabled composer controls, cancellation without an error bubble, refreshed busy state, and automatic history reload after the session becomes idle.

