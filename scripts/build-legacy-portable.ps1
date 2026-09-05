param(
    [int]$VerificationPort = 18790
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendRoot = Join-Path $repoRoot "frontend"
$backendRoot = Join-Path $repoRoot "backend"
$buildRoot = Join-Path $repoRoot "build"
$pyinstallerRoot = Join-Path $buildRoot "pyinstaller"
$pyinstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$distRoot = Join-Path $repoRoot "dist"
$packageName = "Director-Studio-Legacy-Windows-x64"
$packageRoot = Join-Path $distRoot $packageName
$zipPath = Join-Path $distRoot "$packageName.zip"

function Remove-GeneratedDirectory([string]$Path) {
    $resolvedParent = [System.IO.Path]::GetFullPath((Split-Path $Path -Parent))
    if (-not $resolvedParent.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a directory outside the repository: $Path"
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

Push-Location $frontendRoot
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm test -- --run
    if ($LASTEXITCODE -ne 0) { throw "frontend tests failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
}
finally {
    Pop-Location
}

Push-Location $backendRoot
try {
    py -m pytest `
        tests/test_packaged_runtime.py `
        tests/test_projects_api.py `
        tests/test_portable_tools_installer.py `
        tests/test_director_model_runtime.py `
        tests/test_llm_provider.py `
        -q
    if ($LASTEXITCODE -ne 0) { throw "backend packaging tests failed" }
    py -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is unavailable; install backend/requirements-build.txt"
    }
}
finally {
    Pop-Location
}

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-portable-process-cleanup.ps1")
if ($LASTEXITCODE -ne 0) { throw "portable process cleanup test failed" }

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-portable-verifier-slow-health.ps1")
if ($LASTEXITCODE -ne 0) { throw "portable verifier slow-health test failed" }

Remove-GeneratedDirectory $buildRoot
Remove-GeneratedDirectory $distRoot
New-Item -ItemType Directory -Path $pyinstallerRoot -Force | Out-Null
New-Item -ItemType Directory -Path $pyinstallerDist -Force | Out-Null
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null

$specPath = Join-Path $backendRoot "packaging/director-studio-legacy.spec"
py -m PyInstaller `
    --clean `
    --noconfirm `
    --workpath $pyinstallerRoot `
    --distpath $pyinstallerDist `
    $specPath
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$builtExe = Join-Path $pyinstallerDist "DirectorStudio.exe"
if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw "PyInstaller did not produce DirectorStudio.exe"
}

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-packaged-workflow-assets.ps1") `
    -ExecutablePath $builtExe
if ($LASTEXITCODE -ne 0) { throw "packaged workflow asset test failed" }
pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-packaged-director-guides.ps1") `
    -ExecutablePath $builtExe
if ($LASTEXITCODE -ne 0) { throw "packaged Director guide test failed" }

Copy-Item -LiteralPath $builtExe -Destination (Join-Path $packageRoot "DirectorStudio.exe")
Copy-Item -LiteralPath (Join-Path $repoRoot "Install-Tools.cmd") -Destination (Join-Path $packageRoot "Install-Tools.cmd")
Copy-Item -LiteralPath (Join-Path $repoRoot "Install-Tools.py") -Destination (Join-Path $packageRoot "Install-Tools.py")
Copy-Item -LiteralPath (Join-Path $repoRoot "portable-tools-requirements.txt") -Destination (Join-Path $packageRoot "portable-tools-requirements.txt")
Copy-Item -LiteralPath (Join-Path $backendRoot ".env.example") -Destination (Join-Path $packageRoot ".env")
Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination (Join-Path $packageRoot "README.md")

pwsh -NoProfile -File (Join-Path $PSScriptRoot "verify-legacy-portable.ps1") `
    -PackageRoot $packageRoot `
    -Port $VerificationPort
if ($LASTEXITCODE -ne 0) { throw "Portable package verification failed" }

tar.exe -a -c -f $zipPath -C $distRoot $packageName
if ($LASTEXITCODE -ne 0) { throw "Portable zip creation failed" }

$archiveEntries = @(tar.exe -tf $zipPath)
if ($LASTEXITCODE -ne 0) { throw "Portable zip listing failed" }
$requiredArchiveEntries = @(
    "$packageName/DirectorStudio.exe",
    "$packageName/Install-Tools.cmd",
    "$packageName/Install-Tools.py",
    "$packageName/portable-tools-requirements.txt",
    "$packageName/.env",
    "$packageName/README.md"
)
foreach ($requiredEntry in $requiredArchiveEntries) {
    if ($archiveEntries -notcontains $requiredEntry) {
        throw "Portable zip is missing required entry: $requiredEntry"
    }
}

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-packaged-h3-profiles.ps1") `
    -ExecutablePath $builtExe `
    -ZipPath $zipPath
if ($LASTEXITCODE -ne 0) { throw "packaged H3 profile isolation test failed" }

$hash = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
$size = (Get-Item -LiteralPath $zipPath).Length

[ordered]@{
    package_root = $packageRoot
    zip = $zipPath
    sha256 = $hash.Hash
    bytes = $size
} | ConvertTo-Json
