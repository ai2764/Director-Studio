param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,
    [int]$Port = 18790,
    [int]$StartupTimeoutSec = 45
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
    "Install-Tools.cmd",
    "Install-Tools.py",
    "portable-tools-requirements.txt",
    ".env",
    "README.md",
    "data"
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

$dataPath = Join-Path $packagePath "data"
if (Get-ChildItem -LiteralPath $dataPath -Force | Select-Object -First 1) {
    throw "Packaged data directory must be empty"
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
    if ($segments -contains "harness") {
        throw "Package contains Harness content: $relative"
    }
    if ($segments -contains "projects" -or $segments -contains "jobs") {
        throw "Package contains persisted user state: $relative"
    }
    if ($forbiddenNames -contains $item.Name.ToLowerInvariant()) {
        throw "Package contains a forbidden secret file: $relative"
    }
    if ($item.Extension -in @(".pem", ".key", ".p12", ".pfx")) {
        throw "Package contains a credential-like file: $relative"
    }
}

$verificationRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "director-studio-package-verification-" + [Guid]::NewGuid().ToString("N")
)
$runtimePackagePath = Join-Path $verificationRoot "package"
$exePath = Join-Path $runtimePackagePath "DirectorStudio.exe"
$previousPort = $env:DS_PORT
$previousHost = $env:DS_HOST
$process = $null
try {
    New-Item -ItemType Directory -Path $verificationRoot | Out-Null
    Copy-Item -LiteralPath $packagePath -Destination $runtimePackagePath -Recurse

    $env:DS_PORT = [string]$Port
    $env:DS_HOST = "127.0.0.1"
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

    $frontend = Invoke-WebRequest `
        -Uri "http://127.0.0.1:$Port/" `
        -TimeoutSec 5 `
        -UseBasicParsing
    if ($frontend.StatusCode -ne 200 -or $frontend.Content -notmatch '<div id="root">') {
        throw "Packaged frontend entry page is unavailable"
    }
    if (Get-ChildItem -LiteralPath $dataPath -Force | Select-Object -First 1) {
        throw "Package verification modified the release data directory"
    }

    [ordered]@{
        ok = $true
        package_root = $packagePath
        port = $Port
        health = $health
        frontend_status = $frontend.StatusCode
    } | ConvertTo-Json -Depth 6
}
finally {
    try {
        if ($null -ne $process) {
            Stop-ProcessesByExecutablePath -ExecutablePath $exePath
        }
    }
    finally {
        $env:DS_PORT = $previousPort
        $env:DS_HOST = $previousHost
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
