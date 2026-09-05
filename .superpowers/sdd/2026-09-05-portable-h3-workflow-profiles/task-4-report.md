# Task 4 report — H3 workflow profile HTTP API

## Outcome

- Added the runtime `/api/workflow-profiles/h3` setup surface for listing, multipart import, deterministic analysis, strict mapping updates, contract plus live-Comfy validation, activation, and selection.
- Added durable import-local mapping, validation, and successful-test evidence. Activation re-reads the import and requires contract-v1 validation plus a successful test job for the same workflow and mapping hashes before installing and atomically selecting a generated custom profile ID.
- Added `ComfyMcpClient.validate_workflow(graph)` and a shared temporary-workflow context manager. Submission still validates before queueing and both operations remove their temporary API JSON.
- Added stable top-level API errors shaped as `{code, message, details}` for storage, lifecycle, contract, dependency-validation, and changed-profile failures.

## TDD evidence

Initial focused run:

```text
py -m pytest tests/test_h3_workflow_profiles_api.py tests/test_comfy_mcp_client.py -q
2 failed, 9 passed, 7 errors
```

The API module/routes were absent and `ComfyMcpClient` had no `validate_workflow` method. After the first implementation, the focused set passed 18/18.

A self-review race test then changed an import while live Comfy validation was in progress. It failed as expected (`expected 409, got 200`). Validation evidence now records only when the current workflow and mapping hashes still match the graph actually sent to Comfy; the race test and focused suite then passed.

## Final verification

From `backend/`:

```text
py -m ruff format --check app/api/h3_workflow_profiles.py app/integrations/comfy_mcp.py app/workflow_profiles/h3/__init__.py app/workflow_profiles/h3/errors.py app/workflow_profiles/h3/store.py tests/test_h3_workflow_profiles_api.py tests/test_comfy_mcp_client.py
7 files already formatted

py -m ruff check --ignore SIM117,BLE001 app/api/h3_workflow_profiles.py app/integrations/comfy_mcp.py app/workflow_profiles/h3/__init__.py app/workflow_profiles/h3/errors.py app/workflow_profiles/h3/store.py tests/test_h3_workflow_profiles_api.py tests/test_comfy_mcp_client.py
All checks passed!

py -m pytest tests/test_h3_workflow_profiles_api.py tests/test_comfy_mcp_client.py -q
19 passed in 2.40s

py -m pytest -q
755 passed, 2 warnings in 32.46s
```

The two full-suite warnings are pre-existing Pillow `Image.getdata` deprecations in `tests/test_tail_frame_extraction.py`.

## Self-review

- Confirmed every post-import filesystem lookup is derived from the store root and a validated opaque import/profile ID; no route accepts a path field.
- Confirmed mapping request models reject extra fields and non-string node IDs.
- Confirmed mapping changes invalidate prior validation/test evidence, validation is bound across the live MCP call, and activation rechecks both workflow and mapping hashes.
- Confirmed profile files and installed validation metadata are written before `active.json`; the active pointer remains the final atomic state change.
- Confirmed generated profile IDs satisfy the existing normalized ID contract and include 192 bits of workflow/mapping identity.
- Confirmed Task 6 can supply real activation evidence through `record_test_success(import_id, workflow_sha256=..., job_id=...)`; this task intentionally does not expose the real test-job route.

## Limitations

- Comfy validation is covered through the MCP transport boundary with a faithful fake session; no live local ComfyUI instance or GPU generation was run.
- The real 56-frame test job and its evidence producer remain Task 6 scope. Until that producer lands, only trusted internal/test code can create successful-test evidence, so normal users cannot activate an imported profile yet.
