param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,
    [int]$Port = 18790,
    [int]$StartupTimeoutSec = 300,
    [switch]$SkipOfflineHarnessCheck,
    [switch]$SkipOfflineComfyCheck
)

$ErrorActionPreference = "Stop"
$processHelpers = Join-Path $PSScriptRoot "portable-processes.ps1"
. $processHelpers
$packagePath = [System.IO.Path]::GetFullPath($PackageRoot)

if (-not (Test-Path -LiteralPath $packagePath -PathType Container)) {
    throw "Package directory does not exist: $packagePath"
}

$required = @(
    "DirectorStudio.exe",
    ".env",
    "README.md",
    "runtime/node/node.exe",
    "runtime/node/LICENSE",
    "harness/dist/server.js",
    "harness/package.json",
    "harness/THIRD_PARTY_LICENSES.json",
    "harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node",
    "portable-manifest.json",
    "runtime/python/python.exe",
    "runtime/python/python3.dll",
    "runtime/python/python313.dll",
    "runtime/python/python313.zip",
    "runtime/python/python313._pth",
    "runtime/python/comfy.exe",
    "runtime/python/Lib/site-packages/pip/__init__.py",
    "runtime/python/Lib/site-packages/pip-25.1.1.dist-info/METADATA",
    "runtime/python/Lib/site-packages/pip-25.1.1.dist-info/licenses/LICENSE.txt",
    "runtime/python/Lib/site-packages/sitecustomize.py",
    "runtime/comfy-bootstrap.json",
    "runtime/comfy-requirements.lock",
    "THIRD_PARTY_LICENSES/python.txt",
    "THIRD_PARTY_LICENSES/pip.txt"
)
foreach ($relativePath in $required) {
    $candidate = Join-Path $packagePath $relativePath
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "Package is missing required path: $relativePath"
    }
}

$envPath = Join-Path $packagePath ".env"
$envLines = @(Get-Content -LiteralPath $envPath)
if ($envLines -match "DS_GPT_BRIDGE_") {
    throw "Package contains GPT Bridge configuration in .env"
}
$secretKeyPattern = "(?i)(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)"
foreach ($line in $envLines) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    if ($trimmed -notmatch "^(?:export\s+)?(?<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?<value>.*)$") {
        continue
    }
    $key = $Matches["key"]
    $value = $Matches["value"].Trim().Trim('"').Trim("'")
    if ($key -match $secretKeyPattern -and $value) {
        throw "Package contains an active secret in .env: $key"
    }
}

$forbiddenNames = @(
    ".env.example",
    "credentials.json",
    "secrets.json",
    "token.json",
    "id_rsa",
    "id_ed25519"
)
foreach ($item in Get-ChildItem -LiteralPath $packagePath -Recurse -Force) {
    $relative = [System.IO.Path]::GetRelativePath($packagePath, $item.FullName)
    $segments = $relative -split '[\\/]'
    $normalizedRelative = $relative.Replace('\', '/')
    $isHarnessDependency = $normalizedRelative.StartsWith(
        "harness/node_modules/",
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $isPythonDependency = $normalizedRelative.StartsWith(
        "runtime/python/Lib/site-packages/",
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $isPublicCaBundle = $isPythonDependency -and $normalizedRelative.EndsWith(
        "/certifi/cacert.pem",
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if (
        -not $isHarnessDependency -and
        -not ($isPythonDependency -and $segments -contains "data") -and (
            $segments -contains "data" -or
            $segments -contains "workflow_profiles" -or
            $segments -contains "projects" -or
            $segments -contains "jobs" -or
            $segments -contains "outputs" -or
            $segments -contains "tests"
        )
    ) {
        throw "Package contains persisted user state: $relative"
    }
    if (-not $isHarnessDependency -and $forbiddenNames -contains $item.Name.ToLowerInvariant()) {
        throw "Package contains a forbidden secret file: $relative"
    }
    if (
        -not $isHarnessDependency -and
        -not $isPublicCaBundle -and
        $item.Extension -in @(".pem", ".key", ".p12", ".pfx")
    ) {
        throw "Package contains a credential-like file: $relative"
    }
}

py (Join-Path $PSScriptRoot "verify_portable_contents.py") `
    --platform windows `
    --package-root $packagePath
if ($LASTEXITCODE -ne 0) {
    throw "Portable package content policy verification failed"
}

if (-not $SkipOfflineHarnessCheck) {
    $sidecarListener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $sidecarListener.Start()
    $sidecarVerificationPort = ([System.Net.IPEndPoint]$sidecarListener.LocalEndpoint).Port
    $sidecarListener.Stop()
    py (Join-Path $PSScriptRoot "verify_bundled_harness.py") `
        --package-root $packagePath `
        --port $sidecarVerificationPort
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled Harness offline verification failed"
    }
}

if (-not $SkipOfflineComfyCheck) {
    py (Join-Path $PSScriptRoot "verify_bundled_comfy.py") `
        --package-root $packagePath
    if ($LASTEXITCODE -ne 0) {
        throw "Comfy first-launch bootstrap verification failed"
    }
}

$verificationRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "director-studio-package-verification-" + [Guid]::NewGuid().ToString("N")
)
$runtimePackagePath = Join-Path $verificationRoot "package"
$exePath = Join-Path $runtimePackagePath "DirectorStudio.exe"
$previousPort = $env:DS_PORT
$previousHost = $env:DS_HOST
$previousRuntime = $env:DS_DIRECTOR_AGENT_RUNTIME
$previousManaged = $env:DS_HARNESS_MANAGED
$previousComfyCommand = $env:DS_COMFY_MCP_COMMAND
$previousComfyArgs = $env:DS_COMFY_MCP_ARGS
$previousComfyBin = $env:DS_COMFY_MCP_COMFY_BIN
$previousPath = $env:PATH
$process = $null
try {
    New-Item -ItemType Directory -Path $verificationRoot | Out-Null
    Copy-Item -LiteralPath $packagePath -Destination $runtimePackagePath -Recurse

    $env:DS_PORT = [string]$Port
    $env:DS_HOST = "127.0.0.1"
    $env:DS_DIRECTOR_AGENT_RUNTIME = "harness"
    $env:DS_HARNESS_MANAGED = "true"
    Remove-Item Env:DS_COMFY_MCP_COMMAND -ErrorAction SilentlyContinue
    Remove-Item Env:DS_COMFY_MCP_ARGS -ErrorAction SilentlyContinue
    Remove-Item Env:DS_COMFY_MCP_COMFY_BIN -ErrorAction SilentlyContinue
    $env:PATH = (($previousPath -split ';') | Where-Object {
        $_ -and $_ -notmatch '(?i)(?:^|[\\/])nodejs(?:[\\/]|$)'
    }) -join ';'
    $process = Start-Process `
        -FilePath $exePath `
        -WorkingDirectory $runtimePackagePath `
        -WindowStyle Hidden `
        -PassThru

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSec)
    $health = $null
    do {
        if ($process.HasExited) {
            throw "DirectorStudio.exe exited before becoming healthy (exit $($process.ExitCode))"
        }
        try {
            $health = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$Port/api/health" `
                -TimeoutSec 5
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    } while ($null -eq $health -and [DateTimeOffset]::UtcNow -lt $deadline)

    if ($null -eq $health) {
        throw "Director Studio did not become healthy within $StartupTimeoutSec seconds"
    }

    $runtime = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$Port/api/director/runtime?check_sidecar=true" `
        -TimeoutSec 5
    if ($runtime.runtime -ne "harness" -or $runtime.sidecar_ready -ne $true) {
        throw "Packaged backend did not authenticate its managed Harness sidecar"
    }

    if (-not $SkipOfflineComfyCheck) {
        $privateManifest = Join-Path $runtimePackagePath "data/tools/comfy/manifest.json"
        if (-not (Test-Path -LiteralPath $privateManifest -PathType Leaf)) {
            throw "Packaged first launch did not create the private Comfy tool manifest"
        }
        $privatePython = Join-Path $runtimePackagePath "runtime/python/python.exe"
        & $privatePython -c "import comfy_mcp.server, comfy_cli, pywintypes"
        if ($LASTEXITCODE -ne 0) {
            throw "Packaged private Python cannot import the configured Comfy MCP runtime"
        }
    }

    $frontend = Invoke-WebRequest `
        -Uri "http://127.0.0.1:$Port/" `
        -TimeoutSec 5 `
        -UseBasicParsing
    if ($frontend.StatusCode -ne 200 -or $frontend.Content -notmatch '<div id="root">') {
        throw "Packaged frontend entry page is unavailable"
    }
    [ordered]@{
        ok = $true
        package_root = $packagePath
        port = $Port
        health = $health
        director_runtime = $runtime
        frontend_status = $frontend.StatusCode
    } | ConvertTo-Json -Depth 6
}
finally {
    try {
        if ($null -ne $process) {
            Stop-ProcessesByExecutablePath -ExecutablePath $exePath
        }
        $nodePath = Join-Path $runtimePackagePath "runtime/node/node.exe"
        $nodeDeadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
        do {
            $remainingNode = Get-CimInstance Win32_Process | Where-Object {
                $_.ExecutablePath -and
                [System.IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                    [System.IO.Path]::GetFullPath($nodePath),
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            }
            if ($remainingNode) { Start-Sleep -Milliseconds 100 }
        } while ($remainingNode -and [DateTimeOffset]::UtcNow -lt $nodeDeadline)
        if ($remainingNode) {
            Stop-ProcessesByExecutablePath -ExecutablePath $nodePath
            throw "Packaged Harness sidecar remained after Director Studio exited"
        }
    }
    finally {
        $env:DS_PORT = $previousPort
        $env:DS_HOST = $previousHost
        $env:DS_DIRECTOR_AGENT_RUNTIME = $previousRuntime
        $env:DS_HARNESS_MANAGED = $previousManaged
        $env:DS_COMFY_MCP_COMMAND = $previousComfyCommand
        $env:DS_COMFY_MCP_ARGS = $previousComfyArgs
        $env:DS_COMFY_MCP_COMFY_BIN = $previousComfyBin
        $env:PATH = $previousPath
        if (Test-Path -LiteralPath $verificationRoot) {
            $resolvedVerificationRoot = [System.IO.Path]::GetFullPath($verificationRoot)
            $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
            if (-not $resolvedVerificationRoot.StartsWith(
                $resolvedTemp,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Refusing to remove verification directory outside system temp: $resolvedVerificationRoot"
            }
            Remove-Item -LiteralPath $resolvedVerificationRoot -Recurse -Force
        }
    }
}
