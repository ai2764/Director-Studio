# Linux Portable Verifier Review Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Task 4 Linux runtime verification for exact page status checks, bounded child output capture, and process reaping during process-group races.

**Architecture:** Extend the existing focused verifier tests with one real noisy-child regression, exact-status regressions for each required page, and a deterministic cleanup-race unit test. Replace child pipes with temporary log files, read only trailing diagnostic bytes, and make POSIX cleanup fall through to unconditional child reaping.

**Tech Stack:** Python 3, pytest, subprocess, tempfile, urllib.request.

**Spec:** `.superpowers/sdd/2026-09-06-linux-portable/task-4-fix-1.md`

## Global Constraints

- Work only in `C:\Users\AIBOX\dev\Director-Studio\.worktrees\linux-portable`.
- Do not touch Task 5 or later.
- Preserve source-package immutability and owned-process-group-only descendant cleanup.
- Preserve Windows-safe module import and the 250 ms health polling interval.
- Do not push.
- Commit tests separately as `test cover Linux verifier review gaps`.
- Commit production changes as `fix harden Linux runtime verification`.
- Report Windows skips explicitly; do not claim Linux execution.

### Task 1: Add failing review-gap tests

**Files:**
- Modify: `backend/tests/test_linux_portable_verifier.py`

- [ ] Add parameterized page tests that make each of `/`, `/mobile`, and `/docs` return 204 and require `verify_runtime` to raise.
- [ ] Add a noisy early-exit child that emits output beyond pipe capacity and assert trailing stdout/stderr diagnostics remain available.
- [ ] Add a deterministic cleanup unit test where `killpg` raises `ProcessLookupError` and assert the Popen-like child is waited.
- [ ] Run `cd backend; py -3 -m pytest tests/test_linux_portable_verifier.py -q`; record the expected RED output and skipped POSIX integration tests.
- [ ] Commit the test-only changes with `git commit -m "test cover Linux verifier review gaps"`.

### Task 2: Implement minimal production fixes

**Files:**
- Modify: `scripts/verify_linux_portable.py`

- [ ] Capture stdout and stderr with temporary files in the existing temporary verification directory.
- [ ] Read bounded trailing output from those files for early-exit diagnostics.
- [ ] Require status 200 for all three page endpoints.
- [ ] Ensure POSIX cleanup always reaches `process.wait` after a disappearing process group.
- [ ] Preserve the 250 ms health polling interval and existing process-group ownership boundaries.

### Task 3: Verify and report

**Files:**
- Modify: `.superpowers/sdd/2026-09-06-linux-portable/task-4-fix-1-report.md`

- [ ] Run focused tests, `git diff --check`, and a Windows import-safety check.
- [ ] Record exact commands, outputs, commit IDs, skips, limitations, and self-review.
- [ ] Commit production changes with `git commit -m "fix harden Linux runtime verification"`.
- [ ] Stop Convergence Guard before the final report.

