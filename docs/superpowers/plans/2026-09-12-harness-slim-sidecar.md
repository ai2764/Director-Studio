# Slim Harness sidecar implementation

## Approved design and constraints

Start from origin/main 267c550. Python owns all durable data, context facts,
tools, provider selection, model lifecycle and VRAM. Harness owns only the
ephemeral model/tool loop, context compaction and bounded turn recovery.
Legacy remains the default. No old-branch merge or wholesale copy. No UI rewrite.
No durable Harness log, workflow interpreter, receipts platform or judge model.

## Transport contract

Python POSTs /turns/{uuid} with Bearer token and reads NDJSON. Node only binds
127.0.0.1. It never calls backend/provider URLs. Each request event contains
type=request, id, method (context|llm|tool), params. Python POSTs
/turns/{uuid}/responses/{id} with {ok:true,data} or
{ok:false,error:{code,message,retryable}}. Python sends DELETE /turns/{uuid}
on cancellation. Disconnect aborts the ephemeral sidecar turn. No reconnect or
automatic mutation replay. One outstanding request per turn; sequential tools.

Turn body: {message, history:[{role,content}], context_window, max_steps}.
context returns {system, state, tools:[OpenAI function schemas]}.
llm params: {messages:[OpenAI-shaped text/tool messages], purpose:'turn'|'compaction'}.
Python determines model, tools and adds current authoritative system/context.
llm returns {content,thinking,tool_calls,finish_reason} using Python LLMResult.
tool params: {name,arguments,call_id}; result is existing Python tool output.
Node emits status events and terminal {type:'result',reply,thinking} or
{type:'error',code,message}. Never accept domain objects from the sidecar.

## Tasks

1. Sidecar: pinned real Harness packages, ephemeral sessions, transport,
   cancellation/deadline cleanup, finite steps, retry only transient model errors,
   compaction through backend inference. Unit tests plus real-kernel fake-host test.
2. Python: runtime switch, NDJSON driver, backend-owned turn context and tool
   dispatch reusing existing implementations. Enforce offered tools each call,
   duplicate call identity rejection, no retries of side effects, current project
   reload. Existing history/upload/session APIs remain Python-owned.
3. Launcher: select runtime from flag/environment/config; start authenticated
   sidecar automatically for Harness, health identity, loopback binding, stop
   owned children. Legacy and frontend-only launch do not require sidecar.
4. Verification: regression tests for legacy, focused transport/tool tests,
   real Python-to-Node fixture proving mutation and argument recovery, cancellation,
   forced compaction. Record scope and live-model limitations in report.

## Acceptance

Same existing model/provider/VRAM path and tool handlers as legacy. Sidecar
unavailability fails explicitly, never falls back. Cancel or disconnect cleans
ephemeral state. A lost tool result stops the turn without replay. Python returns
fresh project data. Compaction cannot overwrite project state. Installed Harness
is exercised, not a handwritten replacement loop. No real generation jobs or user
data are touched by tests. A/B starts with independent fixture projects.
