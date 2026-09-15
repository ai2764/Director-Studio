# Windows Harness Portable Bundling Design

Date: 2026-09-14

## Summary

Director Studio's Windows x64 portable release will include everything required to run the DeepSeek Harness sidecar. A user will extract one ZIP and launch `DirectorStudio.exe`; they will not need to install Node.js, npm, `tsx`, or Harness dependencies.

The release remains a multi-file portable package with a single user-facing entry point. It will carry a private Node.js runtime, precompiled Harness JavaScript, production-only npm dependencies, and the Windows x64 Koffi native module. `DirectorStudio.exe` will own the sidecar lifecycle and will start the web application only after the sidecar passes an authenticated capability health check.

## Goals

- Make Harness the functional default in the Windows x64 portable release.
- Require no system Node.js, npm, or dependency installation.
- Preserve the existing package-relative behavior of DeepSeek Harness packages and Koffi.
- Start and stop the bundled sidecar automatically with `DirectorStudio.exe`.
- Fail visibly and diagnostically when Harness cannot start instead of silently switching to Legacy.
- Retain explicit Legacy mode and externally managed Harness support for development and recovery.
- Produce a reproducible package whose Node and npm dependency versions can be audited.

## Non-goals

- A single-file Windows executable.
- Linux or macOS Harness portable bundling in this iteration.
- Replacing or rewriting DeepSeek Harness dependencies.
- Installing or updating Node packages on the user's machine at runtime.
- Automatically falling back to Legacy when bundled Harness startup fails.

## Selected Packaging Approach

The portable ZIP will use this logical layout:

```text
DirectorStudio-Portable/
|-- DirectorStudio.exe
|-- runtime/
|   `-- node/
|       |-- node.exe
|       `-- LICENSE
|-- harness/
|   |-- dist/
|   |-- node_modules/
|   |-- package.json
|   `-- THIRD_PARTY_LICENSES
|-- workflows/
|-- .env
|-- README.md
`-- data/
    |-- harness-sessions/
    `-- logs/
```

The exact placement of assets already embedded by PyInstaller may remain unchanged. The important contract is that the private Node runtime and Harness runtime are external sibling resources that the frozen Python entry point can resolve relative to `DirectorStudio.exe`.

### Why not a single JavaScript bundle

A preliminary esbuild probe showed that DeepSeek Harness dependencies resolve package metadata relative to `import.meta.url`. Koffi also loads a platform-specific `.node` binary through its package layout. Flattening everything into one JavaScript file breaks those assumptions. The selected design compiles Director Studio's first-party TypeScript while retaining production dependencies in their normal package directories.

### Why not a single Windows executable

Embedding Node and the Koffi native binary inside the PyInstaller executable would require extraction and native-library path management on every installation or first launch. It would increase startup complexity, antivirus false-positive risk, and failure modes without changing the user workflow. The multi-file ZIP still exposes only `DirectorStudio.exe` as the application entry point.

## Build Design

The Windows portable build will add a Harness staging phase before the existing package assembly:

1. Install the exact dependency graph from `harness/package-lock.json` in a clean build workspace.
2. Compile `harness/src` to ordinary Node.js-compatible JavaScript in `harness/dist`.
3. Create a clean runtime staging directory and install production dependencies from the same lockfile with development dependencies omitted.
4. Copy `dist`, the runtime `package.json`, production `node_modules`, license material, and the Windows x64 Koffi binary into the portable package.
5. Obtain a pinned official Windows x64 Node.js archive from a build cache or the official distribution endpoint and verify its configured SHA-256 before staging the required runtime files.
6. Record the Node version, Harness package version or source revision, lockfile digest, and target platform in a portable manifest.

Node binaries will not be committed to Git. Release builds must not use an arbitrary Node installation found on `PATH` as the packaged runtime.

The build must fail if the selected Node release, checksum, target architecture, production dependency installation, compilation, or required native module is missing. Unsupported platforms must produce a clear build error rather than a package that starts without Harness.

## Runtime Lifecycle

When the configured Director runtime is `harness`, the frozen Windows entry point will:

1. Resolve the private Node executable and compiled Harness entry module relative to the portable application directory.
2. Create the persistent session and log directories if they do not exist.
3. Select an available loopback port and generate a cryptographically random per-launch internal token.
4. Start the private Node executable directly, without a shell, PowerShell, npm, or `tsx`.
5. Pass a small allowlisted environment containing the loopback binding, selected port, internal token, session root, and required provider configuration.
6. Capture sidecar output in `data/logs/harness-sidecar.log` with bounded retention.
7. Wait for the authenticated health endpoint to report the expected Harness identity and capabilities.
8. Inject the confirmed sidecar URL and token into the backend process environment, then start Uvicorn and the normal Director Studio application.
9. On normal exit, interrupt, or backend startup failure, terminate the owned sidecar and wait for it to exit. If graceful shutdown times out, terminate only the exact child process created by this application instance.

Port allocation must tolerate the default port already being occupied. Ownership will be tracked by process handle and launch token, never by killing every process with a matching executable name or port.

## Configuration Rules

- The portable `.env` defaults to `DS_DIRECTOR_RUNTIME=harness`.
- If the user explicitly selects `legacy`, the bundled sidecar is not started.
- If the user explicitly selects an externally managed Harness endpoint, the application connects to it and does not start the bundled sidecar.
- A dedicated managed-sidecar setting may be introduced if endpoint presence alone cannot distinguish a default from an explicit external configuration.
- Internal token and dynamically selected port values are process-local launch state and are not written back to `.env`.

The source checkout launcher and portable entry point should share lifecycle semantics where practical, but the portable path must not depend on a PowerShell script.

## Failure Behavior

Harness is part of the default execution contract, so startup failures are terminal and visible. The application must not silently fall back to Legacy.

Errors presented to the user should identify the failed stage and point to the sidecar log. At minimum, distinct diagnostics are required for:

- bundled runtime or entry module missing;
- target architecture or native Koffi module mismatch;
- sidecar process exiting before readiness;
- readiness timeout;
- authenticated health identity or capability mismatch;
- session directory not writable; and
- external Harness endpoint unavailable when external mode was explicitly configured.

A failed launch must clean up the sidecar child it created. Existing user session data must not be removed as part of recovery.

## Security

- Bind the sidecar only to loopback.
- Generate a new high-entropy internal token for every managed launch.
- Spawn the fixed packaged executable and module with argument arrays; do not invoke a shell.
- Avoid passing unrelated parent-process secrets into the child environment.
- Verify the pinned Node archive during build and record packaged component hashes in the manifest.
- Keep the Koffi native binary limited to the declared `win-x64` target.
- Never run npm or install packages at application startup.

## Persistence and Upgrade Compatibility

Harness JSONL session data and logs live under the portable `data` directory, separate from versioned runtime files. Replacing application, Node, or Harness runtime files must not overwrite `data`.

The portable manifest enables support reports to identify the exact runtime combination. If a future Harness version requires an incompatible persistence migration, that migration must be explicit, backed up, and version-gated; this first iteration does not invent a new persistence format.

## Verification Strategy

### Build and structure checks

- Verify all existing portable required files.
- Verify private `node.exe`, its license, compiled Harness entry point, runtime package metadata, and portable manifest.
- Verify the Windows x64 Koffi `.node` binary is present.
- Verify development-only execution dependencies such as `tsx` and TypeScript are absent from the staged runtime.
- Replace the old blanket prohibition on a `harness` directory with an allowlist of expected Harness runtime content.

### Isolated smoke test

Run the packaged application in an environment where system Node/npm are absent from `PATH` and network access is unavailable. Confirm that:

- the bundled sidecar reaches authenticated readiness;
- the backend reports Harness as the active runtime;
- a minimal deterministic agent turn completes through the Harness path; and
- the process uses the packaged `node.exe` rather than a machine installation.

The LLM-dependent turn may use a controlled test provider or fixture so portable validation does not depend on a public model service.

### Lifecycle tests

- Occupy the usual sidecar port and confirm launch selects another port.
- Exit the main application and confirm its sidecar child exits.
- Force the sidecar to exit during readiness and confirm a bounded, actionable failure.
- Supply an invalid capability response and confirm backend startup is refused.
- Select Legacy explicitly and confirm no Node sidecar starts.
- Select a test external endpoint and confirm no bundled sidecar starts.

## Rollout

The first supported target is `win-x64`. Documentation and package naming must identify that target. Linux portable remains on its existing behavior until a separate design covers its runtime distribution.

The change should be implemented in separable commits: build/staging support, managed lifecycle support, verifier and automated tests, and documentation. The existing source-mode Harness launcher remains useful for development and is not removed.

## Acceptance Criteria

- A clean Windows x64 machine can extract the ZIP and start Director Studio in Harness mode by running `DirectorStudio.exe`.
- No Node.js, npm, or Harness dependency installation is requested or performed.
- The application does not use a system Node installation even if one is available.
- The bundled Harness completes its authenticated readiness check before the backend accepts Director requests.
- Application shutdown does not leave its managed sidecar running.
- Harness startup failure is bounded, identifies the failed stage, and provides a log path.
- Legacy and external-Harness modes remain explicitly selectable.
- Portable verification covers package contents, native dependency presence, isolated startup, and lifecycle behavior.
