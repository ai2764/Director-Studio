# Windows Comfy MCP Portable Bundling Design

## Goal

Make the Windows x64 portable archive capable of running every local ComfyUI
pipeline without a system Python installation and without running
`Install-Tools.cmd`. ComfyUI itself remains an external service.

## Scope

This change extends the existing Windows Harness portable package. It does not
change workflows, job semantics, H3 provider selection, ComfyUI installation,
or Linux packaging. `Install-Tools.cmd` remains available as an explicit repair
or override path, but is not required by a fresh portable archive.

The first result is a local technical artifact. Do not publish a binary that
redistributes the bundled tools until the project owner has accepted the
applicable license obligations: `comfy-mcp==0.10.0` declares
`AGPL-3.0-or-later OR LicenseRef-Comfy-Commercial`, and `comfy-cli==1.20.0`
declares `GPL-3.0-only`.

## Selected architecture

Stage the official Windows x64 embeddable CPython runtime under
`runtime/python`. Install an exact, hashed Windows dependency lock into
`runtime/python/Lib/site-packages`, enable that directory in the embeddable
runtime's `python313._pth`, and generate a relocatable `comfy.exe` console
launcher in the same directory.

The launcher uses a relative `python.exe` shebang. `comfy-mcp` resolves
`COMFY_BIN` and prepends its directory to the child `PATH`, so the launcher
finds the adjacent private interpreter after the package is moved. The backend
does not need a second MCP executable: it starts the private interpreter with
`-m comfy_mcp.server` over the existing stdio transport.

Alternatives rejected:

- Copying a venv: Windows launchers and `pyvenv.cfg` retain build-machine paths.
- Two PyInstaller one-file tools: relocatable but duplicates runtime data and
  adds extraction overhead to every `comfy-cli` child call.
- Bypassing MCP with direct Comfy HTTP: changes the approved execution boundary
  and loses MCP validation/download behavior.

## Runtime layout

```text
Director-Studio-Windows-x64/
  DirectorStudio.exe
  runtime/
    node/...
    python/
      python.exe
      python313.dll
      python313.zip
      python313._pth
      comfy.exe
      Lib/site-packages/...
  harness/...
  THIRD_PARTY_LICENSES/
    python.txt
    comfy-mcp.txt
    comfy-cli.txt
  portable-manifest.json
```

The exact CPython archive URL and SHA-256 live in a checked-in runtime manifest.
The dependency lock records exact versions and hashes for every redistributed
wheel. Staging rejects source distributions, wrong-platform native wheels,
unsafe ZIP paths, checksum drift, missing license records, empty package roots,
and build/test caches.

## Configuration and startup

Before importing the Pydantic settings singleton, the packaged entry point
resolves the private runtime and supplies defaults only when the user has not
explicitly configured the corresponding setting:

```text
DS_COMFY_MCP_COMMAND=<install>/runtime/python/python.exe
DS_COMFY_MCP_ARGS=-m comfy_mcp.server
DS_COMFY_MCP_COMFY_BIN=<install>/runtime/python/comfy.exe
```

An explicit process environment value or uncommented portable `.env` value
wins. Missing or incomplete bundled files fail closed when a local pipeline
first opens MCP, with the resolved path in the error. There is no silent switch
to a system Python or globally installed command.

The MCP server remains demand-started and owned by the existing persistent
`ComfyMcpClient`; users never start it manually. ComfyUI remains independently
managed at `DS_COMFY_BASE_URL`.

## Verification

Unit tests cover manifest parsing, checksum and safe extraction, `_pth`
configuration, exact package/license requirements, relocatable default
selection, and explicit override precedence.

The staged-runtime smoke test runs with a sanitized environment whose `PATH`
does not contain Python, `comfy`, or `comfy-mcp`. It must:

1. import `comfy_mcp` and `comfy_cli` with the private interpreter;
2. execute the private `comfy.exe --version` launcher;
3. start `python.exe -m comfy_mcp.server` via the production MCP client;
4. complete MCP initialization and list the allowlisted workflow tools;
5. terminate the child cleanly without leaving Python processes.

The Windows package verifier checks these files inside both the staged folder
and ZIP, rejects dev/test packages and credentials, and preserves the existing
official-H3-only checks. The full build then runs the existing packaged backend,
Harness, frontend, cleanup, and archive verifications.

## Documentation

README and `.env.example` state that Windows portable includes the private MCP
tool runtime. `Install-Tools.cmd` is documented only as an override/repair path.
The archive reports its Python, `comfy-mcp`, and `comfy-cli` versions and
licenses in its deterministic manifest.
